"""Canonical knowledge-tool definitions and argument validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from final_framework.agent_tasks.tool_registry import (
    SEARCH_LOCAL_MEDIA,
    SEARCH_LOCAL_TEXT,
    AgentToolDefinition,
    AgentToolRegistry,
    AgentToolRegistryError,
    build_default_agent_tool_registry,
)


class KnowledgeToolArgumentsError(ValueError):
    """A knowledge tool call does not satisfy its public JSON contract."""


# One production instance is shared by manifest validation, Gateway dispatch and,
# in later checkpoints, built-in Agent declarations and HTTP route description.
KNOWLEDGE_TOOL_REGISTRY = build_default_agent_tool_registry()


def knowledge_tool_definition(tool_name: str) -> AgentToolDefinition:
    return KNOWLEDGE_TOOL_REGISTRY.get(tool_name)


def knowledge_tool_definitions() -> tuple[AgentToolDefinition, ...]:
    return KNOWLEDGE_TOOL_REGISTRY.definitions()


def validate_knowledge_tool_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize one framework-owned knowledge tool call."""

    if not isinstance(arguments, Mapping):
        raise KnowledgeToolArgumentsError("knowledge tool arguments must be an object.")
    try:
        definition = KNOWLEDGE_TOOL_REGISTRY.get(tool_name)
    except AgentToolRegistryError as exc:
        raise KnowledgeToolArgumentsError(str(exc)) from exc
    data = dict(arguments)
    errors = sorted(
        Draft202012Validator(
            definition.to_dict()["input_schema"],
            format_checker=FormatChecker(),
        ).iter_errors(data),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        raise KnowledgeToolArgumentsError(
            f"invalid arguments for '{tool_name}': {errors[0].message}"
        )
    query = data["query"].strip()
    if not query:
        raise KnowledgeToolArgumentsError(
            f"invalid arguments for '{tool_name}': query must not be blank."
        )
    return {"query": query, "package_ids": list(data["package_ids"])}


__all__ = [
    "KNOWLEDGE_TOOL_REGISTRY",
    "KnowledgeToolArgumentsError",
    "SEARCH_LOCAL_MEDIA",
    "SEARCH_LOCAL_TEXT",
    "knowledge_tool_definition",
    "knowledge_tool_definitions",
    "validate_knowledge_tool_arguments",
]
