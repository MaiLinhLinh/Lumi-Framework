"""CP7 regression tests for policy-owned fallback before Artifact commit."""

from __future__ import annotations

import asyncio
import itertools
import unittest

from final_framework.artifacts import ArtifactStore
from final_framework.async_tasks.resources import (
    InMemoryResourceStore,
    ResourceBatchLifecycle,
    ResourceReference,
)
from final_framework.executors import (
    COMPLETE_SURFACE_TASK,
    DRAW_IMAGE_TASK,
    SEARCH_IMAGE_TASK,
    build_default_executor_registry,
)
from final_framework.task_manager import (
    ACCEPT,
    REJECT_AND_FALLBACK,
    Artifact,
    SearchImageFallbackPolicy,
    TaskManager,
    TaskRecord,
    TaskResult,
)
from final_framework.tests.plan_fixtures import make_surface_plan, submit_plan


def _ids():
    counter = itertools.count(1)
    return lambda prefix: f"{prefix}_{next(counter)}"


def _artifact(context, artifact_type: str, data: dict) -> Artifact:
    return Artifact(
        artifact_id=f"artifact_{context.task.task_id}",
        artifact_type=artifact_type,
        run_id=context.run_id,
        produced_by=context.task.task_id,
        evidence_ref=context.task.evidence_ref,
        data=data,
    )


class FallbackPolicyTests(unittest.TestCase):
    def test_search_policy_uses_resource_metrics_not_artifact_shape(self) -> None:
        policy = SearchImageFallbackPolicy()
        task = TaskRecord(
            run_id="run_1",
            task_id="task_1",
            task_type=SEARCH_IMAGE_TASK,
            status="RUNNING",
            evidence_ref="#E1",
            instruction="Tìm ảnh con mèo.",
        )

        accepted = policy.evaluate(
            task,
            status="SUCCEEDED",
            metrics={"usable_resource_count": 1, "usable_resource_kinds": ["image"]},
        )
        rejected = policy.evaluate(
            task,
            status="SUCCEEDED",
            metrics={"usable_resource_count": 1, "usable_resource_kinds": ["audio"]},
        )

        self.assertEqual(accepted.action, ACCEPT)
        self.assertEqual(rejected.action, REJECT_AND_FALLBACK)
        self.assertEqual(rejected.error_code, "NO_USABLE_RESOURCE")
        self.assertEqual(rejected.fallback.task_type, DRAW_IMAGE_TASK)

    def test_rejected_search_rolls_back_resources_and_only_draw_artifact_survives(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            store = ArtifactStore()
            resources = InMemoryResourceStore(("audio/mpeg",))

            async def search(context):
                reference = ResourceReference(
                    resource_id="resource_wrong_kind",
                    owner_task_id=context.task.task_id,
                    attempt=context.task.attempt,
                    manifest_id=SEARCH_IMAGE_TASK,
                    kind="audio",
                    mime_type="audio/mpeg",
                    source_uri="https://cdn.example.com/not-an-image.mp3",
                )
                resources.register_batch("session_cp7", (reference,))
                return TaskResult(
                    task_id=context.task.task_id,
                    status="succeeded",
                    evidence_ref=context.task.evidence_ref,
                    artifact=_artifact(
                        context,
                        "async_task_output",
                        {"results": [{"resource_id": reference.resource_id}]},
                    ),
                    metrics={
                        "usable_resource_count": 1,
                        "usable_resource_kinds": ["audio"],
                    },
                    lifecycle=ResourceBatchLifecycle(
                        resources,
                        session_id="session_cp7",
                        owner_task_id=context.task.task_id,
                        attempt=context.task.attempt,
                    ),
                )

            async def draw(context):
                return TaskResult(
                    task_id=context.task.task_id,
                    status="succeeded",
                    evidence_ref=context.task.evidence_ref,
                    artifact=_artifact(
                        context,
                        "generated_image",
                        {"items": [{"media_id": "drawn_image"}]},
                    ),
                )

            async def complete(context):
                return TaskResult(
                    task_id=context.task.task_id,
                    status="succeeded",
                    artifact=Artifact(
                        artifact_id=f"complete_{context.task.task_id}",
                        artifact_type="complete_surface",
                        run_id=context.run_id,
                        produced_by=context.task.task_id,
                        data={},
                    ),
                )

            registry.bind(SEARCH_IMAGE_TASK, search)
            registry.bind(DRAW_IMAGE_TASK, draw)
            registry.bind(COMPLETE_SURFACE_TASK, complete)
            manager = TaskManager(registry, store, id_factory=_ids())
            submitted = await submit_plan(
                manager,
                make_surface_plan((("#E1", SEARCH_IMAGE_TASK, "Tìm ảnh con mèo."),)),
            )
            snapshot = await manager.wait_for_run(submitted.run_id)
            return manager, snapshot, store, resources

        manager, snapshot, store, resources = asyncio.run(scenario())
        search = next(
            record for record in snapshot.records if record.task_type == SEARCH_IMAGE_TASK
        )
        draw = next(
            record for record in snapshot.records if record.task_type == DRAW_IMAGE_TASK
        )

        self.assertEqual(search.status, "FAILED")
        self.assertEqual(search.error, "NO_USABLE_RESOURCE")
        self.assertIsNone(search.artifact_id)
        self.assertEqual(draw.status, "SUCCEEDED")
        self.assertEqual(draw.fallback_of_task_id, search.task_id)
        self.assertFalse(resources.contains("session_cp7", "resource_wrong_kind"))
        self.assertIsNone(store.artifact_for_task(snapshot.run_id, search.task_id))
        evidence = store.artifact_for_evidence(snapshot.run_id, "#E1")
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.produced_by, draw.task_id)
        self.assertEqual(evidence.artifact_type, "generated_image")
        self.assertEqual(
            [event.event for event in manager.events(snapshot.run_id)].count(
                "TASK_FALLBACK_SCHEDULED"
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
