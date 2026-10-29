"""Surface Retrieval Plan lifecycle, parallel workers, events and assembly barrier.

Task Manager owns orchestration only.  It never chooses surface widgets,
search sources, or widget props; those semantic decisions remain in the task
executors introduced by later checkpoints.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from lumi_openui.artifacts import ArtifactStore
from lumi_openui.executors import (
    COMPLETE_SURFACE_TASK,
    ExecutorRegistry,
    ExecutorRegistryError,
)

from .contracts import (
    Artifact,
    SurfaceRetrievalPlan,
    TaskEvent,
    TaskRecord,
    TaskResult,
)
from .execution import TaskExecutionContext
from .fallback import (
    ACCEPT,
    REJECT_AND_FALLBACK,
    FallbackDecision,
    FallbackPolicyRegistry,
    build_default_fallback_policy_registry,
)
from lumi_openui.surface.contracts import AnchorManifest, CompiledSurface, SurfaceDraft


logger = logging.getLogger("lumi.task_manager")


TaskEventSink = Callable[[TaskEvent], Awaitable[None] | None]
BeforeSchedule = Callable[[], Awaitable[None] | None]
IdFactory = Callable[[str], str]

QUEUED = "QUEUED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCEL_REQUESTED = "CANCEL_REQUESTED"
CANCELLED = "CANCELLED"
PAUSE_REQUESTED = "PAUSE_REQUESTED"
PAUSED = "PAUSED"
SUPERSEDED = "SUPERSEDED"
RETRY_WAIT = "RETRY_WAIT"

TERMINAL_STATUSES = frozenset({SUCCEEDED, FAILED, CANCELLED, SUPERSEDED})
BARRIER_TERMINAL_STATUSES = frozenset({SUCCEEDED, FAILED, CANCELLED})


class TaskManagerError(RuntimeError):
    """A graph, lifecycle action, or executor result is invalid."""


@dataclass(slots=True)
class _RunState:
    run_id: str
    plan: SurfaceRetrievalPlan
    records: dict[str, TaskRecord]
    base_task_ids: tuple[str, ...]
    barrier_task_ids: list[str]
    draft_task_id: str
    active_surface: dict[str, Any] | None = None
    status: str = "ACTIVE"
    complete_task_id: str | None = None
    barrier_open: bool = False
    fallback_by_source: dict[str, str] = field(default_factory=dict)
    events: list[TaskEvent] = field(default_factory=list)
    updating: bool = False
    task_bindings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskRunSnapshot:
    """Read-only lifecycle state returned to the caller, never browser data."""

    run_id: str
    status: str
    records: tuple[TaskRecord, ...]
    barrier_open: bool
    complete_task_id: str | None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _status(value: str) -> str:
    return value.strip().upper()


class TaskManager:
    """Run the Agent surface plan with parallel workers and a completion barrier.

    Submission is asynchronous: all base records are queued and dispatched in
    the same event-loop turn.  A later task implementation can be bound to the
    registry without changing graph creation or barrier behavior.
    """

    def __init__(
        self,
        executor_registry: ExecutorRegistry,
        artifact_store: ArtifactStore,
        *,
        event_sink: TaskEventSink | None = None,
        id_factory: IdFactory = _new_id,
        executor_timeout_seconds: float | None = None,
        fallback_policy_registry: FallbackPolicyRegistry | None = None,
    ) -> None:
        if not isinstance(executor_registry, ExecutorRegistry):
            raise TaskManagerError("executor_registry must be an ExecutorRegistry.")
        if not isinstance(artifact_store, ArtifactStore):
            raise TaskManagerError("artifact_store must be an ArtifactStore.")
        if executor_timeout_seconds is not None and executor_timeout_seconds <= 0:
            raise TaskManagerError("executor_timeout_seconds must be positive when provided.")
        self._executor_registry = executor_registry
        self._artifact_store = artifact_store
        self._event_sink = event_sink
        self._id_factory = id_factory
        self._executor_timeout_seconds = executor_timeout_seconds
        self._fallback_policies = (
            fallback_policy_registry
            if fallback_policy_registry is not None
            else build_default_fallback_policy_registry()
        )
        if not isinstance(self._fallback_policies, FallbackPolicyRegistry):
            raise TaskManagerError(
                "fallback_policy_registry must be a FallbackPolicyRegistry."
            )
        self._runs: dict[str, _RunState] = {}
        self._handles: dict[tuple[str, str], asyncio.Task[None]] = {}


    async def submit_surface_plan(
        self,
        plan: SurfaceRetrievalPlan,
        *,
        draft: SurfaceDraft,
        compiled_draft: CompiledSurface | None = None,
        surface_id: str | None = None,
        active_surface: dict[str, Any] | None = None,
        before_schedule: BeforeSchedule | None = None,
    ) -> TaskRunSnapshot:
        """Accept the Agent plan and record its already-compiled Draft.

        The caller has already compiled the exact Draft for early presentation.
        Runtime records that Draft as a completed, non-executable task artifact,
        then schedules only the concrete tasks declared by the Agent.
        """

        if not isinstance(plan, SurfaceRetrievalPlan):
            raise TaskManagerError("plan must be a SurfaceRetrievalPlan.")
        if not isinstance(draft, SurfaceDraft):
            raise TaskManagerError("draft must be a SurfaceDraft.")
        if draft.blocks != plan.blocks:
            raise TaskManagerError("draft.blocks must exactly match surface plan blocks.")
        if compiled_draft is not None:
            if not isinstance(compiled_draft, CompiledSurface):
                raise TaskManagerError("compiled_draft must be a CompiledSurface.")
            surface_id = compiled_draft.surface_id
            if tuple((item.id, item.type) for item in compiled_draft.components) != tuple(
                (str(index), block.widget_id)
                for index, block in enumerate(draft.blocks, start=1)
            ):
                raise TaskManagerError("compiled_draft components must exactly match draft blocks.")
        if not isinstance(surface_id, str) or not surface_id.strip():
            raise TaskManagerError("surface_id must be a non-empty string.")
        if active_surface is not None and not isinstance(active_surface, dict):
            raise TaskManagerError("active_surface must be an object when provided.")
        self._validate_surface_plan_types(plan)

        run_id = self._create_id("run")
        draft_task_id = self._create_id("task")
        records: dict[str, TaskRecord] = {
            draft_task_id: TaskRecord(
                run_id=run_id,
                task_id=draft_task_id,
                task_type="surface_draft",
                status=QUEUED,
            )
        }
        declared_task_ids: list[str] = []
        for plan_task in plan.tasks:
            task_id = self._create_id("task")
            records[task_id] = TaskRecord(
                run_id=run_id,
                task_id=task_id,
                task_type=plan_task.task_type,
                status=QUEUED,
                evidence_ref=plan_task.evidence_ref,
                instruction=plan_task.instruction,
            )
            declared_task_ids.append(task_id)
        run = _RunState(
            run_id=run_id,
            plan=plan,
            records=records,
            base_task_ids=tuple(declared_task_ids),
            barrier_task_ids=list(declared_task_ids),
            active_surface=dict(active_surface) if active_surface is not None else None,
            draft_task_id=draft_task_id,
        )
        self._runs[run_id] = run
        run.task_bindings = dict(zip(self._task_names(plan), declared_task_ids, strict=True))

        logger.info(
            "[TASK:SURFACE_PLAN_ACCEPTED] run=%s blocks=%s tasks=%s active_surface=%s",
            run_id,
            len(plan.blocks),
            [
                {
                    "task_type": item.task_type,
                    "evidence_ref": item.evidence_ref,
                    "target_slots": list(item.target_slots),
                }
                for item in plan.tasks
            ],
            active_surface is not None,
        )
        await self._publish(run, draft_task_id, "TASK_ACCEPTED", "Surface Draft đã được tiếp nhận.")
        draft_data: dict[str, Any] = {
            "surface": draft.to_dict()["surface"],
            "surface_id": surface_id.strip(),
        }
        if compiled_draft is not None:
            draft_data["anchor_manifest"] = AnchorManifest.from_compiled(compiled_draft).to_dict()
        else:
            logger.warning(
                "[TASK:SURFACE_PLAN_LEGACY_NO_ANCHOR_MANIFEST] run=%s surface=%s",
                run_id,
                surface_id,
            )
        draft_artifact = Artifact(
            artifact_id=f"surface_draft_{draft_task_id}",
            artifact_type="surface_draft",
            run_id=run_id,
            produced_by=draft_task_id,
            data=draft_data,
        )
        self._artifact_store.write(draft_artifact)
        self._replace_record(
            run,
            replace(
                run.records[draft_task_id],
                status=SUCCEEDED,
                artifact_id=draft_artifact.artifact_id,
            ),
        )
        await self._publish(run, draft_task_id, "TASK_SUCCEEDED", "Surface Draft đã được compile.")
        await self._publish(
            run,
            draft_task_id,
            "SURFACE_DRAFT_READY",
            "Surface Draft đã được compile và sẵn sàng để trình bày.",
        )
        for task_id in declared_task_ids:
            await self._publish(run, task_id, "TASK_ACCEPTED", "Task đã được Task Manager tiếp nhận.")
        if before_schedule is not None:
            result = before_schedule()
            if inspect.isawaitable(result):
                await result
        for task_id in declared_task_ids:
            self._schedule(run, task_id)
        await self._maybe_open_barrier(run)
        return self.snapshot(run_id)

    @staticmethod
    def _task_names(plan: SurfaceRetrievalPlan) -> tuple[str, ...]:
        return (plan.openui_program.tasks if plan.openui_program and plan.openui_program.tasks
                else tuple(task.evidence_ref for task in plan.tasks))

    def surface_work_pending(self, run_id: str) -> bool:
        """An edit may start only after workers and the aggregate completion settle."""
        run = self._run(run_id)
        task_ids = [*run.barrier_task_ids]
        if run.complete_task_id is not None:
            task_ids.append(run.complete_task_id)
        return (run.updating or not run.barrier_open or any(
            run.records[task_id].status not in BARRIER_TERMINAL_STATUSES for task_id in task_ids))

    def validate_update(self, run_id: str, plan: SurfaceRetrievalPlan, compiled: CompiledSurface) -> None:
        run = self._run(run_id)
        if run.status != 'ACTIVE':
            raise TaskManagerError('Only an active run can be edited.')
        if self.surface_work_pending(run_id):
            raise TaskManagerError('Wait for the current tasks and Assembly to finish before editing.')
        self._validate_surface_plan_types(plan)
        if len(self._task_names(plan)) != len(plan.tasks) or len(set(self._task_names(plan))) != len(plan.tasks):
            raise TaskManagerError('Task statement bindings must be unique and match declarations.')
        if len(compiled.components) != len(plan.blocks):
            raise TaskManagerError('Compiled update must match plan blocks.')

    async def update_surface_plan(self, run_id: str, plan: SurfaceRetrievalPlan, *,
                                  compiled: CompiledSurface) -> TaskRunSnapshot:
        """Reconcile tasks inside a run; obsolete work never participates in evidence/barrier."""
        self.validate_update(run_id, plan, compiled)
        run = self._run(run_id)
        old_tasks = dict(zip(self._task_names(run.plan), run.plan.tasks, strict=True))
        changed_graph = run.plan.tasks != plan.tasks or self._task_names(run.plan) != self._task_names(plan)
        # A new Pending Slot may reuse already accepted evidence.
        new_slots = set(plan.openui_program.slots if plan.openui_program else ()) - set(
            run.plan.openui_program.slots if run.plan.openui_program else ())
        needs_completion = changed_graph
        if plan.openui_program:
            old_blocks = dict(zip(run.plan.openui_program.slots, run.plan.blocks)) if run.plan.openui_program else {}
            needs_completion = needs_completion or any(
                block.slot_role and (slot in new_slots or (
                    slot in old_blocks and (block.props, block.children) != (old_blocks[slot].props, old_blocks[slot].children)
                    and compiled.components[index].draft_placeholder is not None))
                for index, (slot, block) in enumerate(zip(plan.openui_program.slots, plan.blocks)))
        retained, new_ids, obsolete = {}, [], set(run.base_task_ids)
        for name, declaration in zip(self._task_names(plan), plan.tasks, strict=True):
            before = old_tasks.get(name)
            # All bundled executors use AgentTaskRequestFactory: task identity and
            # instruction only, never Surface bindings. Unknown extensions can opt
            # in explicitly; otherwise target changes conservatively replace work.
            independent = declaration.task_type in {'search_information_task', 'search_image_task', 'draw_image_task'}
            if self._executor_registry.is_bound(declaration.task_type):
                executor = self._executor_registry.resolve(declaration.task_type)
                independent = independent or getattr(executor, 'surface_binding_independent', False)
            same = before is not None and (before.task_type, before.instruction, before.evidence_ref) == (
                declaration.task_type, declaration.instruction, declaration.evidence_ref)
            if same and (independent or before.target_slots == declaration.target_slots):
                task_id = run.task_bindings[name]
                obsolete.discard(task_id)
            else:
                task_id = self._create_id('task')
                new_ids.append(task_id)
            retained[name] = task_id
        run.updating = True
        try:
            if needs_completion and run.complete_task_id:
                old_completion = run.complete_task_id
                run.complete_task_id = None
                if run.records[old_completion].status not in TERMINAL_STATUSES:
                    await self.cancel_task(run_id, old_completion)
            run.plan = plan
            run.task_bindings = retained
            run.base_task_ids = tuple(retained.values())
            run.barrier_task_ids = list(run.base_task_ids)
            for source in run.base_task_ids:
                fallback = run.fallback_by_source.get(source)
                if fallback:
                    run.barrier_task_ids.append(fallback)
            # Replace the single draft artifact only after all candidate validation.
            artifact = self._artifact_store.artifact_for_task(run_id, run.draft_task_id)
            assert artifact is not None
            self._artifact_store.write(replace(artifact, data={
                'surface': SurfaceDraft(plan.blocks).to_dict()['surface'],
                'surface_id': compiled.surface_id,
                'anchor_manifest': AnchorManifest.from_compiled(compiled).to_dict()}))
            for name, declaration in zip(self._task_names(plan), plan.tasks, strict=True):
                task_id = retained[name]
                if task_id in new_ids:
                    run.records[task_id] = TaskRecord(run_id=run_id, task_id=task_id,
                        task_type=declaration.task_type, status=QUEUED,
                        evidence_ref=declaration.evidence_ref, instruction=declaration.instruction)
            for task_id in obsolete:
                fallback = run.fallback_by_source.get(task_id)
                if fallback:
                    await self.cancel_task(run_id, fallback)
                await self.cancel_task(run_id, task_id)
            if needs_completion:
                run.barrier_open = False
            for task_id in new_ids:
                await self._publish(run, task_id, 'TASK_ACCEPTED', 'Task mới được tiếp nhận sau edit.')
                self._schedule(run, task_id)
        finally:
            run.updating = False
        await self._maybe_open_barrier(run)
        return self.snapshot(run_id)

    def accept_completed_program(self, run_id: str, plan: SurfaceRetrievalPlan, compiled: CompiledSurface) -> None:
        """Keep filled content as the next accepted scheduling/Assembly base."""
        run = self._run(run_id)
        if run.plan.tasks != plan.tasks:
            raise TaskManagerError('Completion task graph is stale.')
        run.plan = plan
        artifact = self._artifact_store.artifact_for_task(run_id, run.draft_task_id)
        assert artifact is not None
        self._artifact_store.write(replace(artifact, data={
            'surface': SurfaceDraft(plan.blocks).to_dict()['surface'], 'surface_id':compiled.surface_id,
            'anchor_manifest':AnchorManifest.from_compiled(compiled).to_dict()}))

    def accept_completed_content(self, run_id: str, *, program: Any, blocks: Any,
                                 compiled: CompiledSurface) -> None:
        run = self._run(run_id)
        self.accept_completed_program(run_id, replace(run.plan, blocks=blocks, openui_program=program), compiled)

    def snapshot(self, run_id: str) -> TaskRunSnapshot:
        """Return Task Manager state without exposing internal async handles."""

        run = self._run(run_id)
        return TaskRunSnapshot(
            run_id=run.run_id,
            status=run.status,
            records=tuple(run.records.values()),
            barrier_open=run.barrier_open,
            complete_task_id=run.complete_task_id,
        )

    def events(self, run_id: str) -> tuple[TaskEvent, ...]:
        """Return the event history intended for Gemini Live transport."""

        return tuple(self._run(run_id).events)

    async def wait_for_run(self, run_id: str) -> TaskRunSnapshot:
        """Wait until currently scheduled work for a run reaches a stable state."""

        while True:
            handles = [
                handle for (candidate_run, _), handle in self._handles.items()
                if candidate_run == run_id and not handle.done()
            ]
            if not handles:
                return self.snapshot(run_id)
            await asyncio.gather(*handles, return_exceptions=True)

    async def cancel_task(self, run_id: str, task_id: str) -> TaskRunSnapshot:
        """Request cancellation and cancel the asyncio boundary where possible."""

        run = self._run(run_id)
        record = self._record(run, task_id)
        if record.status in TERMINAL_STATUSES:
            return self.snapshot(run_id)
        if record.status in {QUEUED, PAUSED}:
            self._replace_record(run, replace(record, status=CANCELLED))
            handle = self._handles.get((run_id, task_id))
            if handle is not None and not handle.done():
                handle.cancel()
            await self._publish(run, task_id, "TASK_CANCELLED", "Task đã bị hủy.")
            await self._maybe_open_barrier(run)
            return self.snapshot(run_id)
        self._replace_record(run, replace(record, status=CANCEL_REQUESTED))
        handle = self._handles.get((run_id, task_id))
        if handle is not None and not handle.done():
            handle.cancel()
        else:
            self._replace_record(run, replace(record, status=CANCELLED))
            await self._publish(run, task_id, "TASK_CANCELLED", "Task đã bị hủy.")
            await self._maybe_open_barrier(run)
        return self.snapshot(run_id)

    async def cancel_run(self, run_id: str) -> TaskRunSnapshot:
        """Cancel all non-terminal records in one active run."""

        run = self._run(run_id)
        if run.status == "SUPERSEDED":
            return self.snapshot(run_id)
        run.status = "CANCELLED"
        for task_id, record in tuple(run.records.items()):
            if record.status not in TERMINAL_STATUSES:
                await self.cancel_task(run_id, task_id)
        return self.snapshot(run_id)

    async def retry_task(self, run_id: str, task_id: str) -> TaskRunSnapshot:
        """Queue another attempt; failed/cancelled attempts have no Artifact."""

        run = self._run(run_id)
        record = self._record(run, task_id)
        if run.status != "ACTIVE":
            raise TaskManagerError(f"run '{run_id}' is not active and cannot retry tasks.")
        if record.status not in {FAILED, CANCELLED}:
            raise TaskManagerError("only FAILED or CANCELLED tasks may be retried.")
        if not self._executor_registry.is_bound(record.task_type):
            raise TaskManagerError(
                f"task type '{record.task_type}' has no bound executor for retry."
            )
        if run.complete_task_id is not None:
            complete = run.records[run.complete_task_id]
            if complete.status not in TERMINAL_STATUSES:
                raise TaskManagerError("cannot retry while complete_surface_task is still active.")
            run.complete_task_id = None
        run.barrier_open = False
        self._replace_record(
            run,
            replace(record, status=QUEUED, attempt=record.attempt + 1, error=None),
        )
        await self._publish(run, task_id, "TASK_RETRIED", "Task được xếp lại với attempt mới.")
        self._schedule(run, task_id)
        return self.snapshot(run_id)

    async def pause_task(self, run_id: str, task_id: str) -> TaskRunSnapshot:
        """Pause queued work; running work records a cooperative pause request."""

        run = self._run(run_id)
        record = self._record(run, task_id)
        if record.status == QUEUED:
            self._replace_record(run, replace(record, status=PAUSED))
            handle = self._handles.get((run_id, task_id))
            if handle is not None and not handle.done():
                # The coroutine was queued by submit_surface_plan() but has not received a
                # turn yet.  Drain its cancellation before resume() may queue
                # a replacement handle for the same task ID.
                handle.cancel()
                await asyncio.gather(handle, return_exceptions=True)
            await self._publish(run, task_id, "TASK_PROGRESS", "Task đã pause trước khi chạy.")
        elif record.status == RUNNING:
            self._replace_record(run, replace(record, status=PAUSE_REQUESTED))
            await self._publish(
                run,
                task_id,
                "TASK_PROGRESS",
                "Đã yêu cầu pause; executor chỉ pause tại checkpoint hỗ trợ.",
            )
        else:
            raise TaskManagerError("only QUEUED or RUNNING tasks may be paused.")
        return self.snapshot(run_id)

    async def resume_task(self, run_id: str, task_id: str) -> TaskRunSnapshot:
        """Resume queued-paused work; clear a running cooperative pause request."""

        run = self._run(run_id)
        record = self._record(run, task_id)
        if record.status == PAUSED:
            self._replace_record(run, replace(record, status=QUEUED))
            await self._publish(run, task_id, "TASK_PROGRESS", "Task tiếp tục từ hàng đợi.")
            self._schedule(run, task_id)
        elif record.status == PAUSE_REQUESTED:
            self._replace_record(run, replace(record, status=RUNNING))
            await self._publish(run, task_id, "TASK_PROGRESS", "Đã hủy yêu cầu pause.")
        else:
            raise TaskManagerError("only PAUSED or PAUSE_REQUESTED tasks may be resumed.")
        return self.snapshot(run_id)

    async def supersede_run(self, run_id: str) -> TaskRunSnapshot:
        """Close a run and ignore every late executor result from it."""

        run = self._run(run_id)
        if run.status == "SUPERSEDED":
            return self.snapshot(run_id)
        run.status = "SUPERSEDED"
        for task_id, record in tuple(run.records.items()):
            if record.status not in TERMINAL_STATUSES:
                self._replace_record(run, replace(record, status=SUPERSEDED))
                handle = self._handles.get((run_id, task_id))
                if handle is not None and not handle.done():
                    handle.cancel()
                await self._publish(run, task_id, "RUN_SUPERSEDED", "Run đã bị thay thế.")
        return self.snapshot(run_id)


    def _validate_surface_plan_types(self, plan: SurfaceRetrievalPlan) -> None:
        for plan_task in plan.tasks:
            task_type = plan_task.task_type
            if task_type in {"surface_task", COMPLETE_SURFACE_TASK}:
                raise TaskManagerError(
                    f"surface plan may not declare Runtime-owned task type '{task_type}'."
                )
            if not self._executor_registry.supports(task_type):
                raise TaskManagerError(f"unsupported task type '{task_type}'.")
        if not self._executor_registry.supports(COMPLETE_SURFACE_TASK):
            raise TaskManagerError("complete_surface_task must be registered.")

    def _schedule(self, run: _RunState, task_id: str) -> None:
        previous = self._handles.get((run.run_id, task_id))
        if previous is not None and not previous.done():
            raise TaskManagerError(f"task '{task_id}' is already scheduled.")
        self._handles[(run.run_id, task_id)] = asyncio.create_task(
            self._execute(run.run_id, task_id), name=f"{run.run_id}:{task_id}"
        )

    async def _execute(self, run_id: str, task_id: str) -> None:
        run = self._run(run_id)
        record = self._record(run, task_id)
        if run.status == "SUPERSEDED" or record.status == SUPERSEDED:
            return
        if record.status == PAUSED:
            return
        if record.status != QUEUED:
            return
        try:
            executor = self._executor_registry.resolve(record.task_type)
        except ExecutorRegistryError as exc:
            logger.error(
                "[TASK:EXECUTOR_UNAVAILABLE] run=%s task=%s type=%s evidence=%s detail=%s",
                run_id, task_id, record.task_type, record.evidence_ref or "-", exc,
            )
            await self._fail(run, task_id, str(exc))
            return
        self._replace_record(run, replace(record, status=RUNNING))
        logger.info(
            "[TASK:START] run=%s task=%s type=%s attempt=%s evidence=%s instruction=%r",
            run_id,
            task_id,
            record.task_type,
            record.attempt,
            record.evidence_ref or "-",
            record.instruction,
        )
        result: object | None = None
        try:
            await self._publish(run, task_id, "TASK_STARTED", "Task bắt đầu thực hiện.")
            pending = executor(self._execution_context(run, self._record(run, task_id)))
            if not inspect.isawaitable(pending):
                raise TaskManagerError("executor must return an awaitable TaskResult.")
            if self._executor_timeout_seconds is None:
                result = await pending
            else:
                result = await asyncio.wait_for(pending, self._executor_timeout_seconds)
            latest = self._record(run, task_id)
            if run.status != "ACTIVE" or latest.status == SUPERSEDED:
                self._rollback_result_lifecycle(result)
                return
            if latest.status in {CANCEL_REQUESTED, CANCELLED}:
                self._rollback_result_lifecycle(result)
                self._replace_record(run, replace(latest, status=CANCELLED))
                await self._publish(run, task_id, "TASK_CANCELLED", "Task đã bị hủy.")
                await self._maybe_open_barrier(run)
                return
            self._validate_result(run, task_id, result)
            decision = self._fallback_policies.evaluate(
                self._record(run, task_id),
                status=SUCCEEDED,
                metrics=result.metrics,
            )
            if decision.action != ACCEPT:
                self._rollback_result_lifecycle(result)
                await self._reject_candidate(run, task_id, decision)
                return
            self._accept_result(run, task_id, result)
            logger.info(
                "[TASK:RESULT] run=%s task=%s type=%s evidence=%s artifact=%s metrics=%s warnings=%s",
                run_id,
                task_id,
                latest.task_type,
                latest.evidence_ref or "-",
                result.artifact.artifact_id
                if isinstance(result, TaskResult) and result.artifact is not None
                else "invalid",
                dict(result.metrics) if isinstance(result, TaskResult) else {},
                list(result.warnings) if isinstance(result, TaskResult) else [],
            )
        except asyncio.CancelledError:
            latest = self._record(run, task_id)
            if run.status != "SUPERSEDED" and latest.status != SUPERSEDED:
                self._replace_record(run, replace(latest, status=CANCELLED))
                await self._publish(run, task_id, "TASK_CANCELLED", "Task đã bị hủy.")
                await self._maybe_open_barrier(run)
            raise
        except Exception as exc:
            self._rollback_result_lifecycle(result)
            logger.exception(
                "[TASK:FAILED] run=%s task=%s type=%s evidence=%s",
                run_id,
                task_id,
                record.task_type,
                record.evidence_ref or "-",
            )
            await self._fail(run, task_id, str(exc) or type(exc).__name__)
            if record.task_type == COMPLETE_SURFACE_TASK:
                missing_parts = tuple(
                    {
                        "description": block.description,
                        "reason": "COMPLETE_SURFACE_FAILED",
                    }
                    for block in run.plan.blocks
                    if block.slot_role is not None and block.description
                )
                await self._publish(
                    run,
                    task_id,
                    "COMPLETE_SURFACE_FAILED",
                    "Complete Surface không thể được materialize.",
                    missing_parts=missing_parts,
                )
        else:
            await self._publish(run, task_id, "TASK_SUCCEEDED", "Task đã hoàn tất.")
            if self._record(run, task_id).task_type == COMPLETE_SURFACE_TASK:
                assert isinstance(result, TaskResult)
                surface_status = result.metrics.get("surface_status", "ready")
                raw_missing_parts = result.metrics.get("missing_parts", ())
                missing_parts = tuple(
                    item
                    for item in raw_missing_parts
                    if isinstance(item, Mapping)
                    and set(item) == {"description", "reason"}
                ) if isinstance(raw_missing_parts, (list, tuple)) else ()
                event_name = (
                    "COMPLETE_SURFACE_PARTIAL"
                    if surface_status == "partial"
                    else "COMPLETE_SURFACE_READY"
                )
                await self._publish(
                    run,
                    task_id,
                    event_name,
                    "Complete Surface đã được materialize.",
                    missing_parts=missing_parts,
                )
            await self._maybe_open_barrier(run)

    def _accept_result(self, run: _RunState, task_id: str, result: object) -> None:
        self._validate_result(run, task_id, result)
        assert isinstance(result, TaskResult)
        record = self._record(run, task_id)
        assert result.artifact is not None
        if result.lifecycle is not None:
            result.lifecycle.commit()
        self._artifact_store.write(result.artifact)
        self._replace_record(
            run,
            replace(record, status=SUCCEEDED, artifact_id=result.artifact.artifact_id, error=None),
        )

    def _validate_result(self, run: _RunState, task_id: str, result: object) -> None:
        """Validate a candidate without committing lifecycle side effects or Artifact."""

        if not isinstance(result, TaskResult):
            raise TaskManagerError("executor must return a TaskResult.")
        record = self._record(run, task_id)
        if result.task_id != task_id:
            raise TaskManagerError("TaskResult.task_id does not match the executing task.")
        if result.evidence_ref != record.evidence_ref:
            raise TaskManagerError("TaskResult.evidence_ref does not match the executing task.")
        if _status(result.status) != SUCCEEDED:
            raise TaskManagerError("a completed executor must return status 'succeeded'.")
        if result.artifact is None:
            raise TaskManagerError("a succeeded TaskResult must contain one artifact.")
        self._validate_artifact(record, result.artifact)

    @staticmethod
    def _rollback_result_lifecycle(result: object) -> None:
        if not isinstance(result, TaskResult) or result.lifecycle is None:
            return
        try:
            result.lifecycle.rollback()
        except Exception:
            logger.exception("[TASK:RESULT_LIFECYCLE_ROLLBACK_FAILED] task=%s", result.task_id)

    def _validate_artifact(self, record: TaskRecord, artifact: Artifact) -> None:
        if artifact.run_id != record.run_id:
            raise TaskManagerError("artifact.run_id does not match its TaskRecord.")
        if artifact.produced_by != record.task_id:
            raise TaskManagerError("artifact.produced_by does not match its TaskRecord.")
        if artifact.evidence_ref != record.evidence_ref:
            raise TaskManagerError("artifact.evidence_ref does not match its TaskRecord.")

    async def _fail(self, run: _RunState, task_id: str, error: str) -> None:
        record = self._record(run, task_id)
        if run.status == "SUPERSEDED" or record.status == SUPERSEDED:
            return
        if record.status in {CANCEL_REQUESTED, CANCELLED}:
            self._replace_record(run, replace(record, status=CANCELLED))
            await self._maybe_open_barrier(run)
            return
        decision = self._fallback_policies.evaluate(
            record,
            status=FAILED,
            metrics={},
        )
        await self._mark_failed(run, task_id, error)
        await self._schedule_policy_fallback(run, task_id, decision)
        await self._maybe_open_barrier(run)

    async def _reject_candidate(
        self,
        run: _RunState,
        task_id: str,
        decision: FallbackDecision,
    ) -> None:
        error = decision.error_code or "TASK_RESULT_REJECTED_BY_FALLBACK_POLICY"
        await self._mark_failed(run, task_id, error)
        await self._schedule_policy_fallback(run, task_id, decision)
        await self._maybe_open_barrier(run)

    async def _mark_failed(self, run: _RunState, task_id: str, error: str) -> None:
        record = self._record(run, task_id)
        self._replace_record(run, replace(record, status=FAILED, error=error))
        await self._publish(
            run,
            task_id,
            "TASK_FAILED",
            "Task thất bại.",
            warnings=(error,),
        )

    async def _schedule_policy_fallback(
        self,
        run: _RunState,
        source_task_id: str,
        decision: FallbackDecision,
    ) -> bool:
        """Materialize one policy-requested fallback without task-specific logic."""

        if decision.action != REJECT_AND_FALLBACK or decision.fallback is None:
            return False
        if run.status != "ACTIVE":
            return False
        if source_task_id not in run.base_task_ids:
            return False
        source = self._record(run, source_task_id)
        if source_task_id in run.fallback_by_source:
            return False
        request = decision.fallback
        if not self._executor_registry.is_bound(request.task_type):
            logger.warning(
                "[TASK:FALLBACK_UNAVAILABLE] run=%s source_task=%s fallback_type=%s",
                run.run_id,
                source_task_id,
                request.task_type,
            )
            return False
        if source.evidence_ref is None:
            raise TaskManagerError("a fallback source task requires evidence_ref.")

        fallback_id = self._create_id("task")
        fallback = TaskRecord(
            run_id=run.run_id,
            task_id=fallback_id,
            task_type=request.task_type,
            status=QUEUED,
            evidence_ref=source.evidence_ref,
            instruction=request.instruction,
            fallback_of_task_id=source.task_id,
        )
        run.records[fallback_id] = fallback
        run.barrier_task_ids.append(fallback_id)
        run.fallback_by_source[source_task_id] = fallback_id
        logger.info(
            "[TASK:FALLBACK_SCHEDULED] run=%s source_task=%s source_status=%s "
            "fallback_task=%s fallback_type=%s evidence=%s",
            run.run_id,
            source.task_id,
            source.status,
            fallback_id,
            request.task_type,
            source.evidence_ref,
        )
        await self._publish(
            run,
            fallback_id,
            "TASK_FALLBACK_SCHEDULED",
            "Fallback task was scheduled by Runtime policy.",
        )
        await self._publish(
            run,
            fallback_id,
            "TASK_ACCEPTED",
            "Manager-owned fallback task was accepted.",
        )
        self._schedule(run, fallback_id)
        return True

    async def _maybe_open_barrier(self, run: _RunState) -> None:
        if run.status != "ACTIVE" or run.barrier_open or run.updating:
            return
        barrier_records = tuple(run.records[task_id] for task_id in run.barrier_task_ids)
        if not all(record.status in BARRIER_TERMINAL_STATUSES for record in barrier_records):
            logger.info(
                "[TASK:BARRIER_WAIT] run=%s states=%s",
                run.run_id,
                {
                    record.task_type + ":" + record.task_id[-8:]: record.status
                    for record in barrier_records
                },
            )
            return
        surface = run.records[run.draft_task_id]
        if surface.status != SUCCEEDED:
            run.status = "BLOCKED"
            logger.error(
                "[TASK:BARRIER_BLOCKED] run=%s reason=surface_draft_not_succeeded surface_status=%s surface_error=%r states=%s",
                run.run_id,
                surface.status,
                surface.error,
                {
                    record.task_type + ":" + record.task_id[-8:]: {
                        "status": record.status,
                        "error": record.error,
                    }
                    for record in barrier_records
                },
            )
            return
        run.barrier_open = True
        complete_id = self._create_id("task")
        run.complete_task_id = complete_id
        run.records[complete_id] = TaskRecord(
            run_id=run.run_id,
            task_id=complete_id,
            task_type=COMPLETE_SURFACE_TASK,
            status=QUEUED,
        )
        await self._publish(
            run,
            complete_id,
            "TASK_ACCEPTED",
            "Barrier đã mở; complete_surface_task được tạo.",
        )
        logger.info(
            "[TASK:BARRIER_OPEN] run=%s complete_task=%s evidence_refs=%s",
            run.run_id,
            complete_id,
            [record.evidence_ref for record in barrier_records if record.evidence_ref is not None],
        )
        self._schedule(run, complete_id)

    async def _publish(
        self,
        run: _RunState,
        task_id: str,
        event_name: str,
        summary: str,
        *,
        warnings: tuple[str, ...] = (),
        missing_parts: tuple[Mapping[str, str], ...] = (),
    ) -> None:
        record = self._record(run, task_id)
        event = TaskEvent(
            event=event_name,
            run_id=run.run_id,
            task_id=record.task_id,
            task_type=record.task_type,
            status=record.status,
            summary=summary,
            evidence_ref=record.evidence_ref,
            artifact_id=record.artifact_id,
            warnings=warnings,
            fallback_of_task_id=record.fallback_of_task_id,
            missing_parts=missing_parts,
        )
        run.events.append(event)
        logger.info(
            "[TASK:EVENT] run=%s task=%s type=%s event=%s status=%s evidence=%s artifact=%s warnings=%s missing_parts=%s fallback_of=%s summary=%r",
            event.run_id,
            event.task_id,
            event.task_type,
            event.event,
            event.status,
            event.evidence_ref or "-",
            event.artifact_id or "-",
            list(event.warnings),
            list(event.missing_parts),
            event.fallback_of_task_id or "-",
            event.summary,
        )
        if self._event_sink is not None:
            emitted = self._event_sink(event)
            if inspect.isawaitable(emitted):
                await emitted

    def _create_id(self, prefix: str) -> str:
        value = self._id_factory(prefix)
        if not isinstance(value, str) or not value.strip():
            raise TaskManagerError("id_factory must return a non-empty string.")
        return value.strip()

    def _run(self, run_id: str) -> _RunState:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise TaskManagerError(f"unknown run_id '{run_id}'.") from exc

    @staticmethod
    def _record(run: _RunState, task_id: str) -> TaskRecord:
        try:
            return run.records[task_id]
        except KeyError as exc:
            raise TaskManagerError(f"unknown task_id '{task_id}' in run '{run.run_id}'.") from exc

    @staticmethod
    def _replace_record(run: _RunState, record: TaskRecord) -> None:
        run.records[record.task_id] = record

    @staticmethod
    def _execution_context(run: _RunState, task: TaskRecord) -> TaskExecutionContext:
        return TaskExecutionContext(
            run_id=run.run_id,
            task=task,
            surface_plan=run.plan,
            active_surface=run.active_surface,
            evidence_task_ids=tuple(task_id for task_id in run.barrier_task_ids
                                    if run.records[task_id].status == SUCCEEDED),
        )

