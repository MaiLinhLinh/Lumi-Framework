"""Runtime-owned fallback policies evaluated before Artifact commit.

Policies decide whether an executor result is usable and, when appropriate,
describe one fallback task.  They do not persist Artifacts, mutate the Task
Ledger, inspect provider responses, or read Async Task manifests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .contracts import TaskRecord


ACCEPT = "ACCEPT"
REJECT = "REJECT"
REJECT_AND_FALLBACK = "REJECT_AND_FALLBACK"
SEARCH_IMAGE_TASK = "search_image_task"
DRAW_IMAGE_TASK = "draw_image_task"


class FallbackPolicyError(RuntimeError):
    """A fallback policy or registry declaration is invalid."""


@dataclass(frozen=True, slots=True)
class FallbackTaskRequest:
    """Runtime task requested by a policy after rejecting a source outcome."""

    task_type: str
    instruction: str

    def __post_init__(self) -> None:
        if not isinstance(self.task_type, str) or not self.task_type.strip():
            raise FallbackPolicyError("fallback task_type must be a non-empty string.")
        if not isinstance(self.instruction, str) or not self.instruction.strip():
            raise FallbackPolicyError("fallback instruction must be a non-empty string.")
        object.__setattr__(self, "task_type", self.task_type.strip())
        object.__setattr__(self, "instruction", self.instruction.strip())


@dataclass(frozen=True, slots=True)
class FallbackDecision:
    """Policy result consumed generically by Task Manager."""

    action: str
    error_code: str | None = None
    fallback: FallbackTaskRequest | None = None

    def __post_init__(self) -> None:
        if self.action not in {ACCEPT, REJECT, REJECT_AND_FALLBACK}:
            raise FallbackPolicyError(f"unsupported fallback action '{self.action}'.")
        if self.error_code is not None:
            if not isinstance(self.error_code, str) or not self.error_code.strip():
                raise FallbackPolicyError("error_code must be a non-empty string when provided.")
            object.__setattr__(self, "error_code", self.error_code.strip())
        if self.action == REJECT_AND_FALLBACK and self.fallback is None:
            raise FallbackPolicyError(
                "REJECT_AND_FALLBACK requires a fallback task request."
            )
        if self.action != REJECT_AND_FALLBACK and self.fallback is not None:
            raise FallbackPolicyError(
                "only REJECT_AND_FALLBACK may contain a fallback task request."
            )


class FallbackPolicy(Protocol):
    """Evaluate one terminal executor outcome without side effects."""

    def evaluate(
        self,
        task: TaskRecord,
        *,
        status: str,
        metrics: Mapping[str, object],
    ) -> FallbackDecision: ...


class FallbackPolicyRegistry:
    """Map task types to Runtime policies independently of task manifests."""

    def __init__(self) -> None:
        self._policies: dict[str, FallbackPolicy] = {}

    def register(self, task_type: str, policy: FallbackPolicy) -> None:
        if not isinstance(task_type, str) or not task_type.strip():
            raise FallbackPolicyError("task_type must be a non-empty string.")
        normalized = task_type.strip()
        if normalized in self._policies:
            raise FallbackPolicyError(
                f"fallback policy for task type '{normalized}' is already registered."
            )
        if not callable(getattr(policy, "evaluate", None)):
            raise FallbackPolicyError("fallback policy must provide evaluate().")
        self._policies[normalized] = policy

    def evaluate(
        self,
        task: TaskRecord,
        *,
        status: str,
        metrics: Mapping[str, object] | None = None,
    ) -> FallbackDecision:
        policy = self._policies.get(task.task_type)
        if policy is None:
            return FallbackDecision(ACCEPT if status.strip().upper() == "SUCCEEDED" else REJECT)
        return policy.evaluate(task, status=status, metrics=metrics or {})


class SearchImageFallbackPolicy:
    """Accept only normalized usable images; otherwise request one draw task."""

    def evaluate(
        self,
        task: TaskRecord,
        *,
        status: str,
        metrics: Mapping[str, object],
    ) -> FallbackDecision:
        normalized_status = status.strip().upper()
        if normalized_status == "SUCCEEDED" and _has_usable_image(metrics):
            return FallbackDecision(ACCEPT)

        # A fallback task is terminal for this chain.  This guard also prevents
        # a malformed Runtime-created search fallback from producing a loop.
        if (
            task.fallback_of_task_id is not None
            or task.evidence_ref is None
            or task.instruction is None
        ):
            return FallbackDecision(
                REJECT,
                error_code="NO_USABLE_RESOURCE" if normalized_status == "SUCCEEDED" else None,
            )

        return FallbackDecision(
            REJECT_AND_FALLBACK,
            error_code="NO_USABLE_RESOURCE" if normalized_status == "SUCCEEDED" else None,
            fallback=FallbackTaskRequest(
                task_type=DRAW_IMAGE_TASK,
                instruction=(
                    "Vẽ một ảnh minh họa phù hợp cho yêu cầu sau: "
                    f"{task.instruction}"
                ),
            ),
        )


def build_default_fallback_policy_registry() -> FallbackPolicyRegistry:
    """Return the Runtime policy set; manifests never configure these rules."""

    registry = FallbackPolicyRegistry()
    registry.register(SEARCH_IMAGE_TASK, SearchImageFallbackPolicy())
    return registry


def _has_usable_image(metrics: Mapping[str, object]) -> bool:
    count = metrics.get("usable_resource_count")
    kinds = metrics.get("usable_resource_kinds")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return False
    if not isinstance(kinds, (list, tuple, set, frozenset)):
        return False
    return any(isinstance(kind, str) and kind.strip().lower() == "image" for kind in kinds)


__all__ = [
    "ACCEPT",
    "REJECT",
    "REJECT_AND_FALLBACK",
    "FallbackDecision",
    "FallbackPolicy",
    "FallbackPolicyError",
    "FallbackPolicyRegistry",
    "FallbackTaskRequest",
    "SearchImageFallbackPolicy",
    "build_default_fallback_policy_registry",
]
