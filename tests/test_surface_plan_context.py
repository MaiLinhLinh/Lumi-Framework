"""Runtime shares one Agent plan across workers and completion."""
import asyncio
import unittest

from final_framework.artifacts import ArtifactStore
from final_framework.executors import (
    COMPLETE_SURFACE_TASK, SEARCH_INFORMATION_TASK, build_default_executor_registry,
)
from final_framework.task_manager import Artifact, TaskManager, TaskResult
from final_framework.tests.plan_fixtures import make_surface_plan, submit_plan


class SurfacePlanContextTests(unittest.TestCase):
    def test_workers_and_completion_receive_plan_without_extra_ui_bindings(self):
        async def scenario():
            registry = build_default_executor_registry()
            store = ArtifactStore()
            received = []

            async def information(context):
                received.append(context)
                return TaskResult(
                    context.task.task_id, "succeeded", evidence_ref=context.task.evidence_ref,
                    artifact=Artifact(
                        artifact_id="knowledge_1", artifact_type="knowledge", run_id=context.run_id,
                        produced_by=context.task.task_id, evidence_ref=context.task.evidence_ref,
                        data={"items": [{"payload": {"passage": "Dữ liệu đã xác minh."}}]},
                    ),
                )

            async def complete(context):
                received.append(context)
                return TaskResult(
                    context.task.task_id,
                    "succeeded",
                    Artifact(
                        artifact_id="complete_1",
                        artifact_type="complete_surface",
                        run_id=context.run_id,
                        produced_by=context.task.task_id,
                        data={},
                    ),
                )

            registry.bind(SEARCH_INFORMATION_TASK, information)
            registry.bind(COMPLETE_SURFACE_TASK, complete)
            manager = TaskManager(registry, store)
            plan = make_surface_plan((("#E1", SEARCH_INFORMATION_TASK, "Tìm passage đã xác minh."),))
            submitted = await submit_plan(manager, plan)
            return await manager.wait_for_run(submitted.run_id), received, plan, store

        snapshot, contexts, plan, store = asyncio.run(scenario())
        self.assertTrue(snapshot.barrier_open)
        self.assertEqual(len(contexts), 2)
        for context in contexts:
            self.assertEqual(context.surface_plan, plan)
            self.assertFalse(hasattr(context, "experience_plan"))
            self.assertFalse(hasattr(context, "goal"))
            self.assertFalse(hasattr(context.task, "target_slots"))
        self.assertTrue(all(not hasattr(a, "target_slots") for a in store.artifacts_for_run(snapshot.run_id)))
