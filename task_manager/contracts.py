"""Validated Task Manager request, surface-plan and runtime contracts.

These types validate the natural-language Live request, the Agent surface plan
and runtime envelopes exchanged with executors. They do not execute work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from final_framework.panel.contracts import PlanBlock


class TaskContractError(ValueError):
    """A request, surface plan or runtime envelope violates its contract."""


class TaskResultLifecycle(Protocol):
    """Non-serialized transactional side effects attached to one TaskResult."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskContractError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TaskContractError(f"{field_name} must be an object.")
    return dict(value)


def _strings(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise TaskContractError(f"{field_name} must be {qualifier} of strings.")
    values = tuple(_text(item, f"{field_name}[]") for item in value)
    if len(values) != len(set(values)):
        raise TaskContractError(f"{field_name} must not contain duplicates.")
    return values


@dataclass(frozen=True, slots=True)
class TaskManagerRequest:
    """One natural-language request delegated by Gemini Live to the planning agent."""

    request: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", _text(self.request, "request"))

    @classmethod
    def from_dict(cls, value: object) -> "TaskManagerRequest":
        data = _mapping(value, "request_task_manager.arguments")
        if set(data) != {"request"}:
            raise TaskContractError(
                "request_task_manager.arguments must contain exactly the request field."
            )
        return cls(request=data["request"])

    def to_dict(self) -> dict[str, str]:
        return {"request": self.request}


@dataclass(frozen=True, slots=True)
class RequestTaskManager:
    """Validated ``request_task_manager`` function-call envelope."""

    request: TaskManagerRequest
    name: str = field(default="request_task_manager", init=False)

    @classmethod
    def from_dict(cls, value: object) -> "RequestTaskManager":
        data = _mapping(value, "function call")
        if set(data) != {"name", "arguments"}:
            raise TaskContractError("function call must contain exactly name and arguments.")
        if data["name"] != "request_task_manager":
            raise TaskContractError("function call name must be 'request_task_manager'.")
        return cls(request=TaskManagerRequest.from_dict(data["arguments"]))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.request.to_dict()}


def request_task_manager_from_call(value: object) -> RequestTaskManager:
    """Parse the minimal Gemini Live request delegated to Task Manager Agent."""

    return RequestTaskManager.from_dict(value)


@dataclass(frozen=True, slots=True)
class SurfacePlanTask:
    """One concrete Runtime task declared by a Surface Retrieval Plan."""

    evidence_ref: str
    task_type: str
    target_slots: tuple[str, ...]
    instruction: str

    def __post_init__(self) -> None:
        evidence_ref = _text(self.evidence_ref, "surface plan task.evidence_ref")
        if not evidence_ref.startswith("#E"):
            raise TaskContractError("surface plan task.evidence_ref must start with '#E'.")
        object.__setattr__(self, "evidence_ref", evidence_ref)
        object.__setattr__(self, "task_type", _text(self.task_type, "surface plan task.task_type"))
        object.__setattr__(self, "target_slots", _strings(self.target_slots, "surface plan task.target_slots"))
        object.__setattr__(self, "instruction", _text(self.instruction, "surface plan task.instruction"))

    @classmethod
    def from_dict(cls, value: object) -> "SurfacePlanTask":
        data = _mapping(value, "surface plan task")
        required = {"evidence_ref", "task_type", "target_slots", "instruction"}
        if set(data) != required:
            missing = required - set(data)
            unsupported = set(data) - required
            raise TaskContractError(
                "surface plan task is missing "
                f"{sorted(missing)} or has unsupported fields {sorted(unsupported)}."
            )
        return cls(
            evidence_ref=data["evidence_ref"],
            task_type=data["task_type"],
            target_slots=_strings(data["target_slots"], "surface plan task.target_slots"),
            instruction=data["instruction"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_ref": self.evidence_ref,
            "task_type": self.task_type,
            "target_slots": list(self.target_slots),
            "instruction": self.instruction,
        }


@dataclass(frozen=True, slots=True)
class SurfaceRetrievalPlan:
    """The Task Manager Agent output consumed by Task Manager Runtime in CP4."""

    blocks: tuple[PlanBlock, ...]
    template_description: str | None
    tasks: tuple[SurfacePlanTask, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.blocks, tuple) or not self.blocks:
            raise TaskContractError("surface retrieval plan.blocks must contain at least one PlanBlock.")
        if not all(isinstance(block, PlanBlock) for block in self.blocks):
            raise TaskContractError("surface retrieval plan.blocks must contain PlanBlock values.")
        if self.template_description is not None:
            object.__setattr__(
                self,
                "template_description",
                _text(self.template_description, "surface retrieval plan.template_description"),
            )
        if not isinstance(self.tasks, tuple) or not all(
            isinstance(task, SurfacePlanTask) for task in self.tasks
        ):
            raise TaskContractError("surface retrieval plan.tasks must contain SurfacePlanTask values.")
        evidence_refs = tuple(task.evidence_ref for task in self.tasks)
        if len(evidence_refs) != len(set(evidence_refs)):
            raise TaskContractError("surface retrieval plan.tasks must not duplicate evidence_ref.")
        slot_roles = {block.slot_role for block in self.blocks if block.slot_role is not None}
        declared_slot_roles = tuple(block.slot_role for block in self.blocks if block.slot_role is not None)
        if len(declared_slot_roles) != len(slot_roles):
            raise TaskContractError("surface retrieval plan.slot_role values must be unique.")
        target_slots = {slot for task in self.tasks for slot in task.target_slots}
        unknown_slots = target_slots - slot_roles
        if unknown_slots:
            raise TaskContractError(
                "surface plan task.target_slots must reference an existing slot_role: "
                + ", ".join(sorted(unknown_slots))
                + "."
            )
        unbound_slots = slot_roles - target_slots
        if unbound_slots:
            raise TaskContractError(
                "every slot_role must be targeted by a surface plan task: "
                + ", ".join(sorted(unbound_slots))
                + "."
            )

    @classmethod
    def from_dict(cls, value: object) -> "SurfaceRetrievalPlan":
        data = _mapping(value, "surface retrieval plan")
        required = {"action", "template_description", "surface", "tasks"}
        if set(data) != required:
            missing = required - set(data)
            unsupported = set(data) - required
            raise TaskContractError(
                "surface retrieval plan is missing "
                f"{sorted(missing)} or has unsupported fields {sorted(unsupported)}."
            )
        if data["action"] != "create_surface_plan":
            raise TaskContractError("surface retrieval plan.action must be 'create_surface_plan'.")
        surface = _mapping(data["surface"], "surface retrieval plan.surface")
        if set(surface) != {"blocks"} or not isinstance(surface["blocks"], list):
            raise TaskContractError("surface retrieval plan.surface must contain exactly an array blocks field.")
        if not isinstance(data["tasks"], list):
            raise TaskContractError("surface retrieval plan.tasks must be an array.")
        try:
            blocks = tuple(PlanBlock.from_dict(item) for item in surface["blocks"])
        except Exception as exc:
            raise TaskContractError(f"surface retrieval plan.surface.blocks is invalid: {exc}") from exc
        return cls(
            blocks=blocks,
            template_description=data["template_description"],
            tasks=tuple(SurfacePlanTask.from_dict(item) for item in data["tasks"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": "create_surface_plan",
            "template_description": self.template_description,
            "surface": {"blocks": [block.to_dict() for block in self.blocks]},
            "tasks": [task.to_dict() for task in self.tasks],
        }


@dataclass(frozen=True, slots=True)
class Artifact:
    """The one final output retained for a successful task."""

    artifact_id: str
    artifact_type: str
    run_id: str
    produced_by: str
    data: Mapping[str, Any]
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("artifact_id", "artifact_type", "run_id", "produced_by"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.evidence_ref is not None:
            reference = _text(self.evidence_ref, "artifact.evidence_ref")
            if not reference.startswith("#E"):
                raise TaskContractError("artifact.evidence_ref must start with '#E'.")
            object.__setattr__(self, "evidence_ref", reference)
        object.__setattr__(self, "data", _mapping(self.data, "artifact.data"))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the Artifact for TaskResult diagnostics or persistence."""

        result: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "run_id": self.run_id,
            "produced_by": self.produced_by,
            "data": dict(self.data),
        }
        if self.evidence_ref is not None:
            result["evidence_ref"] = self.evidence_ref
        return result


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """Runtime-owned task identity and lifecycle state; no scheduling occurs here."""

    run_id: str
    task_id: str
    task_type: str
    status: str
    attempt: int = 0
    evidence_ref: str | None = None
    instruction: str | None = None
    artifact_id: str | None = None
    error: str | None = None
    fallback_of_task_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "task_id", "task_type", "status"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise TaskContractError("task.attempt must be a non-negative integer.")
        if self.evidence_ref is not None:
            reference = _text(self.evidence_ref, "task.evidence_ref")
            if not reference.startswith("#E"):
                raise TaskContractError("task.evidence_ref must start with '#E'.")
            object.__setattr__(self, "evidence_ref", reference)
        if self.artifact_id is not None:
            object.__setattr__(self, "artifact_id", _text(self.artifact_id, "task.artifact_id"))
        if self.instruction is not None:
            object.__setattr__(self, "instruction", _text(self.instruction, "task.instruction"))
        if self.error is not None:
            object.__setattr__(self, "error", _text(self.error, "task.error"))
        if self.fallback_of_task_id is not None:
            object.__setattr__(
                self,
                "fallback_of_task_id",
                _text(self.fallback_of_task_id, "task.fallback_of_task_id"),
            )


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Executor result submitted to Task Manager in CP2 and later."""

    task_id: str
    status: str
    artifact: Artifact | None = None
    evidence_ref: str | None = None
    warnings: tuple[str, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    lifecycle: TaskResultLifecycle | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _text(self.task_id, "result.task_id"))
        object.__setattr__(self, "status", _text(self.status, "result.status"))
        if self.artifact is not None and not isinstance(self.artifact, Artifact):
            raise TaskContractError("result.artifact must be an Artifact when provided.")
        succeeded = self.status.strip().lower() == "succeeded"
        if succeeded and self.artifact is None:
            raise TaskContractError("a succeeded TaskResult must contain one artifact.")
        if not succeeded and self.artifact is not None:
            raise TaskContractError("a non-succeeded TaskResult must not contain an artifact.")
        if self.evidence_ref is not None:
            reference = _text(self.evidence_ref, "result.evidence_ref")
            if not reference.startswith("#E"):
                raise TaskContractError("result.evidence_ref must start with '#E'.")
            object.__setattr__(self, "evidence_ref", reference)
            if self.artifact is not None and self.artifact.evidence_ref != reference:
                raise TaskContractError("result artifact must use the result.evidence_ref.")
        object.__setattr__(self, "warnings", _strings(self.warnings, "result.warnings", allow_empty=True))
        object.__setattr__(self, "metrics", _mapping(self.metrics, "result.metrics"))
        if self.lifecycle is not None and (
            not callable(getattr(self.lifecycle, "commit", None))
            or not callable(getattr(self.lifecycle, "rollback", None))
        ):
            raise TaskContractError(
                "result.lifecycle must provide synchronous commit() and rollback()."
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the executor-to-Task-Manager result contract."""

        result: dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "artifact_id": self.artifact.artifact_id if self.artifact is not None else None,
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
        }
        if self.evidence_ref is not None:
            result["evidence_ref"] = self.evidence_ref
        return result


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """Task Manager notification sent to Gemini Live, never directly to Browser."""

    event: str
    run_id: str
    task_id: str
    task_type: str
    status: str
    summary: str
    evidence_ref: str | None = None
    artifact_id: str | None = None
    warnings: tuple[str, ...] = ()
    fallback_of_task_id: str | None = None
    missing_parts: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name in ("event", "run_id", "task_id", "task_type", "status", "summary"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.evidence_ref is not None:
            reference = _text(self.evidence_ref, "event.evidence_ref")
            if not reference.startswith("#E"):
                raise TaskContractError("event.evidence_ref must start with '#E'.")
            object.__setattr__(self, "evidence_ref", reference)
        if self.artifact_id is not None:
            object.__setattr__(self, "artifact_id", _text(self.artifact_id, "event.artifact_id"))
        object.__setattr__(self, "warnings", _strings(self.warnings, "event.warnings", allow_empty=True))
        if self.fallback_of_task_id is not None:
            object.__setattr__(
                self,
                "fallback_of_task_id",
                _text(self.fallback_of_task_id, "event.fallback_of_task_id"),
            )
        normalized_parts: list[dict[str, str]] = []
        if not isinstance(self.missing_parts, (list, tuple)):
            raise TaskContractError("event.missing_parts must be an array.")
        for item in self.missing_parts:
            if not isinstance(item, Mapping) or set(item) != {"description", "reason"}:
                raise TaskContractError(
                    "event.missing_parts[] must contain exactly description and reason."
                )
            normalized_parts.append({
                "description": _text(
                    item.get("description"), "event.missing_parts[].description"
                ),
                "reason": _text(item.get("reason"), "event.missing_parts[].reason"),
            })
        object.__setattr__(self, "missing_parts", tuple(normalized_parts))
