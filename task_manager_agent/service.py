"""Task Manager Agent: Plan-Agent-style surface planning before Runtime."""

from __future__ import annotations

import json
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from lumi_openui.catalogs.templates import TemplateCatalog, TemplateCatalogError
from lumi_openui.surface import DraftCompilationError, DraftSurfaceCompiler, SurfaceDraft
from lumi_openui.task_catalog import STATIC_TASK_CATALOG
from lumi_openui.task_manager import SurfaceRetrievalPlan, TaskContractError
from lumi_openui.widgets import WidgetPropsError, WidgetRegistry
from lumi_openui.openui import OpenUIProcessor, OpenUIProcessorError, OpenUIProgramError, ProgramSource

from .prompts import TASK_MANAGER_AGENT_PROMPT


from lumi_openui.trace import log_token_usage


logger = logging.getLogger("lumi.task_manager_agent")
MAX_PLANNING_TURNS = 6

_FAKE_PLACEHOLDER_TEXTS = {
    "đang chờ dữ liệu",
    "tiêu đề sự kiện",
    "tiêu đề tin tức",
    "nội dung sẽ hiển thị ở đây",
    "data pending",
    "waiting for data",
}
ClientFactory = Callable[..., Any]
StreamCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

_TASK_CATALOG = STATIC_TASK_CATALOG

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "describe_widget",
            "description": "Lấy contract chi tiết của một widget trong catalog.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["widget_id"],
                "properties": {"widget_id": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_template",
            "description": "Lấy contract chi tiết của một template trong catalog.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["template_id"],
                "properties": {"template_id": {"type": "string"}},
            },
        },
    },
]


class TaskManagerAgentError(RuntimeError):
    """Task Manager Agent could not produce an accepted Surface Retrieval Plan."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskManagerAgentError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _history(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, tuple):
        raise TaskManagerAgentError("recent_history must be a tuple.")
    result: list[dict[str, str]] = []
    for item in value[-6:]:
        if not isinstance(item, Mapping):
            raise TaskManagerAgentError("recent_history entries must be objects.")
        role, text = item.get("role"), item.get("text")
        if role not in {"user", "assistant"} or not isinstance(text, str) or not text.strip():
            raise TaskManagerAgentError("recent_history entries require role and non-empty text.")
        result.append({"role": role, "text": text.strip()[:1200]})
    return tuple(result)


@dataclass(frozen=True, slots=True)
class TaskManagerAgentRequest:
    """Backend-owned context for one request delegated by Gemini Live."""

    request: str
    recent_history: tuple[dict[str, str], ...] = ()
    active_surface_summary: Mapping[str, Any] | None = None
    compiler_feedback: Mapping[str, Any] | None = None
    accepted_program: ProgramSource | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", _text(self.request, "request"))
        object.__setattr__(self, "recent_history", _history(self.recent_history))
        for field_name in ("active_surface_summary", "compiler_feedback"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, Mapping):
                raise TaskManagerAgentError(f"{field_name} must be an object when provided.")
            if value is not None:
                object.__setattr__(self, field_name, dict(value))


class TaskManagerAgent:
    """Run a constrained describe-tool loop and return a Surface Retrieval Plan."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        widget_registry: WidgetRegistry,
        template_catalog: TemplateCatalog,
        draft_compiler: DraftSurfaceCompiler,
        client_factory: ClientFactory | None = None,
        max_planning_turns: int = MAX_PLANNING_TURNS,
        task_catalog: tuple[Mapping[str, str], ...] = _TASK_CATALOG,
        processor: OpenUIProcessor | None = None,
    ) -> None:
        if not isinstance(api_key, str):
            raise TaskManagerAgentError("Cerebras API key must be a string.")
        self._api_key = api_key.strip()
        self._model = _text(model, "CEREBRAS_MANAGER_MODEL")
        if not isinstance(widget_registry, WidgetRegistry):
            raise TaskManagerAgentError("widget_registry must be a WidgetRegistry.")
        if not isinstance(template_catalog, TemplateCatalog):
            raise TaskManagerAgentError("template_catalog must be a TemplateCatalog.")
        if not isinstance(draft_compiler, DraftSurfaceCompiler):
            raise TaskManagerAgentError("draft_compiler must be a DraftSurfaceCompiler.")
        if isinstance(max_planning_turns, bool) or not isinstance(max_planning_turns, int) or max_planning_turns < 1:
            raise TaskManagerAgentError("max_planning_turns must be a positive integer.")
        if not task_catalog or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("task_type"), str)
            or not item["task_type"].strip()
            for item in task_catalog
        ):
            raise TaskManagerAgentError("task_catalog must contain task_type entries.")
        self._widget_registry = widget_registry
        self._template_catalog = template_catalog
        self._draft_compiler = draft_compiler
        self._client_factory = client_factory
        self._max_planning_turns = max_planning_turns
        self._task_catalog = tuple(dict(item) for item in task_catalog)
        self._processor = processor or OpenUIProcessor()

    @classmethod
    def from_settings(cls, *, settings: Any, registries: Any, client_factory: ClientFactory | None = None) -> "TaskManagerAgent":
        return cls(
            api_key=getattr(settings, "cerebras_api_key", ""),
            model=getattr(settings, "cerebras_manager_model", ""),
            widget_registry=registries.widget_registry,
            template_catalog=registries.template_catalog(),
            draft_compiler=DraftSurfaceCompiler(registries.widget_registry, registries.asset_catalog()),
            client_factory=client_factory,
            task_catalog=registries.task_catalog(settings),
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def task_catalog(self) -> tuple[dict[str, str], ...]:
        """Return a defensive copy of task types published to this Agent."""

        return tuple(dict(item) for item in self._task_catalog)

    async def warmup(self) -> None:
        """Start the local processor before accepting user requests; no model call."""
        started = time.perf_counter()
        await self._processor.call('describe_runtime')
        logger.info(
            "[TASK_MANAGER_AGENT:PROCESSOR_READY] duration_ms=%s",
            round((time.perf_counter() - started) * 1000),
        )

    async def plan(
        self,
        request: TaskManagerAgentRequest,
        *,
        on_stream: StreamCallback | None = None,
    ) -> SurfaceRetrievalPlan:
        if not isinstance(request, TaskManagerAgentRequest):
            raise TaskManagerAgentError("plan requires a TaskManagerAgentRequest.")
        if not self._api_key:
            raise TaskManagerAgentError("CEREBRAS_API_KEY is not configured for Task Manager Agent.")

        plan_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        mode = "edit" if request.accepted_program else "create"
        logger.info(
            "[TASK_MANAGER_AGENT:PLAN_START] plan=%s model=%s mode=%s request=%r",
            plan_id, self._model, mode, request.request,
        )
        if request.accepted_program:
            logger.info(
                "[TASK_MANAGER_AGENT:ACCEPTED_PROGRAM] plan=%s source=\n%s",
                plan_id, request.accepted_program.source,
            )
        payload = {
            "request": request.request,
            "recent_history": list(request.recent_history),
            "active_surface_summary": request.active_surface_summary,
            "compiler_feedback": request.compiler_feedback,
            "accepted_program": request.accepted_program.source if request.accepted_program else None,
            "widget_catalog": list(self._widget_registry.widget_index()),
            "template_catalog": [
                {"id": entry.id, "description": entry.description}
                for entry in self._template_catalog.entries
            ],
            "task_catalog": list(self._task_catalog),
        }
        try:
            runtime = await self._processor.call('describe_runtime')
        except OpenUIProcessorError as exc:
            logger.exception("[TASK_MANAGER_AGENT:PROCESSOR_FAILED] plan=%s phase=describe_runtime", plan_id)
            raise TaskManagerAgentError(str(exc)) from exc
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": TASK_MANAGER_AGENT_PROMPT + '\n\n' + runtime['core_prompt']},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        client = self._client()
        described_widgets: set[str] = set()
        for turn in range(1, self._max_planning_turns + 1):
            logger.info(
                "[TASK_MANAGER_AGENT:MODEL_CALL] plan=%s turn=%s/%s described_widgets=%s",
                plan_id, turn, self._max_planning_turns, sorted(described_widgets),
            )
            model_started = time.perf_counter()
            response = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=_TOOLS,
                # Match OpenUI's server-side Chat Completions tool loop: discovery
                # remains available until the final budgeted round, which must
                # produce assistant text rather than another unexecutable tool call.
                tool_choice="none" if turn == self._max_planning_turns else "auto",
                parallel_tool_calls=True,
                reasoning_effort="low",
                stream=True,
                stream_options={"include_usage": True},
            )
            completion = await _collect_completion_stream(
                response,
                on_stream=on_stream if request.accepted_program is None else None,
                plan_id=plan_id,
                turn=turn,
                model_started=model_started,
            )
            logger.info(
                "[LLM:CALL_COMPLETE] agent=task_manager plan=%s call=%s elapsed_ms=%.1f",
                plan_id, turn, (time.perf_counter() - model_started) * 1000,
            )
            log_token_usage(
                agent="task_manager", model=self._model, api="cerebras",
                usage=getattr(completion, "usage", None), plan=plan_id, call=turn,
            )
            message = _first_message(completion)
            calls = tuple(getattr(message, "tool_calls", None) or ())
            if calls:
                messages.append(_assistant_tool_message(message))
                for call in calls:
                    function = getattr(call, "function", None)
                    logger.info(
                        "[TASK_MANAGER_AGENT:DISCOVERY] plan=%s turn=%s name=%s arguments=%s",
                        plan_id, turn, getattr(function, "name", None),
                        getattr(function, "arguments", None),
                    )
                    response, described_widget = self._execute_tool_call(call)
                    if described_widget is not None:
                        described_widgets.add(described_widget)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": getattr(call, "id", None),
                        "content": json.dumps(response, ensure_ascii=False),
                    })
                continue
            content = getattr(message, "content", None)
            if not isinstance(content, str) or not content.strip():
                logger.warning("[TASK_MANAGER_AGENT:EMPTY_OUTPUT] plan=%s turn=%s", plan_id, turn)
                raise TaskManagerAgentError("Task Manager Agent returned neither tool calls nor an OpenUI program.")
            logger.info(
                "[TASK_MANAGER_AGENT:CANDIDATE] plan=%s turn=%s mode=%s source=\n%s",
                plan_id, turn, mode, content,
            )
            try:
                result = (await self._processor.call('merge_patch', source=request.accepted_program.source, patch=content)
                          if request.accepted_program else await self._processor.parse_program(content))
                plan = SurfaceRetrievalPlan.from_dict(result['plan'])
                self._validate_plan(plan)
                bindings = result['bindings']
                plan = replace(plan, openui_program=ProgramSource(
                    source=result['source'], slots=tuple(bindings['slots']), used_widgets=tuple(bindings['used_widgets']),
                    tasks=tuple(bindings['tasks']), base_source=request.accepted_program.source if request.accepted_program else None,
                    stage_slots=tuple(dict(slot) for slot in result['stage']['slots']),
                    state_updates=result.get('state_updates', {}),
                    prop_updates={slot: tuple(keys) for slot, keys in result.get('prop_updates', {}).items()}))
            except OpenUIProcessorError as exc:
                logger.exception("[TASK_MANAGER_AGENT:PROCESSOR_FAILED] plan=%s turn=%s", plan_id, turn)
                raise TaskManagerAgentError(str(exc)) from exc
            except (OpenUIProgramError, TaskContractError, DraftCompilationError, TaskManagerAgentError) as exc:
                logger.warning(
                    "[TASK_MANAGER_AGENT:VALIDATION_REJECTED] plan=%s turn=%s error_type=%s reason=%s",
                    plan_id, turn, type(exc).__name__, exc,
                )
                messages.extend(_feedback_messages(message, exc, edit=request.accepted_program is not None))
                await _emit_stream(on_stream, {
                    'type': 'abort', 'stream_id': f'{plan_id}:{turn}', 'reason': 'validation_rejected',
                })
                continue
            logger.info(
                "[TASK_MANAGER_AGENT:PLAN_READY] turn=%s model=%s blocks=%s tasks=%s plan=%s duration_ms=%s",
                turn,
                self._model,
                len(plan.blocks),
                len(plan.tasks),
                plan_id,
                round((time.perf_counter() - started) * 1000),
            )
            logger.info(
                "[TASK_MANAGER_AGENT:VALIDATED_PROGRAM] plan=%s source=\n%s",
                plan_id, result['source'],
            )
            return plan
        logger.warning(
            "[TASK_MANAGER_AGENT:TURN_LIMIT] plan=%s turns=%s duration_ms=%s",
            plan_id, self._max_planning_turns, round((time.perf_counter() - started) * 1000),
        )
        raise TaskManagerAgentError("Task Manager Agent exceeded the plan correction turn limit.")

    async def close(self) -> None:
        await self._processor.close()

    def _validate_plan(self, plan: SurfaceRetrievalPlan) -> None:
        declared_task_types = {item["task_type"] for item in self._task_catalog}
        unsupported_task_types = {task.task_type for task in plan.tasks} - declared_task_types
        if unsupported_task_types:
            raise TaskManagerAgentError(
                "tasks use a task_type absent from task_catalog: "
                + ", ".join(sorted(unsupported_task_types))
                + "."
            )
        targeted_slots = {
            slot
            for task in plan.tasks
            for slot in task.target_slots
        }
        placeholder_paths = [
            path
            for block_index, block in enumerate(plan.blocks)
            if block.slot_role in targeted_slots
            for path in _fake_placeholder_paths(
                block.props,
                f"surface.blocks[{block_index}].props",
            )
        ]
        if placeholder_paths:
            raise TaskManagerAgentError(
                "task-bound props contain fake placeholder text at "
                + ", ".join(placeholder_paths)
                + "; use empty props or an empty structural skeleton instead."
            )
        self._draft_compiler.compile(SurfaceDraft(blocks=plan.blocks))

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(api_key=self._api_key, base_url="https://api.cerebras.ai/v1")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - environment setup boundary.
            raise TaskManagerAgentError("Task Manager Agent requires the openai package.") from exc
        return AsyncOpenAI(api_key=self._api_key, base_url="https://api.cerebras.ai/v1")

    def _execute_tool_call(self, call: Any) -> tuple[dict[str, Any], str | None]:
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        raw_arguments = getattr(function, "arguments", "{}")
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError as exc:
            raise TaskManagerAgentError(f"Task Manager Agent tool arguments are invalid JSON: {exc}") from exc
        if not isinstance(arguments, Mapping):
            raise TaskManagerAgentError("Task Manager Agent tool arguments must be an object.")
        if name == "describe_widget":
            widget_id = _text(arguments.get("widget_id"), "describe_widget.widget_id")
            try:
                widget = self._widget_registry.get(widget_id)
            except WidgetPropsError as exc:
                raise TaskManagerAgentError(str(exc)) from exc
            try:
                definitions = json.loads((Path(__file__).resolve().parents[1] / 'build/openui/widget-definitions.json').read_text(encoding='utf-8'))
                definition = definitions[widget.widget_id]
            except (OSError, ValueError, KeyError) as exc:
                raise TaskManagerAgentError('OpenUI widget definitions unavailable; rebuild frontend.') from exc
            return {
                "widget": {
                    "id": widget.widget_id,
                    "purpose": widget.purpose,
                    "contract": widget.public_contract(),
                    "openui": definition,
                }
            }, widget.widget_id
        if name == "describe_template":
            template_id = _text(arguments.get("template_id"), "describe_template.template_id")
            try:
                template = self._template_catalog.load_layout_template(template_id)
            except TemplateCatalogError as exc:
                raise TaskManagerAgentError(str(exc)) from exc
            return {
                "template_id": template.template_id,
                "description": template.description,
                "semantic_spec": template.semantic_spec.to_dict(),
                "blocks": [block.to_dict() for block in template.blocks],
                "bindings": [binding.to_dict() for binding in template.bindings],
            }, None
        raise TaskManagerAgentError(f"Task Manager Agent called unsupported tool '{name}'.")


async def _emit_stream(callback: StreamCallback | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback(event)
    if inspect.isawaitable(result):
        await result


async def _collect_completion_stream(
    response: Any,
    *,
    on_stream: StreamCallback | None,
    plan_id: str,
    turn: int,
    model_started: float | None = None,
) -> Any:
    """Collect one provider turn and expose only content-only turns as OpenUI.

    Provider tool-call fragments are agent-side protocol, not OpenUI Lang.  A
    standard tool turn starts with ``delta.tool_calls`` and stays entirely on the
    server.  If a provider violates that separation by starting text before a
    later tool call, abort the preview and discard that text from tool history.
    """
    if not hasattr(response, '__aiter__'):
        message = _first_message(response)
        content = getattr(message, 'content', None)
        calls = tuple(getattr(message, 'tool_calls', None) or ())
        if not calls and on_stream is not None and isinstance(content, str) and content:
            stream_id = f'{plan_id}:{turn}'
            await _emit_stream(on_stream, {'type': 'start', 'stream_id': stream_id})
            await _emit_stream(on_stream, {'type': 'delta', 'stream_id': stream_id, 'sequence': 1, 'delta': content})
        return response

    from types import SimpleNamespace

    content_parts: list[str] = []
    tool_parts: dict[int, dict[str, str]] = {}
    usage = None
    stream_id = f'{plan_id}:{turn}'
    sequence = 0
    started = False
    tool_turn = False
    aborted = False
    first_output_logged = False
    first_tool_call_logged = False
    async for chunk in response:
        usage = getattr(chunk, 'usage', None) or usage
        choices = getattr(chunk, 'choices', None) or ()
        if not choices:
            continue
        delta = getattr(choices[0], 'delta', None)
        if delta is None:
            continue
        delta_calls = tuple(getattr(delta, 'tool_calls', None) or ())
        if delta_calls:
            if not first_tool_call_logged and model_started is not None:
                first_tool_call_logged = True
                logger.info(
                    "[LLM:FIRST_TOOL_CALL_CHUNK] agent=task_manager plan=%s call=%s elapsed_ms=%.1f",
                    plan_id, turn, (time.perf_counter() - model_started) * 1000,
                )
            tool_turn = True
            if started and not aborted:
                aborted = True
                await _emit_stream(on_stream, {
                    'type': 'abort', 'stream_id': stream_id, 'reason': 'discovery_tool_call',
                })
            for call in delta_calls:
                index = getattr(call, 'index', None)
                index = index if isinstance(index, int) else len(tool_parts)
                part = tool_parts.setdefault(index, {'id': '', 'name': '', 'arguments': ''})
                call_id = getattr(call, 'id', None)
                function = getattr(call, 'function', None)
                if isinstance(call_id, str):
                    part['id'] += call_id
                name = getattr(function, 'name', None)
                arguments = getattr(function, 'arguments', None)
                if isinstance(name, str):
                    part['name'] += name
                if isinstance(arguments, str):
                    part['arguments'] += arguments
        text = getattr(delta, 'content', None)
        if isinstance(text, str) and text:
            if not first_output_logged and model_started is not None:
                first_output_logged = True
                logger.info(
                    "[LLM:FIRST_OUTPUT_CHUNK] agent=task_manager plan=%s call=%s ttft_ms=%.1f",
                    plan_id, turn, (time.perf_counter() - model_started) * 1000,
                )
            # Some compatible providers attach explanatory text to a tool turn.
            # It is neither tool history nor renderable OpenUI and must not escape.
            if tool_turn:
                continue
            if not started:
                started = True
                await _emit_stream(on_stream, {'type': 'start', 'stream_id': stream_id})
            content_parts.append(text)
            sequence += 1
            await _emit_stream(on_stream, {
                'type': 'delta', 'stream_id': stream_id, 'sequence': sequence, 'delta': text,
            })
    calls = [SimpleNamespace(
        id=part['id'], type='function',
        function=SimpleNamespace(name=part['name'], arguments=part['arguments'] or '{}'),
    ) for _, part in sorted(tool_parts.items())]
    message = SimpleNamespace(
        content=None if calls else ''.join(content_parts) or None,
        tool_calls=calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=usage,
    )


def _first_message(completion: Any) -> Any:
    choices = getattr(completion, "choices", None) or ()
    if not choices:
        raise TaskManagerAgentError("Cerebras returned no Task Manager Agent choice.")
    return getattr(choices[0], "message", None)


def _assistant_tool_message(message: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        # Tool rounds remain server-side.  OpenUI Lang belongs to a later,
        # content-only assistant turn and must never be replayed as tool preamble.
        "content": None,
        "tool_calls": [
            {
                "id": getattr(call, "id", None),
                "type": "function",
                "function": {
                    "name": getattr(getattr(call, "function", None), "name", None),
                    "arguments": getattr(getattr(call, "function", None), "arguments", "{}"),
                },
            }
            for call in tuple(getattr(message, "tool_calls", None) or ())
        ],
    }


def _feedback_messages(message: Any, error: Exception, *, edit: bool = False) -> list[dict[str, Any]]:
    if isinstance(error, OpenUIProgramError):
        errors = [error.error]
    else:
        issues = error.issues if isinstance(error, DraftCompilationError) else (str(error),)
        errors = [{"code": type(error).__name__, "message": issue} for issue in issues]
    feedback = {
        "type": "surface_validation_feedback",
        "errors": errors,
        "instruction": "Sửa toàn bộ lỗi và trả lại chương trình OpenUI Lang tạo mới hoàn chỉnh (root và các statement được tham chiếu). Không trả JSON hoặc markdown. Chỉ gọi describe_widget nếu cần thêm định nghĩa chưa có.",
    }
    if edit:
        feedback['instruction'] = 'Sửa toàn bộ lỗi và trả đầy đủ patch ứng viên OpenUI trên accepted_program; patch lỗi không là bản nền. Không trả toàn chương trình, JSON hoặc markdown. Giữ lịch sử discovery.'
    return [
        {"role": "assistant", "content": getattr(message, "content", None)},
        {"role": "user", "content": json.dumps(feedback, ensure_ascii=False)},
    ]


def _fake_placeholder_paths(value: object, path: str) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return tuple(
            found
            for key, child in value.items()
            for found in _fake_placeholder_paths(child, f"{path}.{key}")
        )
    if isinstance(value, list):
        return tuple(
            found
            for index, child in enumerate(value)
            for found in _fake_placeholder_paths(child, f"{path}[{index}]")
        )
    if isinstance(value, str) and value.strip().casefold() in _FAKE_PLACEHOLDER_TEXTS:
        return (path,)
    return ()
