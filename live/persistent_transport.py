"""One Gemini Live connection and receiver task per application session.

This module is intentionally domain-agnostic. It owns the connection lifecycle
and moves raw Gemini messages into a queue for shared orchestration.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from google.genai import types

from lumi_openui.trace import trace


class PersistentLiveTransportError(RuntimeError):
    """Raised when a persistent transport is unavailable or has closed."""


_CLOSED = object()


@dataclass(frozen=True)
class _ReceivedMessage:
    message: Any
    sequence: int
    received_at: float
    diagnostic: bool


class PersistentLiveTransport:
    """Own a single Live connection and its single background receive task."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._context_manager: Any | None = None
        self._session: Any | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._messages: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = True
        self._tx_sequence = 0
        self._rx_sequence = 0
        self._pcm_chunks = 0
        self._last_send: tuple[int, float] | None = None

    @property
    def connected(self) -> bool:
        return self._session is not None and not self._closed

    async def connect(self, context_manager: Any) -> None:
        """Open the Gemini context once and start exactly one receive task."""

        if self.connected:
            return
        # A remote Gemini close ends the receiver first.  Release that stale
        # context before replacing it, then start the new receiver with an
        # empty queue so an old ``_CLOSED`` sentinel cannot end the new turn.
        if self._context_manager is not None or self._session is not None:
            await self.close()
        self._messages = asyncio.Queue()
        self._context_manager = context_manager
        self._session = await context_manager.__aenter__()
        self._closed = False
        self._receive_task = asyncio.create_task(
            self._receive_loop(), name=f"gemini-live-receive:{self.session_id}"
        )

    async def send_text(self, text: str, *, turn_complete: bool = True) -> None:
        session = self._require_session()
        sequence, started = self._send_begin(
            "text", f"turn_complete={turn_complete} chars={len(text)} preview={text[:160]!r}"
        )
        if "SURFACE CONTEXT" in text or text.startswith("PANEL_INTERACTION_EVENT\n"):
            trace(
                "LIVE_TX_CONTEXT session=%s tx=%s payload=%s",
                self.session_id, sequence, json.dumps(text, ensure_ascii=False),
            )
        try:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]),
                turn_complete=turn_complete,
            )
        except BaseException:
            self._send_end(sequence, started, "text", "failed")
            raise
        self._send_end(sequence, started, "text", "sent")

    async def send_audio(self, pcm: bytes, *, sample_rate_hz: int = 16_000) -> None:
        session = self._require_session()
        await session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={sample_rate_hz}")
        )

    async def end_audio(self) -> None:
        session = self._require_session()
        sequence, started = self._send_begin("audio_stream_end")
        try:
            await session.send_realtime_input(audio_stream_end=True)
        except BaseException:
            self._send_end(sequence, started, "audio_stream_end", "failed")
            raise
        self._send_end(sequence, started, "audio_stream_end", "sent")

    async def send_tool_responses(self, function_responses: list[types.FunctionResponse]) -> None:
        session = self._require_session()
        calls = [(item.name, item.id) for item in function_responses]
        sequence, started = self._send_begin("tool_response", f"calls={calls!r}")
        for item in function_responses:
            if item.name == "update_surface_state":
                trace(
                    "LIVE_TX_STATE_RESPONSE session=%s tx=%s call_id=%s response=%s",
                    self.session_id, sequence, item.id,
                    json.dumps(item.response, ensure_ascii=False),
                )
        try:
            await session.send_tool_response(function_responses=function_responses)
        except BaseException:
            self._send_end(sequence, started, "tool_response", "failed")
            raise
        self._send_end(sequence, started, "tool_response", "sent")

    def _send_begin(self, kind: str, detail: str = "") -> tuple[int, float]:
        self._tx_sequence += 1
        started = time.perf_counter()
        trace("LIVE_TX_BEGIN session=%s tx=%s kind=%s %s", self.session_id, self._tx_sequence, kind, detail)
        return self._tx_sequence, started

    def _send_end(self, sequence: int, started: float, kind: str, status: str) -> None:
        now = time.perf_counter()
        if status == "sent":
            self._last_send = (sequence, now)
        trace(
            "LIVE_TX_END session=%s tx=%s kind=%s status=%s send_ms=%.1f",
            self.session_id, sequence, kind, status, (now - started) * 1000,
        )

    async def receive(self) -> AsyncIterator[Any]:
        """Yield raw Gemini messages collected by the sole receive task."""

        while True:
            item = await self._messages.get()
            if item is _CLOSED:
                return
            if isinstance(item, BaseException):
                raise PersistentLiveTransportError(str(item)) from item
            if isinstance(item, _ReceivedMessage):
                queue_ms = (time.perf_counter() - item.received_at) * 1000
                if item.diagnostic or queue_ms >= 100:
                    trace(
                        "LIVE_RX_DEQUEUED session=%s rx=%s queue_ms=%.1f queue_depth=%s",
                        self.session_id, item.sequence, queue_ms, self._messages.qsize(),
                    )
                item = item.message
            yield item

    async def close(self) -> None:
        """Cancel receive first, then release the Gemini async context exactly once."""

        if self._context_manager is None and self._session is None and self._receive_task is None:
            return
        self._closed = True
        receive_task, self._receive_task = self._receive_task, None
        if receive_task is not None and receive_task is not asyncio.current_task():
            receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receive_task
        context_manager, self._context_manager = self._context_manager, None
        self._session = None
        if context_manager is not None:
            await context_manager.__aexit__(None, None, None)
        await self._messages.put(_CLOSED)

    def discard_pending_messages(self) -> int:
        """Discard stale Gemini events after a browser reconnect.

        No surface context is removed: verified context lives in SessionMemoryStore.
        This only prevents old PCM/transcript events from a disconnected page
        being delivered to the newly attached browser.
        """

        discarded = 0
        while True:
            try:
                self._messages.get_nowait()
                discarded += 1
            except asyncio.QueueEmpty:
                return discarded

    def _require_session(self) -> Any:
        if not self.connected:
            raise PersistentLiveTransportError("Gemini Live persistent transport is not connected.")
        return self._session

    async def _receive_loop(self) -> None:
        try:
            session = self._require_session()
            # google-genai may end one ``receive()`` iterator at a model turn
            # boundary while keeping the underlying Live socket open.  Keep the
            # sole reader alive and obtain the next iterator instead of closing
            # the persistent application session after its first turn.
            while not self._closed:
                received_any = False
                async for message in session.receive():
                    received_any = True
                    received_at = time.perf_counter()
                    self._rx_sequence += 1
                    server = getattr(message, "server_content", None)
                    parts = getattr(getattr(server, "model_turn", None), "parts", None) or []
                    pcm_bytes = sum(len(getattr(getattr(part, "inline_data", None), "data", None) or b"") for part in parts)
                    calls = getattr(getattr(message, "tool_call", None), "function_calls", None) or []
                    interrupted = bool(getattr(server, "interrupted", False))
                    complete = bool(getattr(server, "turn_complete", False))
                    generation_complete = bool(getattr(server, "generation_complete", False))
                    if calls or interrupted:
                        self._pcm_chunks = 0
                    if pcm_bytes:
                        self._pcm_chunks += 1
                    diagnostic = not pcm_bytes or self._pcm_chunks == 1 or bool(calls) or interrupted or complete or generation_complete
                    if diagnostic:
                        last_tx, since_tx_ms = (None, None) if self._last_send is None else (
                            self._last_send[0], round((received_at - self._last_send[1]) * 1000, 1)
                        )
                        trace(
                            "LIVE_RX_RAW session=%s rx=%s last_tx=%s since_tx_ms=%s queue_depth=%s "
                            "tools=%s pcm_bytes=%s pcm_chunk=%s interrupted=%s turn_complete=%s "
                            "generation_complete=%s input_transcript=%s output_transcript=%s tool_cancel=%s",
                            self.session_id, self._rx_sequence, last_tx, since_tx_ms, self._messages.qsize(),
                            [(call.name, call.id) for call in calls], pcm_bytes, self._pcm_chunks,
                            interrupted, complete, generation_complete,
                            bool(getattr(server, "input_transcription", None)),
                            bool(getattr(server, "output_transcription", None)),
                            getattr(getattr(message, "tool_call_cancellation", None), "ids", None),
                        )
                    await self._messages.put(_ReceivedMessage(message, self._rx_sequence, received_at, diagnostic))
                    if complete:
                        self._pcm_chunks = 0
                if not received_any:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # The orchestrator decides recovery in CP-05.
            await self._messages.put(exc)
        finally:
            # Do not leave a remote-closed socket looking reusable.  The next
            # microphone turn can then establish a fresh Gemini session and
            # rehydrate its server-owned memory/context.
            self._closed = True
            await self._messages.put(_CLOSED)


class PersistentLiveTransportStore:
    """Map application ``session_id`` to one reusable Gemini transport."""

    def __init__(self) -> None:
        self._transports: dict[str, PersistentLiveTransport] = {}

    def get(self, session_id: str) -> PersistentLiveTransport:
        return self._transports.setdefault(session_id, PersistentLiveTransport(session_id))

    async def close(self, session_id: str) -> None:
        transport = self._transports.pop(session_id, None)
        if transport is not None:
            await transport.close()

    async def close_all(self) -> None:
        transports, self._transports = list(self._transports.values()), {}
        for transport in transports:
            await transport.close()
