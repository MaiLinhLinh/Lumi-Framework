"""Direct CP2 tests for task capability registration."""

from __future__ import annotations

import asyncio
import unittest

from final_framework.executors import (
    COMPLETE_SURFACE_TASK,
    DRAW_IMAGE_TASK,
    SEARCH_IMAGE_TASK,
    SEARCH_INFORMATION_TASK,
    ExecutorRegistryError,
    build_default_executor_registry,
)
from final_framework.task_manager import Artifact, TaskRecord, TaskResult


class ExecutorRegistryTests(unittest.TestCase):
    def test_declares_exactly_the_architecture_task_types(self) -> None:
        registry = build_default_executor_registry()
        self.assertEqual(registry.task_types(), (
                    SEARCH_INFORMATION_TASK,
            SEARCH_IMAGE_TASK,
            DRAW_IMAGE_TASK,
            COMPLETE_SURFACE_TASK,
        ))
        self.assertTrue(registry.supports(SEARCH_INFORMATION_TASK))
        self.assertFalse(registry.is_bound(SEARCH_INFORMATION_TASK))

    def test_declared_type_must_be_bound_before_resolution(self) -> None:
        registry = build_default_executor_registry()
        with self.assertRaisesRegex(ExecutorRegistryError, "no bound executor"):
            registry.resolve(SEARCH_INFORMATION_TASK)

        async def fake_executor(context) -> TaskResult:
            return TaskResult(
                context.task.task_id,
                "succeeded",
                Artifact(
                    artifact_id="artifact_1",
                    artifact_type="knowledge",
                    run_id=context.run_id,
                    produced_by=context.task.task_id,
                    data={},
                ),
                evidence_ref=context.task.evidence_ref,
            )

        registry.bind(SEARCH_INFORMATION_TASK, fake_executor)
        task = TaskRecord("run_1", "task_1", SEARCH_INFORMATION_TASK, "queued")
        from final_framework.task_manager import TaskExecutionContext
        from final_framework.tests.plan_fixtures import make_surface_plan
        plan = make_surface_plan()
        context = TaskExecutionContext("run_1", task, plan)
        result = asyncio.run(registry.resolve(SEARCH_INFORMATION_TASK)(context))
        self.assertEqual(result.status, "succeeded")

    def test_can_omit_temporarily_disabled_task_type(self) -> None:
        registry = build_default_executor_registry(enabled_task_types=(
                    SEARCH_INFORMATION_TASK,
            DRAW_IMAGE_TASK,
            COMPLETE_SURFACE_TASK,
        ))

        self.assertFalse(registry.supports(SEARCH_IMAGE_TASK))
        self.assertTrue(registry.supports(DRAW_IMAGE_TASK))


if __name__ == "__main__":
    unittest.main()
