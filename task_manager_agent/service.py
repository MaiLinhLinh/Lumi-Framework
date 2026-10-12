"""Task Manager Agent: Plan-Agent-style surface planning before Runtime."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from final_framework.catalogs.templates import TemplateCatalog, TemplateCatalogError
from final_framework.surface import DraftCompilationError, DraftSurfaceCompiler, SurfaceDraft
from final_framework.task_catalog import STATIC_TASK_CATALOG
from final_framework.task_manager import SurfaceRetrievalPlan, TaskContractError
from final_framework.widgets import WidgetPropsError, WidgetRegistry

from .prompts import TASK_MANAGER_AGENT_PROMPT


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

    async def plan(self, request: TaskManagerAgentRequest) -> SurfaceRetrievalPlan:
        if not isinstance(request, TaskManagerAgentRequest):
            raise TaskManagerAgentError("plan requires a TaskManagerAgentRequest.")
        if not self._api_key:
            raise TaskManagerAgentError("CEREBRAS_API_KEY is not configured for Task Manager Agent.")

        payload = {
            "request": request.request,
            "recent_history": list(request.recent_history),
            "active_surface_summary": request.active_surface_summary,
            "compiler_feedback": request.compiler_feedback,
            "widget_catalog": list(self._widget_registry.widget_index()),
            "template_catalog": [
                {"id": entry.id, "description": entry.description}
                for entry in self._template_catalog.entries
            ],
            "task_catalog": list(self._task_catalog),
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": TASK_MANAGER_AGENT_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        client = self._client()
        described_widgets: set[str] = set()
        for turn in range(1, self._max_planning_turns + 1):
            completion = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=_TOOLS,
                tool_choice="auto",
                parallel_tool_calls=True,
                reasoning_effort="low",
            )
            message = _first_message(completion)
            calls = tuple(getattr(message, "tool_calls", None) or ())
            if calls:
                messages.append(_assistant_tool_message(message))
                for call in calls:
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
                raise TaskManagerAgentError("Task Manager Agent returned neither tool calls nor plan JSON.")
            try:
                plan = SurfaceRetrievalPlan.from_dict(json.loads(content))
                self._validate_plan(plan, described_widgets)
            except (json.JSONDecodeError, TaskContractError, DraftCompilationError, TaskManagerAgentError) as exc:
                messages.extend(_feedback_messages(message, str(exc)))
                continue
            logger.info(
                "[TASK_MANAGER_AGENT:PLAN_READY] turn=%s model=%s blocks=%s tasks=%s",
                turn,
                self._model,
                len(plan.blocks),
                len(plan.tasks),
            )
            return plan
        raise TaskManagerAgentError("Task Manager Agent exceeded the plan correction turn limit.")

    def _validate_plan(self, plan: SurfaceRetrievalPlan, described_widgets: set[str]) -> None:
        declared_task_types = {item["task_type"] for item in self._task_catalog}
        unsupported_task_types = {task.task_type for task in plan.tasks} - declared_task_types
        if unsupported_task_types:
            raise TaskManagerAgentError(
                "tasks use a task_type absent from task_catalog: "
                + ", ".join(sorted(unsupported_task_types))
                + "."
            )
        used_widget_ids = {
            widget_id
            for block in plan.blocks
            for widget_id in (block.widget_id, *(child.widget_id for child in block.children))
        }
        missing_descriptions = used_widget_ids - described_widgets
        if missing_descriptions:
            raise TaskManagerAgentError(
                "widgets must be described before use: " + ", ".join(sorted(missing_descriptions)) + "."
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
            return {
                "widget": {
                    "id": widget.widget_id,
                    "purpose": widget.purpose,
                    "contract": widget.public_contract(),
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


def _first_message(completion: Any) -> Any:
    choices = getattr(completion, "choices", None) or ()
    if not choices:
        raise TaskManagerAgentError("Cerebras returned no Task Manager Agent choice.")
    return getattr(choices[0], "message", None)


def _assistant_tool_message(message: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": getattr(message, "content", None),
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


def _feedback_messages(message: Any, detail: str) -> list[dict[str, Any]]:
    feedback = {
        "type": "surface_validation_feedback",
        "errors": [{"code": "invalid_surface_retrieval_plan", "detail": detail}],
        "instruction": "Sửa toàn bộ lỗi và trả lại đúng một Surface Retrieval Plan JSON hoàn chỉnh. Không gọi tool trừ khi cần đọc thêm contract.",
    }
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
