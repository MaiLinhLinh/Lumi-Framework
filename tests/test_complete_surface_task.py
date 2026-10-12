"""Completion executor regression for Surface Retrieval Plans."""
from __future__ import annotations
import asyncio
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from final_framework.artifacts import ArtifactStore
from final_framework.executors.complete_surface import CompleteSurfaceTaskExecutor, bind_complete_surface_task
from final_framework.executors.registry import COMPLETE_SURFACE_TASK, build_default_executor_registry
from final_framework.registry_composition import load_framework_registries
from final_framework.surface.assembly import SurfaceAssemblyAgent
from final_framework.surface.contracts import SurfaceDraft
from final_framework.surface.final_compiler import FinalSurfaceCompiler
from final_framework.settings import Settings
from final_framework.task_manager import Artifact, SurfaceRetrievalPlan, TaskExecutionContext, TaskRecord

ROOT = Path(__file__).resolve().parents[1]
class _Completions:
    def __init__(self, content: str | list[str]) -> None:
        self._contents = [content] if isinstance(content, str) else list(content)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._contents:
            raise AssertionError("unexpected additional Assembly call")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._contents.pop(0)))]
        )


class _Client:
    def __init__(self, completions: _Completions) -> None:
        self.chat = SimpleNamespace(completions=completions)


class _MediaReferences:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def register_worker_result(self, **kwargs):
        self.calls.append(kwargs)
        return ()

    def get(self, **kwargs):
        media_id = kwargs["media_id"]
        if media_id != "media_cat_01":
            raise AssertionError(f"unexpected media resolution: {media_id}")
        return SimpleNamespace(
            url="/media/run_1/media_cat_01",
            caption="Ảnh một con mèo rõ chủ thể",
            source_uri="local://cats/cat.png",
            mime_type="image/png",
            local_file_missing=False,
        )


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


def _flashcard_draft() -> dict:
    return {
        "surface": {
            "blocks": [
                {
                    "widget_id": "text",
                    "description": "Tiêu đề đã do Task Manager Agent tạo.",
                    "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                    "props": {"content": "Học tiếng Anh cùng chú mèo", "role": "title"},
                },
                {
                    "widget_id": "flashcard",
                    "description": "Thẻ từ vựng về con mèo; mặt trước cần ảnh mèo từ evidence được giao.",
                    "grid": {"col": 3, "row": 2, "col_span": 10, "row_span": 6},
                    "props": {
                        "front": {"text": "Con vật này là gì?"},
                        "back": {"word": "cat", "phonetic": "/kæt/", "meaning": "con mèo"},
                    },
                },
            ]
        }
    }


def _plan():
    draft = _flashcard_draft()
    draft["surface"]["blocks"][1]["slot_role"] = "cat_flashcard"
    return SurfaceRetrievalPlan.from_dict({
        "action": "create_surface_plan", "template_description": "Thẻ học tiếng Anh.",
        **draft,
        "tasks": [{"evidence_ref": "#E1", "task_type": "search_image_task", "target_slots": ["cat_flashcard"], "instruction": "Tìm ảnh mèo."}],
    })


def _context():
    return TaskExecutionContext(
        run_id="run_1", surface_plan=_plan(),
        task=TaskRecord("run_1", "task_complete", COMPLETE_SURFACE_TASK, "QUEUED"),
    )


def _card_surface():
    return {"surface": {"blocks": [_plan().blocks[1].to_dict()]}}


def _media_task_output(media_id="media_cat_01"):
    task = _plan().tasks[0]
    return {
        task.evidence_ref: {
            "task": task.to_dict(),
            "output_contract": {
                "type": "object",
                "properties": {"items": {"type": "array"}},
            },
            "output_data": {"items": [{"media_id": media_id}]},
        }
    }


class CompleteSurfaceTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registries = load_framework_registries(ROOT)
        cls.widgets = registries.widget_registry
        cls.assets = registries.asset_catalog()

    def _agent(self, completions):
        return SurfaceAssemblyAgent(_settings(), self.widgets, client_factory=lambda **_: _Client(completions))

    def _store(self, *, with_media=False):
        store = ArtifactStore()
        store.write(Artifact(
            artifact_id="draft_1", artifact_type="surface_draft", run_id="run_1",
            produced_by="task_draft",
            data={**SurfaceDraft(blocks=_plan().blocks).to_dict(), "surface_id": "panel-run_1"},
        ))
        if with_media:
            store.write(Artifact(
                artifact_id="media_1", artifact_type="media", run_id="run_1", produced_by="task_media",
                evidence_ref="#E1", data={
                    "items": [{"media_id": "media_cat_01"}],
                    "references": [{"media_id": "media_cat_01", "package_id": "local_media", "caption": "Ảnh một con mèo rõ chủ thể", "object_key": "cats/cat.png", "mime_type": "image/png", "source_uri": "local://cats/cat.png", "local_file_missing": False}],
                },
            ))
        return store

    def _executor(self, agent, store, media):
        return CompleteSurfaceTaskExecutor(
            agent, FinalSurfaceCompiler(self.widgets, asset_catalog=self.assets, media_reference_store=media), store, media,
        )

    def test_executor_preserves_evidence_refs_and_direct_content(self):
        assembled = _card_surface()
        assembled["surface"]["blocks"][0]["props"]["front"]["media_id"] = "media_cat_01"
        model = _Completions(json.dumps(assembled))
        media = _MediaReferences()
        result = asyncio.run(self._executor(self._agent(model), self._store(with_media=True), media)(_context()))
        payload = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["task_outputs"][0]["task"], _plan().tasks[0].to_dict())
        self.assertEqual(
            payload["task_outputs"][0]["output_data"],
            {"items": [{"media_id": "media_cat_01", "caption": "Ảnh một con mèo rõ chủ thể"}]},
        )
        self.assertNotIn("available_data", payload)
        self.assertNotIn("evidence_by_ref", payload)
        self.assertNotIn("source_uri", payload["task_outputs"][0]["output_data"]["items"][0])
        self.assertEqual(
            result.artifact.data["semantic_surface"]["surface"]["blocks"][1]["props"]["back"]["meaning"],
            "con mèo",
        )
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.metrics, {
            "surface_status": "ready",
            "missing_parts": [],
        })

    def test_direct_content_overwrite_returns_machine_feedback(self):
        repaired = _card_surface()
        invalid = copy.deepcopy(repaired)
        invalid["surface"]["blocks"][0]["props"]["back"]["meaning"] = "Nội dung sai"
        model = _Completions([json.dumps(invalid), json.dumps(repaired)])
        complete = asyncio.run(self._agent(model).assemble(
            draft=SurfaceDraft(blocks=_plan().blocks), surface_plan=_plan(),
            task_outputs_by_ref=_media_task_output(), run_id="run_1",
        ))
        self.assertEqual(complete.draft.blocks[1].props["back"]["meaning"], "con mèo")
        feedback = json.loads(model.calls[1]["messages"][-1]["content"])
        self.assertEqual(feedback["errors"], [{"path": "surface.blocks[0].props.back.meaning", "code": "must_match_draft"}])

    def test_missing_media_stays_unresolved_and_runtime_emits_placeholder_warning(self):
        model = _Completions([])
        result = asyncio.run(self._executor(self._agent(model), self._store(), _MediaReferences())(_context()))
        self.assertEqual(model.calls, [])
        component = result.artifact.data["compiled_surface"]["components"][1]
        self.assertEqual(component["draft_placeholder"], _plan().blocks[1].description)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("giữ placeholder", result.warnings[0])
        self.assertEqual(result.metrics["surface_status"], "partial")
        self.assertEqual(result.metrics["missing_parts"], [{
            "description": _plan().blocks[1].description,
            "reason": "CONTENT_NOT_RENDERED",
        }])

    def test_media_id_not_in_slot_evidence_is_repaired(self):
        repaired = _card_surface()
        invalid = copy.deepcopy(repaired)
        invalid["surface"]["blocks"][0]["props"]["front"]["media_id"] = "media_dog_01"
        model = _Completions([json.dumps(invalid), json.dumps(repaired)])
        asyncio.run(self._agent(model).assemble(
            draft=SurfaceDraft(blocks=_plan().blocks), surface_plan=_plan(),
            task_outputs_by_ref=_media_task_output(), run_id="run_1",
        ))
        feedback = json.loads(model.calls[1]["messages"][-1]["content"])
        self.assertEqual(feedback["errors"], [{"path": "surface.blocks[0].props.front.media_id", "code": "not_in_output_data"}])

    def test_object_group_materializes_composite_media_once(self):
        plan = SurfaceRetrievalPlan.from_dict({
            "action": "create_surface_plan",
            "template_description": "Một nhóm quả dùng ảnh tổng hợp.",
            "surface": {"blocks": [{
                "widget_id": "object_group",
                "slot_role": "fruit_group",
                "description": "Ảnh tổng hợp chứa đúng ba quả táo.",
                "grid": {"col": 1, "row": 1, "col_span": 8, "row_span": 5},
                "props": {"count": 3, "label": "3 quả táo"},
            }]},
            "tasks": [{
                "evidence_ref": "#E1",
                "task_type": "draw_image_task",
                "target_slots": ["fruit_group"],
                "instruction": "Vẽ một ảnh tổng hợp có đúng ba quả táo.",
            }],
        })
        assembled = {"surface": {"blocks": [plan.blocks[0].to_dict()]}}
        assembled["surface"]["blocks"][0]["props"]["media_id"] = "media_cat_01"
        model = _Completions(json.dumps(assembled))
        complete = asyncio.run(self._agent(model).assemble(
            draft=SurfaceDraft(blocks=plan.blocks),
            surface_plan=plan,
            task_outputs_by_ref={
                "#E1": {
                    "task": plan.tasks[0].to_dict(),
                    "output_contract": {
                        "type": "object",
                        "properties": {"items": {"type": "array"}},
                    },
                    "output_data": {"items": [{"media_id": "media_cat_01"}]},
                }
            },
            run_id="run_1",
        ))

        compiled = FinalSurfaceCompiler(
            self.widgets,
            asset_catalog=self.assets,
            media_reference_store=_MediaReferences(),
        ).compile(complete, surface_id="panel_group", media_session_id="run_1")
        component = compiled.components[0]
        self.assertEqual(component.props["media_id"], "media_cat_01")
        self.assertEqual(component.props["count"], 3)
        self.assertEqual(component.props["source"]["url"], "/media/run_1/media_cat_01")
        self.assertEqual([anchor.anchor_key for anchor in compiled.anchors], ["group"])

    def test_binding_is_specific_to_complete_surface_task(self):
        registry = build_default_executor_registry()
        media = _MediaReferences()
        executor = bind_complete_surface_task(
            registry, self._agent(_Completions([])),
            FinalSurfaceCompiler(self.widgets, asset_catalog=self.assets, media_reference_store=media), ArtifactStore(), media,
        )
        self.assertIs(registry.resolve(COMPLETE_SURFACE_TASK), executor)
