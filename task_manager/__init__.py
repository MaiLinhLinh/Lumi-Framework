"""Public contracts and runtime entry points for Task Manager.

Contracts stay separate from lifecycle code so Gemini Live, Task Manager and
executors share one validation boundary without circular imports.
"""

from .contracts import (
    Artifact,
    RequestTaskManager,
    SurfaceRetrievalPlan,
    TaskContractError,
    TaskEvent,
    TaskManagerRequest,
    TaskRecord,
    TaskResult,
    TaskResultLifecycle,
    SurfacePlanTask,
    request_task_manager_from_call,
)
from .execution import TaskExecutionContext
from .fallback import (
    ACCEPT,
    REJECT,
    REJECT_AND_FALLBACK,
    FallbackDecision,
    FallbackPolicy,
    FallbackPolicyError,
    FallbackPolicyRegistry,
    FallbackTaskRequest,
    SearchImageFallbackPolicy,
    build_default_fallback_policy_registry,
)
__all__ = [
    "ACCEPT",
    "Artifact",
    "FallbackDecision",
    "FallbackPolicy",
    "FallbackPolicyError",
    "FallbackPolicyRegistry",
    "FallbackTaskRequest",
    "REJECT",
    "REJECT_AND_FALLBACK",
    "RequestTaskManager",
    "SurfaceRetrievalPlan",
    "TaskContractError",
    "TaskEvent",
    "TaskExecutionContext",
    "TaskManager",
    "TaskManagerError",
    "TaskManagerRequest",
    "TaskRecord",
    "TaskResult",
    "TaskResultLifecycle",
    "SearchImageFallbackPolicy",
    "SurfacePlanTask",
    "TaskRunSnapshot",
    "BARRIER_TERMINAL_STATUSES",
    "TERMINAL_STATUSES",
    "request_task_manager_from_call",
    "build_default_fallback_policy_registry",
]


def __getattr__(name: str):
    """Load lifecycle code lazily to keep shared contracts dependency-safe.

    Executor Registry and Artifact Store import contract submodules during
    framework startup.  Loading TaskManager at package-import time would make
    those foundational modules depend on each other cyclically.
    """

    lifecycle_exports = {
        "BARRIER_TERMINAL_STATUSES",
        "TERMINAL_STATUSES",
        "TaskManager",
        "TaskManagerError",
        "TaskRunSnapshot",
    }
    if name in lifecycle_exports:
        from . import manager

        return getattr(manager, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
