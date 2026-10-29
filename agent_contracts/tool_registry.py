"""Canonical knowledge-tool names and schemas exposed to Agent Tasks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


SEARCH_LOCAL_TEXT = "search_local_text"
SEARCH_LOCAL_MEDIA = "search_local_media"


class AgentToolRegistryError(ValueError):
    """A tool definition or requested grant is not registered."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class AgentToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise AgentToolRegistryError("tool.name must be a non-empty string.")
        if not isinstance(self.description, str) or not self.description.strip():
            raise AgentToolRegistryError("tool.description must be a non-empty string.")
        if not isinstance(self.input_schema, Mapping):
            raise AgentToolRegistryError("tool.input_schema must be an object.")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "input_schema", _freeze(self.input_schema))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": _thaw(self.input_schema),
        }


class AgentToolRegistry:
    """Immutable registry used by contracts before provider-specific adapters."""

    def __init__(self, definitions: tuple[AgentToolDefinition, ...]) -> None:
        by_name: dict[str, AgentToolDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, AgentToolDefinition):
                raise TypeError("AgentToolRegistry accepts AgentToolDefinition values only.")
            if definition.name in by_name:
                raise AgentToolRegistryError(
                    f"duplicate Agent Task tool '{definition.name}'."
                )
            by_name[definition.name] = definition
        self._definitions = MappingProxyType(by_name)

    def get(self, name: str) -> AgentToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise AgentToolRegistryError(
                f"unknown Agent Task tool '{name}'."
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def definitions(self) -> tuple[AgentToolDefinition, ...]:
        return tuple(self._definitions.values())

    def validate_grants(self, grants: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(grants, tuple):
            raise AgentToolRegistryError("tool grants must be a tuple of strings.")
        if len(grants) != len(set(grants)):
            raise AgentToolRegistryError("tool grants must not contain duplicates.")
        for name in grants:
            if not isinstance(name, str) or not name.strip():
                raise AgentToolRegistryError("tool grants must contain non-empty strings.")
            self.get(name)
        return grants


def _search_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["query", "package_ids"],
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 400},
            "package_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
        },
    }


def build_default_agent_tool_registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        (
            AgentToolDefinition(
                SEARCH_LOCAL_TEXT,
                "Tìm dữ liệu văn bản trong các knowledge package được chọn.",
                _search_schema(),
            ),
            AgentToolDefinition(
                SEARCH_LOCAL_MEDIA,
                "Tìm dữ liệu media trong các knowledge package được chọn.",
                _search_schema(),
            ),
        )
    )
