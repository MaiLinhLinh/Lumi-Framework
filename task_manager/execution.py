"""Internal input passed from Task Manager to one task executor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import SurfaceRetrievalPlan, TaskContractError, TaskRecord


@dataclass(frozen=True, slots=True)
class TaskExecutionContext:
    """Run plan and task identity; retrieval agents use task.instruction."""

    run_id: str
    task: TaskRecord
    surface_plan: SurfaceRetrievalPlan
    active_surface: Mapping[str, Any] | None = None
    evidence_task_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise TaskContractError("execution_context.run_id must be a non-empty string.")
        if not isinstance(self.task, TaskRecord):
            raise TaskContractError("execution_context.task must be a TaskRecord.")
        if self.task.run_id != self.run_id:
            raise TaskContractError("execution_context.task.run_id must match run_id.")
        if not isinstance(self.surface_plan, SurfaceRetrievalPlan):
            raise TaskContractError("execution_context.surface_plan must be a SurfaceRetrievalPlan.")
        if self.active_surface is not None and not isinstance(self.active_surface, Mapping):
            raise TaskContractError("execution_context.active_surface must be an object when provided.")
