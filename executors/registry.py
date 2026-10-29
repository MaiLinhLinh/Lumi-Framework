"""Explicit task-executor capability registration.

This registry is intentionally separate from scheduling. It answers whether a
task type is supported and which executor owns it. It does not create or run
tasks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Iterable
from typing import TYPE_CHECKING, Protocol

from lumi_openui.task_manager.contracts import TaskResult

if TYPE_CHECKING:
    from lumi_openui.task_manager.execution import TaskExecutionContext


SEARCH_INFORMATION_TASK = "search_information_task"
SEARCH_IMAGE_TASK = "search_image_task"
DRAW_IMAGE_TASK = "draw_image_task"
COMPLETE_SURFACE_TASK = "complete_surface_task"

DEFAULT_TASK_TYPES = (
    SEARCH_INFORMATION_TASK,
    SEARCH_IMAGE_TASK,
    DRAW_IMAGE_TASK,
    COMPLETE_SURFACE_TASK,
)


class ExecutorRegistryError(RuntimeError):
    """A task type or executor binding is invalid."""


class TaskExecutor(Protocol):
    """The async boundary used by Task Manager to execute one record."""

    def __call__(self, context: TaskExecutionContext) -> Awaitable[TaskResult]: ...


def _task_type(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutorRegistryError("task_type must be a non-empty string.")
    return value.strip()


class ExecutorRegistry:
    """Own supported task types and their optional concrete executors.

    A declaration is sufficient for plan validation. Execution additionally
    requires a concrete binding.
    """

    def __init__(self) -> None:
        self._executors: dict[str, TaskExecutor | None] = {}

    def declare(self, task_type: str) -> None:
        """Declare one supported task type without an implementation yet."""

        normalized = _task_type(task_type)
        if normalized in self._executors:
            raise ExecutorRegistryError(f"task type '{normalized}' is already declared.")
        self._executors[normalized] = None

    def bind(self, task_type: str, executor: TaskExecutor) -> None:
        """Bind the checkpoint-specific implementation for a declared type."""

        normalized = _task_type(task_type)
        if normalized not in self._executors:
            raise ExecutorRegistryError(f"task type '{normalized}' is not declared.")
        if not callable(executor):
            raise ExecutorRegistryError("executor must be an async callable.")
        if self._executors[normalized] is not None:
            raise ExecutorRegistryError(f"task type '{normalized}' already has an executor.")
        self._executors[normalized] = executor

    def supports(self, task_type: str) -> bool:
        """Return whether the task type is declared for this framework."""

        return _task_type(task_type) in self._executors

    def is_bound(self, task_type: str) -> bool:
        """Return whether a concrete executor is available for the type."""

        normalized = _task_type(task_type)
        return normalized in self._executors and self._executors[normalized] is not None

    def resolve(self, task_type: str) -> TaskExecutor:
        """Return a concrete executor, rejecting unbound declarations."""

        normalized = _task_type(task_type)
        if normalized not in self._executors:
            raise ExecutorRegistryError(f"unknown task type '{normalized}'.")
        executor = self._executors[normalized]
        if executor is None:
            raise ExecutorRegistryError(
                f"task type '{normalized}' is declared but has no bound executor."
            )
        return executor

    def task_types(self) -> tuple[str, ...]:
        """Declared task types in stable registration order."""

        return tuple(self._executors)


def build_default_executor_registry(
    *,
    enabled_task_types: Iterable[str] | None = None,
) -> ExecutorRegistry:
    """Declare the task types enabled for one runtime instance.

    The default preserves the complete framework capability set. A runtime can
    opt out of a temporarily disabled capability before Task Manager accepts
    a Surface Retrieval Plan that needs it.
    """

    registry = ExecutorRegistry()
    task_types = DEFAULT_TASK_TYPES if enabled_task_types is None else tuple(enabled_task_types)
    for task_type in task_types:
        registry.declare(task_type)
    return registry
