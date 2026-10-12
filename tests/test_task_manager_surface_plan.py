"""CP4 runtime ingress tests for Task Manager Agent surface plans."""

from __future__ import annotations

import asyncio
import itertools
import unittest

from final_framework.artifacts import ArtifactStore
from final_framework.executors import (
    COMPLETE_SURFACE_TASK,
    SEARCH_INFORMATION_TASK,
    build_default_executor_registry,
)
from final_framework.registry_composition import load_framework_registries
from final_framework.surface import DraftSurfaceCompiler, SurfaceDraft
from pathlib import Path
from final_framework.task_manager import Artifact, SurfaceRetrievalPlan, TaskManager, TaskResult


class TaskManagerSurfacePlanTests(unittest.TestCase):
    def test_surface_plan_compiles_as_runtime_artifact_without_surface_task(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            contexts = []

            async def information(context):
                contexts.append(context)
                return TaskResult(
                    task_id=context.task.task_id,
                    status="succeeded",
                    artifact=Artifact(
                        artifact_id="knowledge_1",
                        artifact_type="knowledge",
                        run_id=context.run_id,
                        produced_by=context.task.task_id,
                        data={},
                        evidence_ref=context.task.evidence_ref,
                    ),
                    evidence_ref=context.task.evidence_ref,
                )

            async def complete(context):
                return TaskResult(
                    task_id=context.task.task_id,
                    status="succeeded",
                    artifact=Artifact(
                        artifact_id="complete_1",
                        artifact_type="complete_surface",
                        run_id=context.run_id,
                        produced_by=context.task.task_id,
                        data={},
                    ),
                )

            registry.bind(SEARCH_INFORMATION_TASK, information)
            registry.bind(COMPLETE_SURFACE_TASK, complete)
            artifact_store = ArtifactStore()
            counter = itertools.count(1)
            manager = TaskManager(
                registry,
                artifact_store,
                id_factory=lambda prefix: f"{prefix}_{next(counter)}",
            )
            plan = SurfaceRetrievalPlan.from_dict({
                "action": "create_surface_plan",
                "template_description": None,
                "surface": {"blocks": [{
                    "widget_id": "text",
                    "slot_role": "history_passage",
                    "description": "Đoạn tư liệu lịch sử sẽ được điền từ dữ liệu đã tìm.",
                    "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 4},
                    "props": {"content": "Đang chờ tư liệu", "role": "body"},
                }]},
                "tasks": [{
                    "evidence_ref": "#E1",
                    "task_type": "search_information_task",
                    "target_slots": ["history_passage"],
                    "instruction": "Tìm một đoạn tư liệu lịch sử ngắn, có nguồn.",
                }],
            })
            draft = SurfaceDraft(blocks=plan.blocks)
            registries = load_framework_registries(Path(__file__).resolve().parents[1])
            compiled_draft = DraftSurfaceCompiler(
                registries.widget_registry,
                registries.asset_catalog(),
            ).compile(draft, surface_id="panel_1")
            snapshot = await manager.submit_surface_plan(
                plan,
                draft=draft,
                compiled_draft=compiled_draft,
            )
            await manager.wait_for_run(snapshot.run_id)
            return manager.snapshot(snapshot.run_id), artifact_store, contexts

        snapshot, artifact_store, contexts = asyncio.run(scenario())

        self.assertNotIn("surface_task", [record.task_type for record in snapshot.records])
        self.assertIn("surface_draft", [record.task_type for record in snapshot.records])
        self.assertIn(COMPLETE_SURFACE_TASK, [record.task_type for record in snapshot.records])
        self.assertEqual(contexts[0].task.instruction, "Tìm một đoạn tư liệu lịch sử ngắn, có nguồn.")
        self.assertFalse(hasattr(contexts[0], "experience_plan"))
        self.assertEqual(contexts[0].surface_plan.tasks[0].evidence_ref, "#E1")
        draft_artifacts = [
            artifact for artifact in artifact_store.artifacts_for_run(snapshot.run_id)
            if artifact.artifact_type == "surface_draft"
        ]
        self.assertEqual(len(draft_artifacts), 1)
        self.assertEqual(draft_artifacts[0].data["surface_id"], "panel_1")
        self.assertEqual(
            draft_artifacts[0].data["anchor_manifest"]["anchors"],
            [{"anchor_id": "a1", "component_id": "1", "anchor_key": "text"}],
        )


if __name__ == "__main__":
    unittest.main()
