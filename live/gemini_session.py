"""Gemini Live transport and natural-language Task Manager Agent routing."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types

from lumi_openui.live.registry import LiveToolRegistry
from lumi_openui.settings import Settings
from lumi_openui.task_manager import (
    TaskContractError,
    TaskEvent,
    TaskManagerError,
    request_task_manager_from_call,
)
from lumi_openui.task_manager_agent import (
    TaskManagerAgent,
    TaskManagerAgentError,
    TaskManagerAgentRequest,
)
from lumi_openui.trace import begin_turn, trace, warning

from .surface_runtime import SurfaceRuntime
from .guide_prompt import (
    panel_interaction_message,
    PRESENTATION_CONTEXT_GUIDANCE,
    surface_ready_message,
    surface_context_update_message,
    surface_context_message,
)
from .delete_surface import DELETE_SURFACE_TOOL
from .update_surface_state import UPDATE_SURFACE_STATE_TOOL
from .persistent_transport import PersistentLiveTransport
from .session_protocol import LiveSessionState
from .visual_presentation import PRESENT_VISUAL_TOOL


from lumi_openui.trace import log_token_usage


logger = logging.getLogger("lumi.gemini_live")
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
TaskRuntimeFactory = Callable[[str, Callable[[TaskEvent], Awaitable[None]], Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]], Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]]], Any]
# ``marker`` is emitted immediately before its PCM packet.  The browser uses
# that packet's AudioContext start time as the visual cue's clock.
AudioCallback = Callable[[bytes, int, dict[str, Any] | None, str], Awaitable[None]]
_RATE = re.compile(r"rate=(\d+)")
_TASK_EVENT_BATCH_DELAY_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class _QueuedTaskMessage:
    """One trusted task-progress message awaiting delivery to Gemini Live."""

    text: str
    run_id: str | None = None


def _ui_trace_timestamp() -> str:
    """Return the local server time in the format shown by the trace log."""

    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _surface_completion(value: object) -> dict[str, Any]:
    """Normalize the trusted completeness signal attached to a final panel."""

    if not isinstance(value, dict) or value.get("surface_status") != "partial":
        return {"surface_status": "ready", "missing_parts": []}
    raw_parts = value.get("missing_parts")
    parts = []
    if isinstance(raw_parts, list):
        for item in raw_parts:
            if not isinstance(item, dict):
                continue
            description, reason = item.get("description"), item.get("reason")
            if (
                isinstance(description, str) and description.strip()
                and isinstance(reason, str) and reason.strip()
            ):
                parts.append({
                    "description": description.strip(),
                    "reason": reason.strip(),
                })
    return {
        "surface_status": "partial" if parts else "ready",
        "missing_parts": parts,
    }


def _usage_counter(usage: object, *names: str) -> int | None:
    """Read provider-reported usage only; never derive token counts locally."""

    for name in names:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _live_usage_for_ui(usage: object) -> dict[str, int] | None:
    if usage is None:
        return None
    values = {
        "prompt_tokens": _usage_counter(usage, "prompt_token_count", "prompt_tokens"),
        "output_tokens": _usage_counter(
            usage, "response_token_count", "candidates_token_count", "completion_tokens"
        ),
        "reasoning_tokens": _usage_counter(usage, "thoughts_token_count"),
        "tool_input_tokens": _usage_counter(usage, "tool_use_prompt_token_count"),
        "total_tokens": _usage_counter(usage, "total_token_count", "total_tokens"),
    }
    return {key: value for key, value in values.items() if value is not None} or None


class GeminiLiveSessionError(RuntimeError):
    pass


def _sample_rate(mime_type: str | None) -> int:
    match = _RATE.search(mime_type or "")
    return int(match.group(1)) if match else 24_000


class GeminiLiveSession:
    """Transport boundary: registries and runtime own tools, templates and presentation state."""

    def __init__(
        self,
        *,
        settings: Settings,
        registry: LiveToolRegistry,
        surface_runtime: SurfaceRuntime,
        task_runtime_factory: TaskRuntimeFactory,
        task_manager_agent: TaskManagerAgent | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._surface_runtime = surface_runtime
        self._task_runtime_factory = task_runtime_factory
        self._task_manager_agent = task_manager_agent

    def _instruction(self, session_id: str) -> str:
        memory = self._surface_runtime.session_memory(session_id)
        history = [
            f"{item['role']}: {item['content'][:700]}"
            for item in memory.history[-6:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        sections = [
            self._registry.prompt_guidance(),
            PRESENTATION_CONTEXT_GUIDANCE,
        ]
        if history:
            sections.append("Recent conversation (context only):\n" + "\n".join(history))
        return "\n\n".join(section for section in sections if section)

    def _connection_config(self, session_id: str) -> types.LiveConnectConfig:
        """Build configuration once per Gemini connection, including safe memory."""

        declarations = [
            types.FunctionDeclaration(
                name=item["name"], description=item["description"], parameters_json_schema=item["parameters"]
            )
            for item in [
                *self._registry.tool_declarations(),
                UPDATE_SURFACE_STATE_TOOL,
                DELETE_SURFACE_TOOL,
                PRESENT_VISUAL_TOOL,
            ]
        ]
        logger.info(
            "[LIVE:TOOL_DECLARATIONS] session=%s tools=%s",
            session_id,
            json.dumps(
                [item.model_dump(mode="json", exclude_none=True) for item in declarations],
                ensure_ascii=False,
            ),
        )
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            tools=[types.Tool(function_declarations=declarations)],
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=350,
                    silence_duration_ms=650,
                ),
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
            #thinking_config=types.ThinkingConfig(thinking_level="low"),
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_hints=types.LanguageHints(language_codes=["vi-VN"])
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._settings.gemini_live_voice)
                ),
                language_code="vi-VN",
            ),
            system_instruction=self._instruction(session_id),
        )

    async def open_persistent_conversation(
        self,
        *,
        session_id: str,
        transport: PersistentLiveTransport,
        on_event: EventCallback,
        on_audio: AudioCallback,
    ) -> "PersistentGeminiLiveConversation":
        """Open or reuse one Gemini Live connection for a persistent browser session."""

        if not self._settings.gemini_live_api_key:
            raise GeminiLiveSessionError("GEMINI_LIVE_API_KEY chưa được cấu hình.")
        client = genai.Client(api_key=self._settings.gemini_live_api_key)
        if not transport.connected:
            await transport.connect(client.aio.live.connect(
                model=self._settings.gemini_live_model,
                config=self._connection_config(session_id),
            ))
        logger.info("[LIVE:PERSISTENT_CONNECTED] session=%s model=%s", session_id, self._settings.gemini_live_model)
        return PersistentGeminiLiveConversation(
            session_id=session_id,
            settings=self._settings,
            transport=transport,
            surface_runtime=self._surface_runtime,
            task_runtime_factory=self._task_runtime_factory,
            task_manager_agent=self._task_manager_agent,
            on_event=on_event,
            on_audio=on_audio,
        )

class PersistentGeminiLiveConversation:
    """Consume one persistent transport using verified fact-driven presentation."""

    def __init__(
        self,
        *,
        session_id: str,
        settings: Settings,
        transport: PersistentLiveTransport,
        surface_runtime: SurfaceRuntime,
        task_runtime_factory: TaskRuntimeFactory,
        task_manager_agent: TaskManagerAgent | None = None,
        on_event: EventCallback,
        on_audio: AudioCallback,
    ) -> None:
        self._session_id = session_id
        self._settings = settings
        self._transport = transport
        self._surface_runtime = surface_runtime
        self._task_manager_agent = task_manager_agent
        self._on_event = on_event
        self._on_audio = on_audio
        self._active_query = ""
        self._transcript: list[str] = []
        self._tool_calls = 0
        self._animation_calls = 0
        self._audio_chunks = 0
        self._audio_bytes = 0
        self._usage_metadata: dict[str, int] | None = None
        self._sample_rate: int | None = None
        self._audio_started = False
        self._speech_started_monotonic: float | None = None
        self._ui_pending_text_trace: list[str] = []
        self._ui_pending_text_trace_timestamp: str | None = None
        self._ui_pending_text_trace_turn_id: str | None = None
        self._pending_visual_marker: dict[str, Any] | None = None
        self._visual_marker_queued_at: float | None = None
        self._interrupted_turn_pending = False
        # A typed message can interrupt a model turn while the microphone
        # stream's sole receiver remains active. Ignore the old completion
        # signal until Gemini begins producing the typed turn.
        self._text_barge_in_pending = False
        self._output_turn_id: str | None = None
        self._planning_turn_pending = False
        self._surface_plan_in_progress = False
        self._planning_task: asyncio.Task[None] | None = None
        self._pending_task_messages: deque[_QueuedTaskMessage] = deque()
        # Lifecycle events remain individually visible to Browser diagnostics,
        # but must not each create a separate Gemini Live turn.  Buffer them
        # briefly by run, then deliver their original order in one batch.
        self._pending_task_events: dict[str, list[dict[str, Any]]] = {}
        self._task_event_batch_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_message_delivery: asyncio.Task[None] | None = None
        self._active_task_run_id: str | None = None
        self._surface_ready_run_id: str | None = None
        # Final context is deliberately withheld until Browser confirms that
        # this exact revision has rendered.  It prevents Gemini from
        # presenting a panel that the user cannot see yet.
        self._pending_final_surface: tuple[
            str, int, str, dict[str, Any], dict[str, Any]
        ] | None = None
        self._task_runtime = task_runtime_factory(
            session_id,
            self._receive_task_event,
            self._receive_draft_presentation,
            self._receive_complete_presentation,
        )

    @property
    def state(self) -> LiveSessionState:
        return self._surface_runtime.session_state(self._session_id)

    async def submit_text(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise GeminiLiveSessionError("Câu hỏi không được để trống.")
        await self._set_state(LiveSessionState.LISTENING)
        self._begin_turn(query)
        await self._transport.send_text(query)
        await self._set_state(LiveSessionState.WAITING_FOR_TOOL)
        return await self._consume_until_settled()

    async def interrupt_with_text(self, query: str) -> None:
        """Inject typed input into the active microphone stream as a barge-in.

        ``consume_audio_stream()`` remains the only coroutine reading Gemini
        messages. This method only sends input; the stream loop receives the
        interruption and then the response to this typed turn.
        """

        query = query.strip()
        if not query:
            raise GeminiLiveSessionError("Text query must not be empty.")
        await self._set_state(LiveSessionState.LISTENING)
        self._begin_turn(query)
        self._text_barge_in_pending = True
        trace("TEXT_BARGE_IN_SENT chars=%s", len(query))
        await self._transport.send_text(query)

    async def submit_panel_interaction(self, interaction: dict[str, Any]) -> dict[str, Any]:
        """Submit one trusted browser interaction when no receiver is active."""

        payload = self._panel_interaction_payload(interaction)
        await self._set_state(LiveSessionState.LISTENING)
        self._begin_turn("<panel interaction>")
        trace("PANEL_INTERACTION_SENT anchor=%s", interaction.get("anchor_id"))
        await self._transport.send_text(payload)
        await self._set_state(LiveSessionState.WAITING_FOR_TOOL)
        return await self._consume_until_settled()

    async def interrupt_with_panel_interaction(self, interaction: dict[str, Any]) -> None:
        """Inject one trusted browser interaction into an active receiver."""

        payload = self._panel_interaction_payload(interaction)
        await self._set_state(LiveSessionState.LISTENING)
        self._begin_turn("<panel interaction>")
        self._text_barge_in_pending = True
        trace("PANEL_INTERACTION_BARGE_IN_SENT anchor=%s", interaction.get("anchor_id"))
        await self._transport.send_text(payload)

    async def sync_surface_context(self, panel_context: dict[str, Any]) -> None:
        """Silently replace Gemini's panel map after browser-confirmed repair."""

        surface_id = panel_context.get("surface_id")
        revision = panel_context.get("revision")
        stage_map = panel_context.get("visual_stage_map")
        effects = panel_context.get("visual_effects")
        if not isinstance(surface_id, str) or not surface_id:
            raise GeminiLiveSessionError("surface context requires a surface_id.")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise GeminiLiveSessionError("surface context requires a positive revision.")
        if not isinstance(stage_map, str) or not stage_map:
            raise GeminiLiveSessionError("surface context requires a visual_stage_map.")
        if not isinstance(effects, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("description"), str)
            for item in effects
        ):
            raise GeminiLiveSessionError(
                "surface context requires visual_effects with id and description."
            )
        payload = surface_context_update_message(panel_context)
        await self._transport.send_text(payload, turn_complete=False)
        logger.info(
            "[REWOO_ASCII_MAP_SENT_TO_GEMINI] kind=repair_context surface=%s revision=%s\n%s",
            surface_id,
            revision,
            stage_map,
        )
        trace("SURFACE_CONTEXT_SYNC_SENT surface=%s revision=%s", surface_id, revision)

    @staticmethod
    def _panel_interaction_payload(interaction: dict[str, Any]) -> str:
        return panel_interaction_message(interaction)

    async def begin_audio(self) -> None:
        """Enter listening when the persistent browser microphone begins."""

        await self._set_state(LiveSessionState.LISTENING)
        self._begin_turn("<voice>")
        await self._on_event({"type": "live:input_ready", "sample_rate_hz": 16_000})

    async def send_audio(self, pcm: bytes) -> None:
        if self.state in {LiveSessionState.IDLE, LiveSessionState.ERROR}:
            raise GeminiLiveSessionError("Microphone audio is accepted only while the Live session is active.")
        await self._transport.send_audio(pcm)

    async def end_audio_stream(self) -> None:
        """Close the physical microphone stream, never an individual VAD turn."""

        await self._transport.end_audio()
        trace("GEMINI_AUDIO_STREAM_END_SENT")

    async def consume_audio_stream(self) -> None:
        """Continuously consume Gemini events while the browser microphone is enabled.

        Gemini's automatic VAD, rather than ``audio_stream_end``, decides each
        user turn.  This coroutine intentionally remains alive across model
        turn completions until the persistent transport closes or its caller
        cancels it.
        """

        async for message in self._transport.receive():
            server = getattr(message, "server_content", None)
            self._capture_usage_metadata(message)
            await self._handle_input_transcript(server)
            if await self._handle_interruption(server):
                continue
            self._resume_after_interruption_if_output(message, server)
            await self._handle_tool_calls(message)
            await self._handle_model_turn(server)
            if server and bool(getattr(server, "turn_complete", False)):
                if self._text_barge_in_pending:
                    trace("GEMINI_STALE_TURN_COMPLETE_IGNORED_AFTER_TEXT_BARGE_IN")
                    continue
                if self._interrupted_turn_pending:
                    trace("GEMINI_INTERRUPTED_TURN_COMPLETE_IGNORED")
                    self._interrupted_turn_pending = False
                    self._reset_voice_turn_tracking()
                    continue
                if await self._handle_turn_complete():
                    summary = self._finish_turn()
                    await self._on_event({
                        "type": "live:turn_complete",
                        "session_id": self._session_id,
                        "turn_id": self._output_turn_id,
                        "summary": summary,
                    })
                    self._reset_voice_turn_tracking()
                    await self._release_task_messages_after_turn()
        raise GeminiLiveSessionError("Gemini Live connection closed while microphone streaming.")

    async def close(self) -> None:
        if self._planning_task is not None and not self._planning_task.done():
            self._planning_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._planning_task
        for batch_task in self._task_event_batch_tasks.values():
            if not batch_task.done():
                batch_task.cancel()
        if self._task_event_batch_tasks:
            await asyncio.gather(*self._task_event_batch_tasks.values(), return_exceptions=True)
            self._task_event_batch_tasks.clear()
        self._pending_task_events.clear()
        if self._task_message_delivery is not None and not self._task_message_delivery.done():
            self._task_message_delivery.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task_message_delivery
        close_task_runtime = getattr(self._task_runtime, "close", None)
        if callable(close_task_runtime):
            closed = close_task_runtime()
            if inspect.isawaitable(closed):
                await closed
        await self._transport.close()
        self._surface_runtime.reset_session_state(self._session_id)
        await self._on_event({"type": "live:state", "state": LiveSessionState.IDLE})

    def _begin_turn(self, user_text: str) -> None:
        if self._pending_visual_marker is not None:
            trace(
                "LIVE_VISUAL_MARKER_CLEARED session=%s reason=new_input anchor=%s wait_ms=%.1f",
                self._session_id, self._pending_visual_marker["cue"].get("anchor_id"),
                (time.perf_counter() - self._visual_marker_queued_at) * 1000
                if self._visual_marker_queued_at is not None else -1,
            )
        begin_turn()
        self._active_query = user_text
        self._transcript = []
        self._tool_calls = self._animation_calls = self._audio_chunks = self._audio_bytes = 0
        self._usage_metadata = None
        self._sample_rate = None
        self._audio_started = False
        self._speech_started_monotonic = None
        self._ui_pending_text_trace = []
        self._ui_pending_text_trace_timestamp = None
        self._ui_pending_text_trace_turn_id = None
        self._pending_visual_marker = None
        self._interrupted_turn_pending = False
        self._text_barge_in_pending = False
        self._output_turn_id = None
        if user_text == "<voice>":
            trace("MIC_BEGIN")
        else:
            trace("TEXT_BEGIN chars=%s", len(user_text))

    def _reset_voice_turn_tracking(self) -> None:
        """Prepare for Gemini VAD's next voice turn without ending the PCM stream."""

        self._active_query = "<voice>"
        self._transcript = []
        self._tool_calls = self._animation_calls = self._audio_chunks = self._audio_bytes = 0
        self._usage_metadata = None
        self._sample_rate = None
        self._audio_started = False
        self._speech_started_monotonic = None
        self._ui_pending_text_trace = []
        self._ui_pending_text_trace_timestamp = None
        self._ui_pending_text_trace_turn_id = None
        self._pending_visual_marker = None
        self._interrupted_turn_pending = False
        self._text_barge_in_pending = False
        self._output_turn_id = None

    async def _handle_interruption(self, server: Any) -> bool:
        """Discard the cancelled model output and notify the browser promptly."""

        if not server or not bool(getattr(server, "interrupted", False)):
            return False
        await self._flush_ui_text_trace()
        if self._pending_visual_marker is not None:
            warning(
                "VISUAL_MARKER_DISCARDED_ON_INTERRUPT anchor=%s",
                self._pending_visual_marker["cue"].get("anchor_id"),
            )
            self._pending_visual_marker = None
        self._interrupted_turn_pending = True
        trace("GEMINI_INTERRUPTED")
        await self._set_state(LiveSessionState.LISTENING)
        interrupted_turn_id = self._output_turn_id
        await self._on_event({"type": "live:interrupted", "turn_id": interrupted_turn_id})
        # The next model output is a new VAD turn, not a continuation of the
        # generation that Gemini cancelled.
        self._output_turn_id = None
        return True

    def _ensure_output_turn_id(self) -> str:
        """Return the local identifier for this model-output turn."""

        if self._output_turn_id is None:
            self._output_turn_id = uuid.uuid4().hex[:8]
        return self._output_turn_id

    def _resume_after_interruption_if_output(self, message: Any, server: Any) -> None:
        """A fresh model output belongs to the user's new VAD-managed turn."""

        has_tool_call = bool(getattr(getattr(message, "tool_call", None), "function_calls", None))
        has_model_turn = bool(getattr(getattr(server, "model_turn", None), "parts", None)) if server else False
        if self._text_barge_in_pending and (has_tool_call or has_model_turn):
            self._text_barge_in_pending = False
            trace("TEXT_BARGE_IN_NEW_OUTPUT_RECEIVED")
        if not self._interrupted_turn_pending:
            return
        if has_tool_call or has_model_turn:
            self._interrupted_turn_pending = False

    def _capture_usage_metadata(self, message: Any) -> None:
        """Keep the latest usage record emitted by Gemini for this Live turn."""

        raw_usage = getattr(message, "usage_metadata", None)
        if raw_usage is not None:
            log_token_usage(
                agent="gemini_live", model=self._settings.gemini_live_model, api="gemini_live",
                usage=raw_usage, session=self._session_id, scope="provider_update",
            )
        usage = _live_usage_for_ui(raw_usage)
        if usage is not None:
            self._usage_metadata = usage

    @staticmethod
    def _format_usage(usage: dict[str, int] | None) -> str:
        if usage is None:
            return "usage metadata: provider không trả về"
        labels = (
            ("prompt_tokens", "input"),
            ("tool_input_tokens", "tool input"),
            ("output_tokens", "output"),
            ("reasoning_tokens", "thinking (tách riêng)"),
            ("total_tokens", "total"),
        )
        return " · ".join(
            f"{label} {usage[name]} tokens" for name, label in labels if name in usage
        )

    async def _emit_live_usage(self) -> None:
        """Show Gemini Live's provider-reported usage once its turn is complete."""

        if self._usage_metadata is None:
            log_token_usage(
                agent="gemini_live", model=self._settings.gemini_live_model, api="gemini_live",
                usage=None, session=self._session_id, scope="turn_complete",
            )
        await self._on_event({
            "type": "live:debug_trace",
            "timestamp": _ui_trace_timestamp(),
            "event": "tokens",
            "turn_id": self._ensure_output_turn_id(),
            "content": (
                "Gemini Live · output của lượt hoàn tất · "
                + self._format_usage(self._usage_metadata)
            ),
        })

    async def _set_state(self, target: LiveSessionState) -> None:
        current = self.state
        if current != target:
            self._surface_runtime.transition_session(session_id=self._session_id, target=target)
        await self._on_event({"type": "live:state", "state": target})

    async def _consume_until_settled(self) -> dict[str, Any]:
        """Handle messages until this turn safely returns to listening."""

        async for message in self._transport.receive():
            server = getattr(message, "server_content", None)
            self._capture_usage_metadata(message)
            await self._handle_input_transcript(server)
            if await self._handle_interruption(server):
                continue
            self._resume_after_interruption_if_output(message, server)
            await self._handle_tool_calls(message)
            await self._handle_model_turn(server)
            if server and bool(getattr(server, "turn_complete", False)):
                if self._interrupted_turn_pending:
                    trace("GEMINI_INTERRUPTED_TURN_COMPLETE_IGNORED")
                    self._interrupted_turn_pending = False
                    self._reset_voice_turn_tracking()
                    continue
                if await self._handle_turn_complete():
                    bridge_turn_completed = self._planning_turn_pending
                    await self._release_task_messages_after_turn()
                    if bridge_turn_completed:
                        continue
                    return self._finish_turn()
        raise GeminiLiveSessionError("Gemini Live connection closed before the turn completed.")

    async def _handle_input_transcript(self, server: Any) -> None:
        input_transcript = getattr(server, "input_transcription", None) if server else None
        if input_transcript and getattr(input_transcript, "text", None):
            self._active_query = str(input_transcript.text)
            trace("LIVE_INPUT_TRANSCRIPT final=%s text=%r", bool(getattr(input_transcript, "finished", False)), self._active_query[:300])
            await self._on_event({
                "type": "input_transcript",
                "text": self._active_query,
                "final": bool(getattr(input_transcript, "finished", False)),
            })

    async def _handle_tool_calls(self, message: Any) -> None:
        calls = getattr(getattr(message, "tool_call", None), "function_calls", None) or []
        if not calls:
            return
        handling_started = time.perf_counter()
        responses: list[types.FunctionResponse] = []
        response_tool_names: list[str] = []
        effect_count = 0
        for call in calls:
            await self._flush_ui_text_trace()
            name, call_id = str(call.name), str(call.id)
            args = dict(call.args) if isinstance(call.args, dict) else {}
            output_turn_id = self._ensure_output_turn_id()
            self._tool_calls += 1
            trace("GEMINI_TOOL_CALL_RECEIVED name=%s call_id=%s args=%s", name, call_id, json.dumps(args, ensure_ascii=False))
            await self._on_event({
                "type": "live:debug_trace",
                "timestamp": _ui_trace_timestamp(),
                "event": "toolcall",
                "turn_id": output_turn_id,
                "content": f"{name}({json.dumps(args, ensure_ascii=False, separators=(',', ': '))})",
            })
            if name == "present_visual":
                # A visual marker is allowed while Gemini is already speaking.
                # Do not move to WAITING_FOR_TOOL: that transition used to reject
                # speaking -> waiting_for_tool and discarded the animation call.
                response = await self._handle_present_visual(args)
                responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
                response_tool_names.append(name)
                await self._on_event({"type": "tool_result", "name": name, "response": response})
                continue
            if name == "update_surface_state":
                response, document_update = await self._handle_update_surface_state(args)
                if document_update is not None:
                    await self._on_event({"type": "openui:document", "document": document_update})
                responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
                response_tool_names.append(name)
                await self._on_event({"type": "tool_result", "name": name, "response": response})
                continue
            if name == "delete_surface":
                response, panel_clear = await self._handle_delete_surface(args)
                if panel_clear is not None:
                    await self._on_event({"type": "panel_clear", **panel_clear})
                responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
                response_tool_names.append(name)
                await self._on_event({"type": "tool_result", "name": name, "response": response})
                continue
            if name == "request_task_manager":
                response = self._start_task_manager_request(args)
                logger.info(
                    "[LIVE:TASK_MANAGER_RESPONSE] session=%s response=%s",
                    self._session_id, json.dumps(response, ensure_ascii=False),
                )
                responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
                response_tool_names.append(name)
                await self._on_event({"type": "tool_result", "name": name, "response": response})
                continue
            response = {"status": "unsupported", "message": "Unknown Live tool."}
            responses.append(types.FunctionResponse(id=call_id, name=name, response={"result": response}))
            response_tool_names.append(name)
            await self._on_event({"type": "tool_result", "name": name, "response": response})
        await self._transport.send_tool_responses(responses)
        await self._on_event({
            "type": "live:debug_trace",
            "timestamp": _ui_trace_timestamp(),
            "event": "tool_response",
            "turn_id": self._ensure_output_turn_id(),
            "content": ", ".join(response_tool_names),
        })
        trace(
            "TOOL_RESPONSE_SENT_TO_GEMINI effects=%s",
            effect_count,
        )
        trace(
            "LIVE_TOOL_BATCH_HANDLED session=%s calls=%s handling_ms=%.1f",
            self._session_id, [(str(call.name), str(call.id)) for call in calls],
            (time.perf_counter() - handling_started) * 1000,
        )

    def _start_task_manager_request(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Acknowledge delegation without blocking the Live receive loop."""
        if self._surface_plan_in_progress or (
            self._active_task_run_id is not None
            and self._task_runtime.manager.surface_work_pending(self._active_task_run_id)
        ):
            return {"status": "busy", "message": "Mình đang hoàn tất nội dung hiện tại."}
        try:
            request_task_manager_from_call({"name": "request_task_manager", "arguments": arguments})
            if self._task_manager_agent is None:
                raise TaskManagerAgentError("Task Manager Agent is not configured.")
        except (TaskContractError, TaskManagerAgentError, ValueError) as exc:
            return {"status": "rejected", "message": str(exc)}
        self._surface_plan_in_progress = True
        self._planning_turn_pending = True
        self._planning_task = asyncio.create_task(self._finish_task_manager_request(dict(arguments)))
        return {"status": "planning"}

    async def _finish_task_manager_request(self, arguments: dict[str, Any]) -> None:
        try:
            result = await self._request_task_manager(arguments, reserved=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {"status": "rejected", "message": "Không thể hoàn tất kế hoạch."}
        status_message = "TASK_MANAGER_STATUS\n" + json.dumps(result, ensure_ascii=False)
        # Attach status to the pending panel context instead of starting an
        # additional presentation turn for the same plan.
        run_id = result.get("run_id")
        for index, message in enumerate(self._pending_task_messages):
            if run_id is not None and message.run_id == run_id and message.text.startswith("SURFACE_"):
                self._pending_task_messages[index] = _QueuedTaskMessage(
                    message.text + "\n\n" + status_message, run_id=run_id)
                break
        else:
            self._pending_task_messages.appendleft(_QueuedTaskMessage(status_message, run_id=run_id))
        logger.info("[LIVE:TASK_MANAGER_BACKGROUND_RESULT] session=%s response=%s",
                    self._session_id, json.dumps(result, ensure_ascii=False))
        self._schedule_task_message_delivery()

    async def _request_task_manager(self, arguments: dict[str, Any], *, reserved: bool = False) -> dict[str, Any]:
        """Delegate a natural-language request to the Task Manager Agent.

        The Agent produces the surface frame and task declarations; Runtime
        compiles that frame and schedules the declared Runtime tasks.
        """

        started = time.perf_counter()
        phase = "busy_check"
        logger.info(
            "[LIVE:TASK_MANAGER_REQUEST] session=%s run=%s request=%s",
            self._session_id, self._active_task_run_id,
            json.dumps(arguments, ensure_ascii=False),
        )
        if not reserved and (self._surface_plan_in_progress or (
            self._active_task_run_id is not None
            and self._task_runtime.manager.surface_work_pending(self._active_task_run_id)
        )):
            return {
                "status": "busy",
                "message": "Mình đang hoàn tất nội dung hiện tại. Bạn chờ xong rồi yêu cầu chỉnh sửa nhé.",
            }
        # Set before the first await so overlapping tool calls cannot plan edits.
        self._surface_plan_in_progress = True
        planning_started = False
        try:
            phase = "planning"
            if self._task_manager_agent is None:
                raise TaskManagerAgentError("Task Manager Agent is not configured.")
            command = request_task_manager_from_call({
                "name": "request_task_manager", "arguments": arguments,
            })
            memory = self._surface_runtime.session_memory(self._session_id)
            recent_history = tuple(
                {"role": item["role"], "text": item["content"]}
                for item in memory.history[-6:]
                if item.get("role") in {"user", "assistant"}
                and isinstance(item.get("content"), str)
                and item["content"].strip()
            )
            planning_started = True
            await self._on_event({"type": "surface:planning", "status": "started"})
            accepted = self._surface_runtime.accepted_openui_program(self._session_id)
            # Keep provider streaming inside Task Manager.  The browser only
            # receives an OpenUI document after parsing and validation succeed,
            # so rejected correction attempts can never replace the visible UI.
            plan = await self._task_manager_agent.plan(TaskManagerAgentRequest(
                request=command.request.request,
                recent_history=recent_history,
                active_surface_summary=self._surface_runtime.active_surface_for_task(self._session_id),
                accepted_program=accepted,
            ))
            logger.info(
                "[LIVE:TASK_MANAGER_PLAN_READY] session=%s mode=%s blocks=%s tasks=%s duration_ms=%s",
                self._session_id, "edit" if accepted is not None else "create",
                len(plan.blocks), len(plan.tasks), round((time.perf_counter() - started) * 1000),
            )
            if accepted is not None:
                phase = "edit_preflight"
                if self._active_task_run_id is None or plan.openui_program is None:
                    raise ValueError('Edit requires the active task run and an OpenUI program.')
                prepared = self._task_runtime.preflight_surface_edit(plan,
                    surface_runtime=self._surface_runtime, run_id=self._active_task_run_id)
                phase = "edit_install"
                # Synchronous install/preflight precedes cancellation or scheduling.
                self._surface_runtime.install_surface(session_id=self._session_id, compiled=prepared.compiled)
                self._surface_runtime.accept_openui_program(session_id=self._session_id,
                    surface_id=prepared.compiled.surface_id, program=plan.openui_program)
                if not reserved:
                    self._planning_turn_pending = True
                self._surface_ready_run_id = None
                self._pending_final_surface = None
                phase = "edit_update_plan"
                snapshot = await self._task_runtime.manager.update_surface_plan(
                    self._active_task_run_id, plan, compiled=prepared.compiled)
                phase = "edit_publish"
                await self._on_event({
                    'type': 'openui:document',
                    'document': self._surface_runtime.active_openui_document(self._session_id),
                })
                context = self._surface_runtime.active_surface_context(self._session_id)
                self._pending_task_messages.append(_QueuedTaskMessage(
                    'SURFACE_EDIT_READY\n\n' + surface_context_message(context),
                    run_id=snapshot.run_id))
                self._schedule_task_message_delivery()
                logger.info(
                    "[LIVE:SURFACE_EDIT_PUBLISHED] session=%s run=%s surface=%s revision=%s duration_ms=%s",
                    self._session_id, snapshot.run_id, prepared.compiled.surface_id,
                    prepared.compiled.revision, round((time.perf_counter() - started) * 1000),
                )
                return {'status':'planned', 'run_id':snapshot.run_id, 'revision':prepared.compiled.revision}
            phase = "create_preflight"
            prepared = self._task_runtime.preflight_surface_plan(plan)
            if not reserved:
                self._planning_turn_pending = True
            previous_run_id = self._active_task_run_id
            if previous_run_id is not None:
                await self._task_runtime.manager.supersede_run(previous_run_id)
                self._active_task_run_id = None
            active_surface = self._surface_runtime.active_surface_for_task(self._session_id)
            if plan.openui_program is None:
                raise ValueError('Create requires an accepted OpenUI program.')

            def commit_before_schedule() -> None:
                self._surface_runtime.install_surface(
                    session_id=self._session_id, compiled=prepared.compiled)
                self._surface_runtime.accept_openui_program(
                    session_id=self._session_id,
                    surface_id=prepared.compiled.surface_id,
                    program=plan.openui_program,
                )

            phase = "create_submit"
            snapshot = await self._task_runtime.submit_surface_plan(
                plan,
                active_surface=active_surface,
                prepared=prepared,
                before_schedule=commit_before_schedule,
            )
            self._active_task_run_id = snapshot.run_id
            self._surface_ready_run_id = None
            if previous_run_id is not None:
                self._discard_pending_messages_for_run(previous_run_id)
                if (
                    self._pending_final_surface is not None
                    and self._pending_final_surface[2] == previous_run_id
                ):
                    self._pending_final_surface = None
            phase = "create_publish"
            await self._task_runtime.present_draft(snapshot.run_id)
        except (TaskContractError, TaskManagerAgentError, TaskManagerError, ValueError) as exc:
            logger.warning(
                "[LIVE:TASK_MANAGER_REJECTED] session=%s run=%s phase=%s error_type=%s reason=%s duration_ms=%s",
                self._session_id, self._active_task_run_id, phase, type(exc).__name__, exc,
                round((time.perf_counter() - started) * 1000), exc_info=True,
            )
            self._planning_turn_pending = False
            return {"status": "rejected", "message": str(exc)}
        except Exception:
            logger.exception(
                "[LIVE:TASK_MANAGER_FAILED] session=%s run=%s phase=%s duration_ms=%s",
                self._session_id, self._active_task_run_id, phase,
                round((time.perf_counter() - started) * 1000),
            )
            raise
        finally:
            try:
                if planning_started:
                    await self._on_event({"type": "surface:planning", "status": "stopped"})
            finally:
                self._surface_plan_in_progress = False

        trace(
            "TASK_MANAGER_REQUEST_PLANNED session=%s blocks=%s tasks=%s",
            self._session_id,
            len(plan.blocks),
            len(plan.tasks),
        )
        return {"status": "planned", "run_id": snapshot.run_id}

    async def _receive_task_event(self, event: TaskEvent) -> None:
        """Bridge every manager lifecycle event to diagnostics and Gemini FIFO."""

        trace(
            "TASK_EVENT run=%s task=%s type=%s event=%s status=%s evidence=%s artifact=%s warnings=%s missing_parts=%s fallback_of=%s summary=%r",
            event.run_id, event.task_id, event.task_type, event.event,
            event.status, event.evidence_ref or "-", event.artifact_id or "-",
            list(event.warnings), list(event.missing_parts),
            event.fallback_of_task_id or "-", event.summary,
        )
        if event.event == "TASK_FAILED":
            warning(
                "TASK_FAILED run=%s task=%s type=%s evidence=%s detail=%s",
                event.run_id,
                event.task_id,
                event.task_type,
                event.evidence_ref or "-",
                "; ".join(event.warnings) or event.summary,
            )
        await self._on_event({
            "type": "live:debug_trace",
            "timestamp": _ui_trace_timestamp(),
            "event": "task_event",
            "turn_id": self._ensure_output_turn_id(),
            "content": json.dumps({
                "event": event.event,
                "run_id": event.run_id,
                "task_id": event.task_id,
                "task_type": event.task_type,
                "status": event.status,
                "evidence_ref": event.evidence_ref,
                "artifact_id": event.artifact_id,
                "warnings": list(event.warnings),
                "missing_parts": list(event.missing_parts),
                "fallback_of_task_id": event.fallback_of_task_id,
            }, ensure_ascii=False, separators=(",", ":")),
        })
        # Diagnostics above always retain every event.  Gemini only receives
        # progress for the active, not-yet-presented run.  Once final surface
        # readiness has been confirmed, late lifecycle events cannot push it
        # behind stale progress again.
        if (
            (self._active_task_run_id is not None and event.run_id != self._active_task_run_id)
            or event.run_id == self._surface_ready_run_id
        ):
            trace(
                "TASK_EVENT_NOT_QUEUED_FOR_GEMINI run=%s event=%s reason=inactive_or_surface_ready",
                event.run_id,
                event.event,
            )
            return
        self._queue_task_event_for_gemini(event)

    def _queue_task_event_for_gemini(self, event: TaskEvent) -> None:
        """Accumulate trusted lifecycle events without dropping their detail."""

        events = self._pending_task_events.setdefault(event.run_id, [])
        events.append({
            "event": event.event,
            "run_id": event.run_id,
            "task_id": event.task_id,
            "task_type": event.task_type,
            "status": event.status,
            "evidence_ref": event.evidence_ref,
            "artifact_id": event.artifact_id,
            "summary": event.summary,
            "warnings": list(event.warnings),
            "missing_parts": list(event.missing_parts),
            "fallback_of_task_id": event.fallback_of_task_id,
        })
        if event.run_id in self._task_event_batch_tasks:
            return
        self._task_event_batch_tasks[event.run_id] = asyncio.create_task(
            self._flush_task_event_batch_after_delay(event.run_id),
            name=f"lumi-task-event-batch:{self._session_id}:{event.run_id}",
        )

    async def _flush_task_event_batch_after_delay(self, run_id: str) -> None:
        """Deliver one ordered batch after a short event-collection window."""

        try:
            await asyncio.sleep(_TASK_EVENT_BATCH_DELAY_SECONDS)
            events = self._pending_task_events.pop(run_id, [])
            if not events:
                return
            self._pending_task_messages.append(_QueuedTaskMessage(
                "TASK_EVENT\n" + json.dumps({
                    "run_id": run_id,
                    "events": events,
                }, ensure_ascii=False, separators=(",", ":")),
                run_id=run_id,
            ))
            trace(
                "TASK_EVENT_BATCH_QUEUED run=%s events=%s",
                run_id,
                len(events),
            )
            self._schedule_task_message_delivery()
        except asyncio.CancelledError:
            raise
        finally:
            self._task_event_batch_tasks.pop(run_id, None)

    async def _receive_draft_presentation(
        self, _payload: dict[str, Any], _context: dict[str, Any]
    ) -> None:
        """Commit the accepted Task Manager Lang document to the browser."""
        try:
            document = self._surface_runtime.active_openui_document(self._session_id)
        except (TypeError, ValueError) as exc:
            warning("DRAFT_PANEL_DROPPED session=%s detail=%s", self._session_id, exc)
            await self._on_event({
                "type": "live:debug_trace", "timestamp": _ui_trace_timestamp(),
                "event": "draft_surface_dropped", "content": str(exc),
            })
            return
        trace(
            "DRAFT_OPENUI_DOCUMENT_SENT session=%s surface=%s revision=%s slots=%s",
            self._session_id,
            document.get("surface_id", "-"),
            document.get("revision", "-"),
            len(document.get("slots", {})),
        )
        await self._on_event({"type": "openui:document", "document": document})

    async def _receive_complete_presentation(
        self, payload: dict[str, Any], _context: dict[str, Any]
    ) -> Any:
        """Render one final/partial compiled panel and send its exact map."""

        compiled = payload.get("compiled")
        if compiled is None:
            return
        surface_completion = _surface_completion(payload.get("surface_completion"))
        try:
            if payload.get('owner_run_id') is not None:
                payload = await self._task_runtime.prepare_completion(payload, surface_runtime=self._surface_runtime)
                compiled = payload['compiled']
            owner_run = payload.get('owner_run_id')
            if owner_run is not None:
                snapshot = self._task_runtime.manager.snapshot(owner_run)
                record = next((r for r in snapshot.records if r.task_id == payload.get('owner_task_id')), None)
                if (owner_run != self._active_task_run_id or snapshot.complete_task_id != payload.get('owner_task_id')
                        or record is None or record.status != 'RUNNING'):
                    raise ValueError('Completion belongs to obsolete work.')
            document, installed = self._surface_runtime.install_completion(
                session_id=self._session_id, compiled=compiled,
                program=payload.get('openui_program'), base_source=payload.get('base_source'),
            )
            if owner_run is not None:
                self._task_runtime.manager.accept_completed_content(owner_run,
                    program=payload['openui_program'], blocks=payload['semantic_blocks'], compiled=installed)
            panel_context = self._surface_runtime.active_surface_context(self._session_id)
        except (TypeError, ValueError) as exc:
            warning("COMPLETE_PANEL_DROPPED session=%s detail=%s", self._session_id, exc)
            await self._on_event({
                "type": "live:debug_trace", "timestamp": _ui_trace_timestamp(),
                "event": "complete_surface_dropped", "content": str(exc),
            })
            raise
        trace(
            "COMPLETE_OPENUI_DOCUMENT_SENT session=%s surface=%s revision=%s slots=%s",
            self._session_id,
            document.get("surface_id", "-"),
            document.get("revision", "-"),
            len(document.get("slots", {})),
        )
        await self._on_event({"type": "openui:document", "document": document})
        if panel_context is not None and self._active_task_run_id is not None:
            self._pending_final_surface = (
                str(panel_context["surface_id"]),
                int(panel_context["revision"]),
                self._active_task_run_id,
                panel_context,
                surface_completion,
            )
            trace(
                "FINAL_SURFACE_AWAITING_BROWSER_CONFIRMATION run=%s surface=%s revision=%s",
                self._active_task_run_id,
                panel_context["surface_id"], panel_context["revision"],
            )
        return {'compiled':installed, 'semantic_blocks':payload['semantic_blocks'],
                'openui_program':payload['openui_program']}

    async def report_runtime_diagnostic(self, diagnostic_result: dict[str, Any]) -> None:
        """Forward a validated browser diagnostic as trusted runtime context."""

        self._pending_task_messages.append(_QueuedTaskMessage(
            "RUNTIME_DIAGNOSTIC\n" + json.dumps(diagnostic_result, ensure_ascii=False)
        ))
        self._schedule_task_message_delivery()

    async def confirm_surface_rendered(
        self, *, surface_id: str, revision: object
    ) -> dict[str, Any]:
        """Release final context only after Browser confirms the exact revision."""

        pending = self._pending_final_surface
        if pending is None:
            return {"status": "ignored", "detail": "no final surface is awaiting confirmation"}
        (
            pending_surface_id,
            pending_revision,
            run_id,
            panel_context,
            surface_completion,
        ) = pending
        if surface_id != pending_surface_id or revision != pending_revision:
            return {"status": "ignored", "detail": "rendered surface is not the pending final revision"}
        if run_id != self._active_task_run_id:
            self._pending_final_surface = None
            return {"status": "ignored", "detail": "pending final surface belongs to an inactive run"}

        discarded = self._discard_pending_messages_for_run(run_id)
        self._surface_ready_run_id = run_id
        self._pending_final_surface = None
        self._pending_task_messages.appendleft(_QueuedTaskMessage(
            surface_ready_message(panel_context, surface_completion), run_id=run_id
        ))
        trace(
            "FINAL_SURFACE_READY_QUEUED run=%s surface=%s revision=%s discarded_progress=%s\n%s",
            run_id,
            surface_id,
            revision,
            discarded,
            panel_context["visual_stage_map"],
        )
        self._schedule_task_message_delivery()
        return {"status": "queued", "discarded_progress_messages": discarded}

    async def _release_task_messages_after_turn(self) -> None:
        if self._planning_turn_pending:
            self._planning_turn_pending = False
        self._schedule_task_message_delivery()

    def _schedule_task_message_delivery(self) -> None:
        if (
            not self._pending_task_messages
            or self._planning_turn_pending
            or self.state is not LiveSessionState.LISTENING
            or (self._task_message_delivery is not None and not self._task_message_delivery.done())
        ):
            return
        self._task_message_delivery = asyncio.create_task(
            self._deliver_one_task_message(), name=f"lumi-task-event:{self._session_id}"
        )

    async def _deliver_one_task_message(self) -> None:
        if self._planning_turn_pending or self.state is not LiveSessionState.LISTENING:
            return
        try:
            message = self._pending_task_messages.popleft()
        except IndexError:
            return
        trace(
            "LIVE_TASK_MESSAGE_SEND session=%s run=%s remaining=%s kind=%r",
            self._session_id, message.run_id, len(self._pending_task_messages), message.text.split("\n", 1)[0][:120],
        )
        await self._transport.send_text(message.text)

    def _discard_pending_messages_for_run(self, run_id: str) -> int:
        """Drop unsent progress only; Browser diagnostics/logging are untouched."""

        before = len(self._pending_task_messages)
        self._pending_task_messages = deque(
            message for message in self._pending_task_messages if message.run_id != run_id
        )
        discarded = before - len(self._pending_task_messages)
        pending_events = self._pending_task_events.pop(run_id, [])
        if pending_events:
            discarded += len(pending_events)
        batch_task = self._task_event_batch_tasks.pop(run_id, None)
        if batch_task is not None and not batch_task.done():
            batch_task.cancel()
        return discarded

    async def _handle_present_visual(self, args: dict[str, Any]) -> dict[str, Any]:
        anchor_id = args.get("anchor_id")
        effect_id = args.get("effect_id")
        if not isinstance(anchor_id, str) or not isinstance(effect_id, str):
            return {"status": "rejected", "message": "anchor_id and effect_id must be strings"}
        try:
            cue = self._surface_runtime.present_visual(
                session_id=self._session_id,
                anchor_id=anchor_id,
                effect_id=effect_id,
            )
        except ValueError as exc:
            warning("PRESENT_VISUAL_REJECTED reason=%s", exc)
            return {"status": "rejected", "message": str(exc)}
        self._animation_calls += 1
        trace("PRESENT_VISUAL_ACCEPTED anchor=%s effect=%s", anchor_id, effect_id)
        if self._pending_visual_marker is not None:
            warning(
                "VISUAL_MARKER_REPLACED previous_anchor=%s next_anchor=%s",
                self._pending_visual_marker["cue"].get("anchor_id"),
                anchor_id,
            )
        self._pending_visual_marker = {
            "cue": cue,
            "animation_delay_ms": self._settings.presentation_animation_delay_ms,
            "turn_id": self._ensure_output_turn_id(),
        }
        self._visual_marker_queued_at = time.perf_counter()
        trace("VISUAL_MARKER_PENDING anchor=%s effect=%s", anchor_id, effect_id)
        return {"status": "completed", "anchor_id": anchor_id, "effect_id": effect_id}

    async def _handle_update_surface_state(
        self, args: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Apply one validated state transition without rerouting the request."""

        surface_id = args.get("surface_id")
        base_revision = args.get("base_revision")
        updates = args.get("updates")
        if (
            not isinstance(surface_id, str)
            or isinstance(base_revision, bool)
            or not isinstance(base_revision, int)
            or not isinstance(updates, list)
        ):
            return {
                "status": "rejected",
                "message": "surface_id, base_revision and updates are required",
            }, None
        try:
            result = self._surface_runtime.update_surface_state(
                session_id=self._session_id,
                surface_id=surface_id,
                base_revision=base_revision,
                updates=updates,
            )
        except ValueError as exc:
            warning("UPDATE_SURFACE_STATE_REJECTED reason=%s", exc)
            return {"status": "rejected", "message": str(exc)}, None
        trace(
            "UPDATE_SURFACE_STATE_ACCEPTED anchors=%s revision=%s",
            ",".join(result.response["updated_anchor_ids"]),
            result.response["revision"],
        )
        return result.response, result.document_update

    async def _handle_delete_surface(
        self, args: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Close a panel after validating the active surface revision."""

        surface_id = args.get("surface_id")
        base_revision = args.get("base_revision")
        if (
            not isinstance(surface_id, str)
            or isinstance(base_revision, bool)
            or not isinstance(base_revision, int)
        ):
            return {
                "status": "rejected",
                "message": "surface_id and base_revision are required",
            }, None
        try:
            result = self._surface_runtime.delete_surface(
                session_id=self._session_id,
                surface_id=surface_id,
                base_revision=base_revision,
            )
        except ValueError as exc:
            warning("DELETE_SURFACE_REJECTED reason=%s", exc)
            return {"status": "rejected", "message": str(exc)}, None
        trace("DELETE_SURFACE_COMPLETED surface=%s revision=%s", surface_id, result.response["revision"])
        return result.response, {
            "surface_id": result.response["surface_id"],
            "revision": result.response["revision"],
        }

    async def _handle_model_turn(self, server: Any) -> None:
        model_turn = getattr(server, "model_turn", None) if server else None
        for part in getattr(model_turn, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            pcm = getattr(inline, "data", None) if inline else None
            if not pcm:
                continue
            # A typed barge-in deliberately puts the session back into
            # LISTENING while its response is awaited.  The first PCM packet
            # is the authoritative signal that Gemini is now speaking for
            # that new turn, regardless of whether it was triggered by a
            # tool flow or typed input.  Without this transition the browser
            # keeps streaming microphone audio unconditionally and can feed
            # speaker echo back into Gemini.
            if self.state in {LiveSessionState.LISTENING, LiveSessionState.WAITING_FOR_TOOL}:
                await self._set_state(LiveSessionState.SPEAKING)
            output_turn_id = self._ensure_output_turn_id()
            rate = _sample_rate(getattr(inline, "mime_type", None))
            if self._sample_rate is None:
                self._sample_rate = rate
                await self._on_event({"type": "audio_format", "sample_rate_hz": rate})
            self._audio_chunks += 1
            self._audio_bytes += len(pcm)
            if not self._audio_started:
                self._audio_started = True
                self._speech_started_monotonic = time.perf_counter()
                trace("GEMINI_AUDIO_FIRST_PCM")
                await self._on_event({
                    "type": "live:debug_trace",
                    "timestamp": _ui_trace_timestamp(),
                    "event": "gemini_speech_started",
                    "turn_id": output_turn_id,
                    "content": f"turn={output_turn_id}",
                })
            marker = self._pending_visual_marker
            self._pending_visual_marker = None
            if marker is not None:
                cue = marker["cue"]
                trace(
                    "VISUAL_MARKER_ATTACHED_TO_PCM anchor=%s effect=%s chunk=%s",
                    cue.get("anchor_id"),
                    cue.get("effect"),
                    self._audio_chunks,
                )
                trace(
                    "LIVE_VISUAL_AUDIO_WAIT session=%s output_turn=%s anchor=%s wait_ms=%.1f animation_delay_ms=%s",
                    self._session_id, output_turn_id, cue.get("anchor_id"),
                    (time.perf_counter() - self._visual_marker_queued_at) * 1000
                    if self._visual_marker_queued_at is not None else -1,
                    marker.get("animation_delay_ms"),
                )
            await self._on_audio(pcm, rate, marker, output_turn_id)

        output = getattr(server, "output_transcription", None) if server else None
        if output and getattr(output, "text", None):
            text = str(output.text)
            self._transcript.append(text)
            if self._ui_pending_text_trace_timestamp is None:
                self._ui_pending_text_trace_timestamp = _ui_trace_timestamp()
                self._ui_pending_text_trace_turn_id = self._ensure_output_turn_id()
            self._ui_pending_text_trace.append(text)
            await self._on_event({
                "type": "text",
                "text": text,
                "turn_id": self._ensure_output_turn_id(),
            })

    async def _flush_ui_text_trace(self) -> None:
        """Discard trace-only transcript text; the Browser still receives text events."""

        self._ui_pending_text_trace = []
        self._ui_pending_text_trace_timestamp = None
        self._ui_pending_text_trace_turn_id = None

    async def _handle_turn_complete(self) -> bool:
        await self._flush_ui_text_trace()
        if self._speech_started_monotonic is not None:
            await self._on_event({
                "type": "live:debug_trace",
                "timestamp": _ui_trace_timestamp(),
                "event": "gemini_speech_completed",
                "turn_id": self._ensure_output_turn_id(),
                "content": (
                    f"turn={self._ensure_output_turn_id()} · "
                    f"duration={round((time.perf_counter() - self._speech_started_monotonic) * 1000)}ms"
                ),
            })
        await self._emit_live_usage()
        if self._pending_visual_marker is not None:
            warning(
                "VISUAL_MARKER_UNATTACHED anchor=%s",
                self._pending_visual_marker["cue"].get("anchor_id"),
            )
            self._pending_visual_marker = None
        if self._tool_calls == 0:
            warning("GEMINI_TURN_COMPLETE_WITHOUT_TOOL")
        trace("GEMINI_TURN_COMPLETE")
        await self._set_state(LiveSessionState.LISTENING)
        return True

    def _finish_turn(self) -> dict[str, Any]:
        final_text = "".join(self._transcript).strip()
        self._surface_runtime.remember_turn(
            session_id=self._session_id,
            user_text=self._active_query,
            assistant_text=final_text,
        )
        summary = {
            "turn_id": self._output_turn_id,
            "tool_calls": self._tool_calls,
            "animation_calls": self._animation_calls,
            "audio_chunks": self._audio_chunks,
            "audio_bytes": self._audio_bytes,
            "audio_sample_rate_hz": self._sample_rate,
            "transcript": final_text,
        }
        trace("PRESENTATION_COMPLETE visual_calls=%s", self._animation_calls)
        return summary
