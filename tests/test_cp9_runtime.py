"""CP9 domainless Live/partial-panel contract tests (no provider calls)."""

from __future__ import annotations

import asyncio
import itertools
import unittest
from pathlib import Path

from final_framework.tests.plan_fixtures import make_surface_plan, submit_plan
from final_framework.artifacts import ArtifactStore
from final_framework.catalogs.resources import SharedResourceRegistry
from final_framework.executors import (
    COMPLETE_SURFACE_TASK,
    SEARCH_INFORMATION_TASK,
    build_default_executor_registry,
)
from final_framework.extension_loader import ExtensionLoader
from final_framework.live.registry import LiveToolRegistry
from final_framework.surface.assembly import CompleteSurface
from final_framework.surface.contracts import SurfaceDraft
from final_framework.surface.final_compiler import FinalSurfaceCompiler
from final_framework.task_manager import (
    Artifact,
    TaskManager,
    TaskResult,
)


ROOT = Path(__file__).resolve().parents[1]


class Cp9RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.widgets = ExtensionLoader(ROOT / "extensions").load().widget_registry
        self.assets = SharedResourceRegistry(ROOT / "resources").load().assets

    def test_live_registry_exposes_only_request_task_manager(self) -> None:
        declarations = LiveToolRegistry().tool_declarations()
        self.assertEqual([item["name"] for item in declarations], ["request_task_manager"])
        schema = declarations[0]["parameters"]
        self.assertEqual(set(schema["required"]), {"request"})
        self.assertNotIn("domain_id", schema["properties"])
        self.assertNotIn("slots", schema["properties"])
        self.assertNotIn("goal", schema["properties"])
        self.assertNotIn("tasks", schema["properties"])

    def test_live_registry_routes_natural_language_without_ui_binding(self) -> None:
        registry = LiveToolRegistry()
        guidance = registry.prompt_guidance()

        self.assertIn("request_task_manager", guidance)
        self.assertIn("flashcard", guidance)
        self.assertIn("widget", guidance)
        self.assertIn("target_slots", guidance)
        self.assertNotIn("submit_experience_plan", guidance)

    def test_final_compiler_keeps_partial_described_component_as_placeholder(self) -> None:
        complete = CompleteSurface(draft=SurfaceDraft.from_dict({
            "surface": {"blocks": [
                {
                    "widget_id": "text",
                    "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                    "props": {"content": "Tiêu đề", "role": "title"},
                },
                {
                    "widget_id": "text",
                    "description": "Nội dung đang chờ dữ liệu.",
                    "grid": {"col": 1, "row": 2, "col_span": 16, "row_span": 3},
                    "props": {},
                },
            ]}
        }))
        compiled = FinalSurfaceCompiler(self.widgets, asset_catalog=self.assets).compile(
            complete, surface_id="panel_1", media_session_id="run_1"
        )
        self.assertIsNone(compiled.components[0].draft_placeholder)
        self.assertEqual(compiled.components[1].draft_placeholder, "Nội dung đang chờ dữ liệu.")

    def test_complete_success_emits_complete_ready_event(self) -> None:
        async def scenario():
            registry = build_default_executor_registry()
            counter = itertools.count(1)

            async def executor(context):
                task = context.task
                artifact_type = "knowledge" if task.task_type == SEARCH_INFORMATION_TASK else "complete_surface"
                return TaskResult(
                    task_id=task.task_id,
                    status="succeeded",
                    artifact=Artifact(
                        artifact_id=f"artifact_{task.task_id}",
                        artifact_type=artifact_type,
                        run_id=task.run_id,
                        produced_by=task.task_id,
                        data={"items": []},
                        evidence_ref=task.evidence_ref,
                    ),
                    evidence_ref=task.evidence_ref,
                )

            for task_type in (SEARCH_INFORMATION_TASK, COMPLETE_SURFACE_TASK):
                registry.bind(task_type, executor)
            manager = TaskManager(
                registry, ArtifactStore(), id_factory=lambda prefix: f"{prefix}_{next(counter)}"
            )
            plan = make_surface_plan((("#E1", SEARCH_INFORMATION_TASK, "Tìm nội dung."),))
            submitted = await submit_plan(manager, plan)
            await manager.wait_for_run(submitted.run_id)
            return manager.events(submitted.run_id)

        events = asyncio.run(scenario())
        self.assertIn("COMPLETE_SURFACE_READY", [event.event for event in events])


if __name__ == "__main__":
    unittest.main()
