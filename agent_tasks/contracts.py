"""Provider-neutral request contracts shared by every Agent Task executor.

The request deliberately excludes Surface bindings such as ``target_slots``.
Those bindings remain in ``SurfaceRetrievalPlan`` and are consumed by Lumi's
Assembly after the Agent Task has produced an Artifact.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


class AgentTaskContractError(ValueError):
    """An Agent Task request violates the framework-owned public contract."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentTaskContractError(f"{field} must be a non-empty string.")
    return value.strip()


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentTaskContractError(f"{field} must be an object.")
    if not all(isinstance(key, str) for key in value):
        raise AgentTaskContractError(f"{field} keys must be strings.")
    return dict(value)


def _strings(value: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or (not allow_empty and not value):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise AgentTaskContractError(f"{field} must be {qualifier} of strings.")
    result = tuple(_text(item, f"{field}[]") for item in value)
    if len(result) != len(set(result)):
        raise AgentTaskContractError(f"{field} must not contain duplicates.")
    return result


def _freeze_json(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise AgentTaskContractError(f"{field} keys must be strings.")
        return MappingProxyType(
            {
                key: _freeze_json(child, f"{field}.{key}")
                for key, child in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(child, f"{field}[{index}]")
            for index, child in enumerate(value)
        )
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise AgentTaskContractError(f"{field} must contain JSON-compatible values only.")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _catalog(
    value: object,
    field: str = "framework_context.knowledge_catalog",
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise AgentTaskContractError(f"{field} must be an array.")
    entries: list[Mapping[str, Any]] = []
    package_ids: list[str] = []
    required = {"package_id", "description", "languages", "content_types"}
    for index, raw_entry in enumerate(value):
        entry_field = f"{field}[{index}]"
        entry = _mapping(raw_entry, entry_field)
        if set(entry) != required:
            raise AgentTaskContractError(
                f"{entry_field} must contain exactly {sorted(required)}."
            )
        package_id = _text(entry["package_id"], f"{entry_field}.package_id")
        description = _text(entry["description"], f"{entry_field}.description")
        languages = _strings(entry["languages"], f"{entry_field}.languages")
        content_types = _strings(
            entry["content_types"], f"{entry_field}.content_types"
        )
        package_ids.append(package_id)
        entries.append(
            MappingProxyType(
                {
                    "package_id": package_id,
                    "description": description,
                    "languages": languages,
                    "content_types": content_types,
                }
            )
        )
    if len(package_ids) != len(set(package_ids)):
        raise AgentTaskContractError(
            "framework_context.knowledge_catalog must not duplicate package_id."
        )
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class AgentTaskIdentity:
    """Runtime identity and natural-language instruction for one Agent Task."""

    run_id: str
    task_id: str
    task_type: str
    evidence_ref: str
    instruction: str

    def __post_init__(self) -> None:
        for name in ("run_id", "task_id", "task_type", "instruction"):
            object.__setattr__(self, name, _text(getattr(self, name), f"task.{name}"))
        evidence_ref = _text(self.evidence_ref, "task.evidence_ref")
        if not evidence_ref.startswith("#E"):
            raise AgentTaskContractError("task.evidence_ref must start with '#E'.")
        object.__setattr__(self, "evidence_ref", evidence_ref)

    @classmethod
    def from_dict(cls, value: object) -> "AgentTaskIdentity":
        data = _mapping(value, "task")
        required = {"run_id", "task_id", "task_type", "evidence_ref", "instruction"}
        if set(data) != required:
            raise AgentTaskContractError(
                f"task must contain exactly {sorted(required)}."
            )
        return cls(**data)

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "evidence_ref": self.evidence_ref,
            "instruction": self.instruction,
        }


@dataclass(frozen=True, slots=True)
class AgentFrameworkContext:
    """Knowledge discovery metadata and enforceable tool names for one task."""

    knowledge_catalog: tuple[Mapping[str, Any], ...]
    tool_grants: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "knowledge_catalog", _catalog(self.knowledge_catalog))
        object.__setattr__(
            self,
            "tool_grants",
            _strings(
                self.tool_grants,
                "framework_context.tool_grants",
                allow_empty=True,
            ),
        )

    @classmethod
    def from_dict(cls, value: object) -> "AgentFrameworkContext":
        data = _mapping(value, "framework_context")
        required = {"knowledge_catalog", "tool_grants"}
        if set(data) != required:
            raise AgentTaskContractError(
                f"framework_context must contain exactly {sorted(required)}."
            )
        return cls(
            knowledge_catalog=tuple(data["knowledge_catalog"])
            if isinstance(data["knowledge_catalog"], list)
            else data["knowledge_catalog"],
            tool_grants=tuple(data["tool_grants"])
            if isinstance(data["tool_grants"], list)
            else data["tool_grants"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_catalog": [
                _thaw_json(entry) for entry in self.knowledge_catalog
            ],
            "tool_grants": list(self.tool_grants),
        }


@dataclass(frozen=True, slots=True)
class AgentTaskRequest:
    """Provider-neutral input sent to a built-in or external Agent Task."""

    task: AgentTaskIdentity
    input: Mapping[str, Any]
    framework_context: AgentFrameworkContext

    def __post_init__(self) -> None:
        if not isinstance(self.task, AgentTaskIdentity):
            raise AgentTaskContractError("task must be an AgentTaskIdentity.")
        if not isinstance(self.framework_context, AgentFrameworkContext):
            raise AgentTaskContractError(
                "framework_context must be an AgentFrameworkContext."
            )
        object.__setattr__(
            self,
            "input",
            _freeze_json(_mapping(self.input, "input"), "input"),
        )

    @classmethod
    def from_dict(cls, value: object) -> "AgentTaskRequest":
        data = _mapping(value, "agent_task_request")
        required = {"task", "input", "framework_context"}
        if set(data) != required:
            raise AgentTaskContractError(
                f"agent_task_request must contain exactly {sorted(required)}."
            )
        return cls(
            task=AgentTaskIdentity.from_dict(data["task"]),
            input=_mapping(data["input"], "input"),
            framework_context=AgentFrameworkContext.from_dict(
                data["framework_context"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "input": _thaw_json(self.input),
            "framework_context": self.framework_context.to_dict(),
        }
