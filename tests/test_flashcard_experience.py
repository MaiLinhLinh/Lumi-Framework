"""CP-E regression tests for the semantic flashcard experience."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from final_framework.artifacts import ArtifactStore
from final_framework.catalogs.resources import SharedResourceRegistry
from final_framework.executors.complete_surface import CompleteSurfaceTaskExecutor
from final_framework.executors.registry import (
    COMPLETE_SURFACE_TASK,
    DRAW_IMAGE_TASK,
    SEARCH_IMAGE_TASK,
    build_default_executor_registry,
)
from final_framework.extension_loader import ExtensionLoader
from final_framework.surface import DraftSurfaceCompiler, SurfaceDraft
from final_framework.task_manager_agent import TaskManagerAgent, TaskManagerAgentRequest
from final_framework.surface.assembly import SurfaceAssemblyAgent
from final_framework.surface.final_compiler import FinalSurfaceCompiler
from final_framework.settings import Settings
from final_framework.task_manager import (
    Artifact,
    SurfaceRetrievalPlan,
    TaskExecutionContext,
    TaskManager,
    TaskResult,
)


ROOT = Path(__file__).resolve().parents[1]


def _settings() -> Settings:
    return Settings(
        gemini_live_api_key="live",
        gemini_live_model="live-model",
        gemini_live_voice="voice",
        live_turn_timeout_seconds=45,
        live_idle_timeout_seconds=900,
        live_reconnect_grace_seconds=30,
        presentation_animation_delay_ms=0,
        cerebras_api_key="assembly-key",
        cerebras_assembly_model="gpt-oss-120b",
    )


def _plan() -> SurfaceRetrievalPlan:
    return SurfaceRetrievalPlan.from_dict({
        "action": "create_surface_plan", "template_description": "Một flashcard có thể lật.",
        **_flashcard_draft(),
        "tasks": [{"evidence_ref": "#E1", "task_type": "search_image_task", "target_slots": ["cat_flashcard"], "instruction": "Tìm ảnh một con mèo rõ chủ thể, phù hợp cho trẻ em."}],
    })


def _flashcard_draft() -> dict:
    return {
        "surface": {
            "blocks": [
                {
                    "widget_id": "text",
                    "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                    "props": {"content": "Học tiếng Anh cùng chú mèo", "role": "title"},
                },
                {
                    "widget_id": "flashcard",
                    "slot_role": "cat_flashcard",
                    "description": "Thẻ học từ vựng về con mèo. Front cần media phù hợp từ task output để điền media_id; giữ câu hỏi. Back giữ word, phonetic và meaning đã có.",
                    "grid": {"col": 3, "row": 2, "col_span": 10, "row_span": 6},
                    "props": {
                        "front": {"text": "Con vật này là gì?"},
                        "back": {"word": "cat", "phonetic": "/kæt/", "meaning": "con mèo"},
                    },
                },
                {
                    "widget_id": "text",
                    "grid": {"col": 1, "row": 9, "col_span": 16, "row_span": 1},
                    "props": {"content": "Chạm vào thẻ để lật và xem đáp án nhé!", "role": "subtitle"},
                },
            ]
        }
    }


class _SurfaceCompletions:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)

    async def create(self, **_: object) -> object:
        return self._responses.pop(0)


class _SurfaceClient:
    def __init__(self, responses: list[object]) -> None:
        self.chat = SimpleNamespace(completions=_SurfaceCompletions(responses))


def _surface_completion(*, tool_calls: tuple[object, ...] = (), content: str | None = None) -> object:
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(tool_calls=list(tool_calls), content=content),
    )])


def _describe_call(call_id: str, widget_id: str) -> object:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name="describe_widget", arguments=json.dumps({"widget_id": widget_id})),
    )


class _AssemblyCompletions:
    """Assembly fake which maps the first usable media item into flashcard front."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        messages = kwargs["messages"]
        payload = json.loads(messages[-1]["content"])
        result = json.loads(json.dumps(payload["surface_draft"], ensure_ascii=False))
        media_id = next(
            (
                item["media_id"]
                for task_output in payload["task_outputs"]
                for item in task_output["output_data"].get("items", [])
                if isinstance(item.get("media_id"), str)
            ),
            None,
        )
        if media_id is not None:
            result["surface"]["blocks"][0]["props"]["front"]["media_id"] = media_id
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(result, ensure_ascii=False)),
        )])


class _AssemblyClient:
    def __init__(self, completions: _AssemblyCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


class _MediaReferences:
    def __init__(self) -> None:
        self.registered: list[dict] = []

    def register_worker_result(self, **kwargs: object) -> tuple[()]:
        self.registered.append(dict(kwargs))
        return ()

    def get(self, *, media_id: str, **_: object) -> object:
        return SimpleNamespace(
            url=f"/media/{media_id}",
            caption=f"Ảnh {media_id}",
            source_uri=f"internal://{media_id}",
            mime_type="image/png",
            local_file_missing=False,
        )


class FlashcardExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.widgets = ExtensionLoader(ROOT / "extensions").load().widget_registry
        cls.assets = SharedResourceRegistry(ROOT / "resources").load().assets

    def test_task_manager_agent_selects_one_flashcard_and_keeps_direct_vocabulary(self) -> None:
        draft = _plan().to_dict()
        client = _SurfaceClient([
            _surface_completion(tool_calls=(
                _describe_call("text", "text"),
                _describe_call("flashcard", "flashcard"),
            )),
            _surface_completion(content=json.dumps(draft, ensure_ascii=False)),
        ])
        planner = TaskManagerAgent(
            api_key="key",
            model="model",
            widget_registry=self.widgets,
            template_catalog=SharedResourceRegistry(ROOT / "resources").load().templates,
            draft_compiler=DraftSurfaceCompiler(self.widgets, self.assets),
            client_factory=lambda **_: client,
        )
        result = asyncio.run(planner.plan(TaskManagerAgentRequest(
            request="Tạo flashcard học tiếng Anh về con mèo cho trẻ em."
        )))

        self.assertEqual([block.widget_id for block in result.blocks], ["text", "flashcard", "text"])
        self.assertEqual(sum(block.widget_id == "flashcard" for block in result.blocks), 1)
        self.assertNotIn("choice", [block.widget_id for block in result.blocks])
        self.assertEqual(result.blocks[1].props["back"], {
            "word": "cat", "phonetic": "/kæt/", "meaning": "con mèo",
        })

    def test_search_media_fills_flashcard_front_without_draw_fallback(self) -> None:
        snapshot, store, assembly = asyncio.run(self._run("search_media"))

        self.assertFalse(any(record.task_type == DRAW_IMAGE_TASK for record in snapshot.records))
        compiled = self._compiled(store)
        card = compiled["components"][1]
        self.assertEqual(card["props"]["front"]["media_id"], "media_search_cat")
        self.assertEqual(card["props"]["back"], {
            "word": "cat", "phonetic": "/kæt/", "meaning": "con mèo",
        })
        self.assertIsNone(card.get("draft_placeholder"))
        self.assertEqual(len(assembly.calls), 1)

    def test_empty_search_uses_draw_fallback_and_fills_flashcard_front(self) -> None:
        snapshot, store, _ = asyncio.run(self._run("empty_search_draws"))

        draw_records = [record for record in snapshot.records if record.task_type == DRAW_IMAGE_TASK]
        self.assertEqual(len(draw_records), 1)
        self.assertEqual(draw_records[0].status, "SUCCEEDED")
        self.assertEqual(draw_records[0].fallback_of_task_id, "task_search")
        card = self._compiled(store)["components"][1]
        self.assertEqual(card["props"]["front"]["media_id"], "media_draw_cat")
        self.assertIsNone(card.get("draft_placeholder"))

    def test_failed_search_and_draw_keep_flashcard_placeholder_and_runtime_warning(self) -> None:
        snapshot, store, _ = asyncio.run(self._run("search_and_draw_fail"))

        statuses = {record.task_type: record.status for record in snapshot.records}
        self.assertEqual(statuses[SEARCH_IMAGE_TASK], "FAILED")
        self.assertEqual(statuses[DRAW_IMAGE_TASK], "FAILED")
        complete = next(record for record in snapshot.records if record.task_type == COMPLETE_SURFACE_TASK)
        self.assertEqual(complete.status, "SUCCEEDED")
        card = self._compiled(store)["components"][1]
        self.assertEqual(card["props"], {})
        self.assertEqual(card["draft_placeholder"], _flashcard_draft()["surface"]["blocks"][1]["description"])
        complete_artifact = next(
            artifact for artifact in store.artifacts_for_run("run_flashcard")
            if artifact.artifact_type == "complete_surface"
        )
        self.assertNotIn("warnings", complete_artifact.data)
        events = [event for event in self._manager_events]
        self.assertTrue(any(event.event == "TASK_FAILED" and event.task_type == DRAW_IMAGE_TASK for event in events))

    async def _run(self, mode: str):
        identifiers = iter(("run_flashcard", "task_surface", "task_search", "task_draw", "task_complete"))
        registry = build_default_executor_registry()
        store = ArtifactStore()
        self._manager_events = []
        assembly_completions = _AssemblyCompletions()
        media_references = _MediaReferences()
        assembly = SurfaceAssemblyAgent(
            _settings(), self.widgets,
            client_factory=lambda **_: _AssemblyClient(assembly_completions),
        )
        registry.bind(
            COMPLETE_SURFACE_TASK,
            CompleteSurfaceTaskExecutor(
                assembly,
                FinalSurfaceCompiler(self.widgets, asset_catalog=self.assets, media_reference_store=media_references),
                store,
                media_references,
            ),
        )



        async def search(context: TaskExecutionContext) -> TaskResult:
            if mode == "search_and_draw_fail":
                raise RuntimeError("search failed")
            items = [{"media_id": "media_search_cat"}] if mode == "search_media" else []
            references = [self._reference("media_search_cat")] if items else []
            return TaskResult(
                task_id=context.task.task_id,
                status="succeeded",
                evidence_ref=context.task.evidence_ref,
                metrics={
                    "usable_resource_count": len(items),
                    "usable_resource_kinds": ["image"] if items else [],
                },
                artifact=Artifact(
                    artifact_id=f"media_{context.task.task_id}",
                    artifact_type="media",
                    run_id=context.run_id,
                    produced_by=context.task.task_id,
                    evidence_ref=context.task.evidence_ref,
                    data={"items": items, "references": references},
                ),
            )

        async def draw(context: TaskExecutionContext) -> TaskResult:
            if mode == "search_and_draw_fail":
                raise RuntimeError("draw failed")
            return TaskResult(
                task_id=context.task.task_id,
                status="succeeded",
                evidence_ref=context.task.evidence_ref,
                artifact=Artifact(
                    artifact_id=f"generated_image_{context.task.task_id}",
                    artifact_type="generated_image",
                    run_id=context.run_id,
                    produced_by=context.task.task_id,
                    evidence_ref=context.task.evidence_ref,
                    data={
                        "items": [{"media_id": "media_draw_cat"}],
                        "references": [self._reference("media_draw_cat")],
                    },
                ),
            )

        registry.bind(SEARCH_IMAGE_TASK, search)
        registry.bind(DRAW_IMAGE_TASK, draw)
        manager = TaskManager(
            registry,
            store,
            event_sink=self._manager_events.append,
            id_factory=lambda _: next(identifiers),
        )
        submitted = await manager.submit_surface_plan(
            _plan(), draft=SurfaceDraft(blocks=_plan().blocks), surface_id="panel-flashcard",
        )
        snapshot = await manager.wait_for_run(submitted.run_id)
        return snapshot, store, assembly_completions

    @staticmethod
    def _reference(media_id: str) -> dict[str, object]:
        return {
            "media_id": media_id,
            "package_id": "test_media",
            "object_key": f"{media_id}.png",
            "mime_type": "image/png",
            "caption": "Ảnh một con mèo rõ chủ thể",
            "source_uri": f"internal://{media_id}",
            "local_file_missing": False,
        }

    @staticmethod
    def _compiled(store: ArtifactStore) -> dict:
        artifact = next(
            artifact for artifact in store.artifacts_for_run("run_flashcard")
            if artifact.artifact_type == "complete_surface"
        )
        return dict(artifact.data["compiled_surface"])


if __name__ == "__main__":
    unittest.main()
