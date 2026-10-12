"""Shared contracts for built-in and manifest-defined Agent Tasks."""

from .contracts import (
    AgentFrameworkContext,
    AgentTaskContractError,
    AgentTaskIdentity,
    AgentTaskRequest,
)
from .request_factory import AgentTaskRequestFactory
from .tool_registry import (
    SEARCH_LOCAL_MEDIA,
    SEARCH_LOCAL_TEXT,
    AgentToolDefinition,
    AgentToolRegistry,
    AgentToolRegistryError,
    build_default_agent_tool_registry,
)

__all__ = [
    "AgentFrameworkContext",
    "AgentTaskContractError",
    "AgentTaskIdentity",
    "AgentTaskRequest",
    "AgentTaskRequestFactory",
    "AgentToolDefinition",
    "AgentToolRegistry",
    "AgentToolRegistryError",
    "SEARCH_LOCAL_MEDIA",
    "SEARCH_LOCAL_TEXT",
    "build_default_agent_tool_registry",
]
