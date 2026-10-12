"""Direct CP3 lifecycle tests without models, network, or browser runtime."""

from __future__ import annotations

import asyncio
import itertools
import time
import unittest

from final_framework.tests.plan_fixtures import make_surface_plan, submit_plan
from final_framework.artifacts import ArtifactStore
from final_framework.executors import (
    COMPLETE_SURFACE_TASK,
    DRAW_IMAGE_TASK,
    SEARCH_IMAGE_TASK,
    SEARCH_INFORMATION_TASK,
    build_default_executor_registry,
)
from final_framework.task_manager import (
    Artifact,
    SurfaceRetrievalPlan,
    TaskManager,
    TaskResult,
)


def _ids() -> callable:
    counter = itertools.count(1)
    return lambda prefix: f"{prefix}_{next(counter)}"


def _plan(*, with_media: bool = True) -> SurfaceRetrievalPlan:
    tasks = [("#E1", SEARCH_INFORMATION_TASK, "Tìm nội dung.")]
    if with_media:
        tasks.append(("#E2", SEARCH_IMAGE_TASK, "Tìm ảnh."))
    return make_surface_plan(tasks)


def _artifact(task, artifact_type: str) -> Artifact:
    return Artifact(
        artifact_id=f"artifact_{task.task_id}",
        artifact_type=artifact_type,
        run_id=task.run_id,
        produced_by=task.task_id,
        evidence_ref=task.evidence_ref,
        data={
            "items": (
                [{"media_id": f"media_{task.task_id}"}]
                if artifact_type == "media"
                else []
            )
        },
    )


def _metrics(task_type: str, artifact: Artifact) -> dict[str, object]:
    if task_type != SEARCH_IMAGE_TASK:
        return {}
    count = len(artifact.data.get("items", ()))
    return {
        "usable_resource_count": count,
        "usable_resource_kinds": ["image"] if count else [],
    }


class TaskManagerLifecycleTests(unittest.TestCase):
    def _manager(self, *, fail_information: bool = False):
        registry = build_default_executor_registry()
        store = ArtifactStore()
        starts: list[tuple[str, float]] = []

        async def executor(context):
            task = context.task
            starts.append((task.task_type, time.perf_counter()))
            await asyncio.sleep(0.03)
            if task.task_type == SEARCH_INFORMATION_TASK and fail_information:
                raise RuntimeError("knowledge unavailable")
            artifact_type = {
                SEARCH_INFORMATION_TASK: "knowledge",
                SEARCH_IMAGE_TASK: "media",
                DRAW_IMAGE_TASK: "generated_image",
                COMPLETE_SURFACE_TASK: "complete_surface",
            }[task.task_type]
            from final_framework.task_manager import TaskResult
            return TaskResult(
                task.task_id,
                "succeeded",
                _artifact(task, artifact_type),
                evidence_ref=task.evidence_ref,
                metrics=_metrics(task.task_type, _artifact(task, artifact_type)),
            )

        for task_type in (
                    SEARCH_INFORMATION_TASK,
            SEARCH_IMAGE_TASK,
            DRAW_IMAGE_TASK,
            COMPLETE_SURFACE_TASK,
        ):
            registry.bind(task_type, executor)
        return TaskManager(registry, store, id_factory=_ids()), starts, store

    def test_records_draft_and_runs_evidence_tasks_in_parallel_then_completes(self) -> None:
        async def scenario():
            manager, starts, store = self._manager()
            submitted = await submit_plan(manager, _plan())
            final = await manager.wait_for_run(submitted.run_id)
            return manager, final, starts, store

        manager, final, starts, store = asyncio.run(scenario())
        records = {record.task_type: record for record in final.records}
        self.assertTrue(final.barrier_open)
        self.assertEqual(records["surface_draft"].status, "SUCCEEDED")
        self.assertEqual(records[SEARCH_INFORMATION_TASK].status, "SUCCEEDED")
        self.assertEqual(records[SEARCH_IMAGE_TASK].status, "SUCCEEDED")
        self.assertEqual(records[COMPLETE_SURFACE_TASK].status, "SUCCEEDED")
        self.assertNotIn(DRAW_IMAGE_TASK, records)
        self.assertLess(max(time for _, time in starts[:2]) - min(time for _, time in starts[:2]), 0.02)
        self.assertEqual(
            [event.event for event in manager.events(final.run_id)].count("TASK_ACCEPTED"), 4
        )
        self.assertEqual(
            set(store.evidence_by_ref(final.run_id)), {"#E1", "#E2"}
        )

    def test_complete_surface_partial_event_carries_semantic_missing_parts(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            store = ArtifactStore()

            async def executor(context):
                task = context.task
                artifact_type = (
                    "complete_surface"
                    if task.task_type == COMPLETE_SURFACE_TASK
                    else "knowledge"
                )
                metrics = {}
                if task.task_type == COMPLETE_SURFACE_TASK:
                    metrics = {
                        "surface_status": "partial",
                        "missing_parts": [{
                            "description": "Ảnh minh họa ba quả táo.",
                            "reason": "CONTENT_NOT_RENDERED",
                        }],
                    }
                return TaskResult(
                    task.task_id,
                    "succeeded",
                    _artifact(task, artifact_type),
                    evidence_ref=task.evidence_ref,
                    metrics=metrics,
                )

            registry.bind(SEARCH_INFORMATION_TASK, executor)
            registry.bind(COMPLETE_SURFACE_TASK, executor)
            manager = TaskManager(registry, store, id_factory=_ids())
            submitted = await submit_plan(manager, _plan(with_media=False))
            await manager.wait_for_run(submitted.run_id)
            return manager.events(submitted.run_id)

        events = asyncio.run(scenario())
        partial = next(
            event for event in events if event.event == "COMPLETE_SURFACE_PARTIAL"
        )
        self.assertEqual(partial.missing_parts, ({
            "description": "Ảnh minh họa ba quả táo.",
            "reason": "CONTENT_NOT_RENDERED",
        },))

    def test_complete_surface_failure_event_names_unrendered_plan_parts(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            store = ArtifactStore()

            async def information(context):
                task = context.task
                return TaskResult(
                    task.task_id,
                    "succeeded",
                    _artifact(task, "knowledge"),
                    evidence_ref=task.evidence_ref,
                )

            async def complete(_context):
                raise RuntimeError("assembly unavailable")

            registry.bind(SEARCH_INFORMATION_TASK, information)
            registry.bind(COMPLETE_SURFACE_TASK, complete)
            manager = TaskManager(registry, store, id_factory=_ids())
            submitted = await submit_plan(manager, _plan(with_media=False))
            await manager.wait_for_run(submitted.run_id)
            return manager.events(submitted.run_id)

        events = asyncio.run(scenario())
        failed = next(
            event for event in events if event.event == "COMPLETE_SURFACE_FAILED"
        )
        self.assertEqual(failed.missing_parts, ({
            "description": "Dữ liệu cho slot_1",
            "reason": "COMPLETE_SURFACE_FAILED",
        },))

    def test_empty_media_result_schedules_draw_fallback_before_barrier(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            store = ArtifactStore()

            async def executor(context):
                task = context.task
                artifact_type = {
                    SEARCH_INFORMATION_TASK: "knowledge",
                    SEARCH_IMAGE_TASK: "media",
                    DRAW_IMAGE_TASK: "generated_image",
                    COMPLETE_SURFACE_TASK: "complete_surface",
                }[task.task_type]
                artifact = _artifact(task, artifact_type)
                if task.task_type == SEARCH_IMAGE_TASK:
                    artifact = Artifact(
                        artifact_id=artifact.artifact_id,
                        artifact_type=artifact.artifact_type,
                        run_id=artifact.run_id,
                        produced_by=artifact.produced_by,
                        evidence_ref=artifact.evidence_ref,
                        data={"items": []},
                    )
                return TaskResult(
                    task.task_id,
                    "succeeded",
                    artifact,
                    evidence_ref=task.evidence_ref,
                    metrics=_metrics(task.task_type, artifact),
                )

            for task_type in (
                            SEARCH_INFORMATION_TASK,
                SEARCH_IMAGE_TASK,
                DRAW_IMAGE_TASK,
                COMPLETE_SURFACE_TASK,
            ):
                registry.bind(task_type, executor)
            manager = TaskManager(registry, store, id_factory=_ids())
            submitted = await submit_plan(manager, _plan())
            return manager, await manager.wait_for_run(submitted.run_id)

        manager, final = asyncio.run(scenario())
        records = final.records
        search = next(record for record in records if record.task_type == SEARCH_IMAGE_TASK)
        fallback = next(record for record in records if record.task_type == DRAW_IMAGE_TASK)
        self.assertEqual(fallback.status, "SUCCEEDED")
        self.assertEqual(fallback.evidence_ref, search.evidence_ref)
        self.assertFalse(hasattr(fallback, "target_slots"))
        self.assertEqual(fallback.fallback_of_task_id, search.task_id)
        self.assertTrue(fallback.instruction.startswith("Vẽ một ảnh minh họa phù hợp"))
        self.assertEqual(
            [event.event for event in manager.events(final.run_id)].count("TASK_FALLBACK_SCHEDULED"),
            1,
        )
        self.assertLess(
            [event.event for event in manager.events(final.run_id)].index("TASK_FALLBACK_SCHEDULED"),
            [event.event for event in manager.events(final.run_id)].index("COMPLETE_SURFACE_READY"),
        )

    def test_failed_media_search_schedules_draw_fallback(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            store = ArtifactStore()

            async def executor(context):
                task = context.task
                if task.task_type == SEARCH_IMAGE_TASK:
                    raise RuntimeError("no local or grounded image")
                artifact_type = {
                    SEARCH_INFORMATION_TASK: "knowledge",
                    DRAW_IMAGE_TASK: "generated_image",
                    COMPLETE_SURFACE_TASK: "complete_surface",
                }[task.task_type]
                return TaskResult(
                    task.task_id,
                    "succeeded",
                    _artifact(task, artifact_type),
                    evidence_ref=task.evidence_ref,
                )

            for task_type in (
                            SEARCH_INFORMATION_TASK,
                SEARCH_IMAGE_TASK,
                DRAW_IMAGE_TASK,
                COMPLETE_SURFACE_TASK,
            ):
                registry.bind(task_type, executor)
            manager = TaskManager(registry, store, id_factory=_ids())
            submitted = await submit_plan(manager, _plan())
            return manager, await manager.wait_for_run(submitted.run_id)

        manager, final = asyncio.run(scenario())
        search = next(record for record in final.records if record.task_type == SEARCH_IMAGE_TASK)
        fallback = next(record for record in final.records if record.task_type == DRAW_IMAGE_TASK)
        self.assertEqual(search.status, "FAILED")
        self.assertEqual(fallback.status, "SUCCEEDED")
        self.assertEqual(fallback.fallback_of_task_id, search.task_id)
        fallback_events = [
            event for event in manager.events(final.run_id)
            if event.event == "TASK_FALLBACK_SCHEDULED"
        ]
        self.assertEqual(fallback_events[0].fallback_of_task_id, search.task_id)

    def test_cancelled_media_search_does_not_schedule_draw_fallback(self) -> None:
        async def scenario():
            manager, _, _ = self._manager()
            submitted = await submit_plan(manager, _plan())
            search = next(
                record for record in submitted.records
                if record.task_type == SEARCH_IMAGE_TASK
            )
            await manager.cancel_task(submitted.run_id, search.task_id)
            return manager, await manager.wait_for_run(submitted.run_id)

        manager, final = asyncio.run(scenario())
        search = next(record for record in final.records if record.task_type == SEARCH_IMAGE_TASK)
        self.assertEqual(search.status, "CANCELLED")
        self.assertFalse(any(record.task_type == DRAW_IMAGE_TASK for record in final.records))
        self.assertNotIn(
            "TASK_FALLBACK_SCHEDULED",
            [event.event for event in manager.events(final.run_id)],
        )

    def test_failed_evidence_still_opens_barrier_when_surface_succeeds(self) -> None:
        async def scenario():
            manager, _, _ = self._manager(fail_information=True)
            submitted = await submit_plan(manager, _plan())
            return await manager.wait_for_run(submitted.run_id)

        final = asyncio.run(scenario())
        records = {record.task_type: record for record in final.records}
        self.assertEqual(records[SEARCH_INFORMATION_TASK].status, "FAILED")
        self.assertEqual(records[COMPLETE_SURFACE_TASK].status, "SUCCEEDED")

    def test_pause_before_execution_then_resume(self) -> None:
        async def scenario():
            manager, _, _ = self._manager()
            submitted = await submit_plan(manager, _plan(with_media=False))
            info = next(
                record for record in submitted.records if record.task_type == SEARCH_INFORMATION_TASK
            )
            await manager.pause_task(submitted.run_id, info.task_id)
            paused = manager.snapshot(submitted.run_id)
            await manager.resume_task(submitted.run_id, info.task_id)
            return paused, await manager.wait_for_run(submitted.run_id)

        paused, final = asyncio.run(scenario())
        paused_info = next(record for record in paused.records if record.task_type == SEARCH_INFORMATION_TASK)
        final_info = next(record for record in final.records if record.task_type == SEARCH_INFORMATION_TASK)
        self.assertEqual(paused_info.status, "PAUSED")
        self.assertEqual(final_info.status, "SUCCEEDED")

    def test_superseded_run_ignores_late_results(self) -> None:
        async def scenario():
            manager, _, _ = self._manager()
            submitted = await submit_plan(manager, _plan())
            superseded = await manager.supersede_run(submitted.run_id)
            await manager.wait_for_run(submitted.run_id)
            return manager, superseded

        manager, snapshot = asyncio.run(scenario())
        self.assertEqual(snapshot.status, "SUPERSEDED")
        self.assertIsNone(snapshot.complete_task_id)
        self.assertIn("RUN_SUPERSEDED", [event.event for event in manager.events(snapshot.run_id)])

    def test_retry_failure_has_no_artifact_and_success_writes_one_final_artifact(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            store = ArtifactStore()
            information_attempts = 0

            async def executor(context):
                nonlocal information_attempts
                task = context.task
                if task.task_type == SEARCH_INFORMATION_TASK:
                    information_attempts += 1
                    if information_attempts == 1:
                        raise RuntimeError("temporary retrieval failure")
                artifact_type = {
                    SEARCH_INFORMATION_TASK: "knowledge",
                    COMPLETE_SURFACE_TASK: "complete_surface",
                }[task.task_type]
                from final_framework.task_manager import TaskResult
                return TaskResult(
                    task.task_id, "succeeded", _artifact(task, artifact_type),
                    evidence_ref=task.evidence_ref,
                )

            for task_type in (SEARCH_INFORMATION_TASK, COMPLETE_SURFACE_TASK):
                registry.bind(task_type, executor)
            manager = TaskManager(registry, store, id_factory=_ids())
            submitted = await submit_plan(manager, _plan(with_media=False))
            initial = await manager.wait_for_run(submitted.run_id)
            information = next(
                record for record in initial.records if record.task_type == SEARCH_INFORMATION_TASK
            )
            await manager.retry_task(initial.run_id, information.task_id)
            return initial, await manager.wait_for_run(initial.run_id), store, information_attempts

        initial, final, store, attempts = asyncio.run(scenario())
        initial_information = next(
            record for record in initial.records if record.task_type == SEARCH_INFORMATION_TASK
        )
        final_information = next(
            record for record in final.records if record.task_type == SEARCH_INFORMATION_TASK
        )
        self.assertEqual(initial_information.status, "FAILED")
        self.assertEqual(final_information.status, "SUCCEEDED")
        self.assertEqual(final_information.attempt, 1)
        self.assertEqual(attempts, 2)
        self.assertIsNotNone(store.artifact_for_evidence(final.run_id, "#E1"))
        self.assertEqual(
            len([
                artifact
                for artifact in store.artifacts_for_run(final.run_id)
                if artifact.evidence_ref == "#E1"
            ]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
