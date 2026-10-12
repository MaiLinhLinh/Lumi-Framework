"""Build the common Agent Task request from Runtime execution context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from final_framework.task_manager import TaskExecutionContext

from .contracts import (
    AgentFrameworkContext,
    AgentTaskContractError,
    AgentTaskIdentity,
    AgentTaskRequest,
)
from .tool_registry import AgentToolRegistry, build_default_agent_tool_registry


class AgentTaskRequestFactory:
    """Build requests without exposing Surface bindings to Agent executors."""

    def __init__(self, tool_registry: AgentToolRegistry | None = None) -> None:
        self._tools = tool_registry or build_default_agent_tool_registry()
        if not isinstance(self._tools, AgentToolRegistry):
            raise TypeError("tool_registry must be an AgentToolRegistry.")

    def build(
        self,
        execution_context: TaskExecutionContext,
        *,
        task_input: Mapping[str, Any],
        knowledge_catalog: tuple[Mapping[str, Any], ...],
        allowed_tools: tuple[str, ...],
    ) -> AgentTaskRequest:
        if not isinstance(execution_context, TaskExecutionContext):
            raise TypeError("execution_context must be a TaskExecutionContext.")
        task = execution_context.task
        if task.evidence_ref is None:
            raise AgentTaskContractError(
                "Agent Task execution requires task.evidence_ref."
            )
        if task.instruction is None:
            raise AgentTaskContractError(
                "Agent Task execution requires task.instruction."
            )
        grants = self._tools.validate_grants(allowed_tools)
        return AgentTaskRequest(
            task=AgentTaskIdentity(
                run_id=execution_context.run_id,
                task_id=task.task_id,
                task_type=task.task_type,
                evidence_ref=task.evidence_ref,
                instruction=task.instruction,
            ),
            input=task_input,
            framework_context=AgentFrameworkContext(
                knowledge_catalog=knowledge_catalog,
                tool_grants=grants,
            ),
        )
