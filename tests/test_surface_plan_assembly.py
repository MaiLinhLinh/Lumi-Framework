"""CP5 regression: scoped evidence, preservation, placeholders and fallback."""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from final_framework.async_tasks import (
    AsyncTaskManifestRegistry,
    GenericHttpAsyncAgentExecutor,
    InMemoryRemoteJobRegistry,
)
from final_framework.artifacts import ArtifactStore
from final_framework.executors.complete_surface import CompleteSurfaceTaskExecutor
from final_framework.executors.registry import (
    COMPLETE_SURFACE_TASK, DRAW_IMAGE_TASK, SEARCH_IMAGE_TASK,
    SEARCH_INFORMATION_TASK, build_default_executor_registry,
)
from final_framework.registry_composition import load_framework_registries
from final_framework.surface import DraftSurfaceCompiler, SurfaceDraft
from final_framework.surface.assembly import (
    AssemblyValidationError, SurfaceAssemblyAgent, SurfaceAssemblyError,
)
from final_framework.surface.final_compiler import FinalSurfaceCompiler
from final_framework.task_manager import Artifact, SurfaceRetrievalPlan, TaskManager, TaskResult
from final_framework.tests.test_complete_surface_task import _settings
from final_framework.tests.test_async_task_executor import (
    _limits,
    _load_manifest,
    _manifest_dict,
    _no_sleep,
)


def _card(slot: str, col: int = 1) -> dict:
    vocabulary = {
        "cat": {"word": "cat", "phonetic": "/kæt/", "meaning": "con mèo"},
        "dog": {"word": "dog", "phonetic": "/dɒɡ/", "meaning": "con chó"},
    }
    return {
        "widget_id": "flashcard", "slot_role": slot,
        "description": f"Thẻ {slot} cần ảnh từ task được giao.",
        "grid": {"col": col, "row": 2, "col_span": 7, "row_span": 6},
        "props": {"front": {"text": "Đây là con gì?"}, "back": vocabulary[slot]},
    }


def _plan(blocks: list[dict], task_type: str = "search_image_task", tasks=None) -> SurfaceRetrievalPlan:
    return SurfaceRetrievalPlan.from_dict({
        "action": "create_surface_plan", "template_description": "Giữ khung Agent đã tạo.",
        "surface": {"blocks": blocks},
        "tasks": tasks if tasks is not None else [{
            "evidence_ref": f"#E{index}", "task_type": task_type,
            "target_slots": [block["slot_role"]], "instruction": f"Tìm dữ liệu cho {block['slot_role']}.",
        } for index, block in enumerate(blocks, 1) if block.get("slot_role")],
    })


def _media(media_id: str) -> tuple[dict, ...]:
    return ({"kind": "media", "data": {"items": [{"media_id": media_id}]}},)


def _text(passage: str) -> tuple[dict, ...]:
    return ({"kind": "knowledge", "data": {"items": [{"passage": passage}]}},)


def _task_outputs(plan: SurfaceRetrievalPlan, evidence: dict) -> dict:
    """Adapt concise fixtures to the provider-neutral CP5 boundary."""

    result = {}
    tasks = {task.evidence_ref: task for task in plan.tasks}
    for evidence_ref, entries in evidence.items():
        if not entries:
            continue
        entry = entries[0]
        data = entry["data"]
        if not data.get("items") or not any(data["items"]):
            continue
        result[evidence_ref] = {
            "task": tasks[evidence_ref].to_dict() if evidence_ref in tasks else {},
            "output_contract": {
                "type": "object",
                "description": f"Test {entry['kind']} output.",
                "properties": {"items": {"type": "array"}},
            },
            "output_data": data,
        }
    return result


class _Completions:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    async def create(self, **kwargs):
        # Freeze messages: the repair loop mutates its own list later.
        self.calls.append(copy.deepcopy(kwargs))
        payload = json.loads(kwargs["messages"][1]["content"])
        result = copy.deepcopy(payload["surface_draft"])
        if self.transform is not None:
            result = self.transform(payload, result, len(self.calls))
        else:
            block = result["surface"]["blocks"][0]
            for task_output in payload["task_outputs"]:
                item = task_output["output_data"]["items"][0]
                if "media_id" in item:
                    block["props"]["front"]["media_id"] = item["media_id"]
                elif block["widget_id"] == "text":
                    block["props"]["content"] = item["passage"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))])


class _MediaReferences:
    def __init__(self):
        self.registered = []

    def register_worker_result(self, **kwargs):
        self.registered.append(kwargs)

    def get(self, *, media_id, **kwargs):
        return SimpleNamespace(
            url=f"/media/{media_id}", caption="Ảnh đã xác minh", source_uri=f"internal://{media_id}",
            mime_type="image/png", local_file_missing=False,
        )


class SurfacePlanAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registries = load_framework_registries(Path(__file__).resolve().parents[1])

    def _agent(self, completions):
        return SurfaceAssemblyAgent(
            _settings(), self.registries.widget_registry,
            client_factory=lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        )

    def _assemble(self, plan, evidence, completions):
        return asyncio.run(self._agent(completions).assemble(
            draft=SurfaceDraft(blocks=plan.blocks), surface_plan=plan,
            task_outputs_by_ref=_task_outputs(plan, evidence), run_id="run_cp5",
        ))

    def test_two_media_slots_receive_only_their_own_evidence(self):
        plan = _plan([_card("cat"), _card("dog", 9)])
        model = _Completions()
        complete = self._assemble(plan, {"#E1": _media("cat_photo"), "#E2": _media("dog_photo")}, model)
        self.assertEqual([b.props["front"]["media_id"] for b in complete.draft.blocks], ["cat_photo", "dog_photo"])
        for call, ref, slot in zip(model.calls, ("#E1", "#E2"), ("cat", "dog"), strict=True):
            payload = json.loads(call["messages"][1]["content"])
            self.assertEqual(
                [output["task"]["evidence_ref"] for output in payload["task_outputs"]],
                [ref],
            )
            self.assertEqual(payload["task_outputs"][0]["task"]["target_slots"], [slot])
            self.assertNotIn("available_data", payload)
            self.assertNotIn("evidence_by_ref", payload)
            self.assertEqual(len(payload["surface_draft"]["surface"]["blocks"]), 1)
        self.assertEqual(complete.draft.blocks[0].props["back"], plan.blocks[0].props["back"])

    def test_cross_slot_media_is_rejected_and_repaired(self):
        def transform(payload, result, turn):
            block = result["surface"]["blocks"][0]
            block["props"]["front"]["media_id"] = (
                "dog_photo" if turn == 1 or block["slot_role"] == "dog" else "cat_photo"
            )
            return result
        plan = _plan([_card("cat"), _card("dog", 9)])
        model = _Completions(transform)
        complete = self._assemble(plan, {"#E1": _media("cat_photo"), "#E2": _media("dog_photo")}, model)
        self.assertEqual(complete.draft.blocks[0].props["front"]["media_id"], "cat_photo")
        feedback = json.loads(model.calls[1]["messages"][-1]["content"])
        self.assertEqual(feedback["errors"], [{"path": "surface.blocks[0].props.front.media_id", "code": "not_in_output_data"}])
        self.assertEqual(complete.draft.blocks[1].props["front"]["media_id"], "dog_photo")

    def test_scoped_existing_content_change_is_rejected_and_repaired(self):
        def transform(payload, result, turn):
            props = result["surface"]["blocks"][0]["props"]
            props["front"]["media_id"] = "cat_photo"
            if turn == 1:
                props["back"]["word"] = "dog"
            return result

        plan = _plan([_card("cat")])
        model = _Completions(transform)
        complete = self._assemble(plan, {"#E1": _media("cat_photo")}, model)
        self.assertEqual(complete.draft.blocks[0].props["back"], plan.blocks[0].props["back"])
        feedback = json.loads(model.calls[1]["messages"][-1]["content"])
        self.assertEqual(feedback["errors"], [{
            "path": "surface.blocks[0].props.back.word", "code": "must_match_draft",
        }])

    def test_child_media_uses_parent_slot_evidence_and_preserves_tree(self):
        def transform(payload, result, turn):
            result["surface"]["blocks"][0]["children"][0]["props"]["media_id"] = (
                "dog_photo" if turn == 1 else "cat_photo"
            )
            return result

        choice = {
            "widget_id": "choice", "slot_role": "cat", "description": "Lựa chọn ảnh mèo.",
            "grid": {"col": 1, "row": 2, "col_span": 7, "row_span": 6}, "props": {},
            "children": [
                {"widget_id": "image", "props": {}},
                {"widget_id": "text", "props": {"content": "Mèo", "role": "label"}},
            ],
        }
        plan = _plan([choice])
        draft = SurfaceDraft(blocks=plan.blocks)
        DraftSurfaceCompiler(self.registries.widget_registry, self.registries.asset_catalog()).compile(draft)
        model = _Completions(transform)
        complete = self._assemble(plan, {"#E1": _media("cat_photo")}, model)
        block = complete.draft.blocks[0]
        self.assertEqual(tuple(child.widget_id for child in block.children), ("image", "text"))
        self.assertEqual(block.children[0].props["media_id"], "cat_photo")
        self.assertEqual(block.children[1], plan.blocks[0].children[1])
        self.assertEqual(block.grid, plan.blocks[0].grid)
        feedback = json.loads(model.calls[1]["messages"][-1]["content"])
        self.assertEqual(feedback["errors"], [{
            "path": "surface.blocks[0].children[0].props.media_id", "code": "not_in_output_data",
        }])

    def test_missing_slot_stays_unchanged_while_other_slot_has_evidence(self):
        plan = _plan([_card("cat"), _card("dog", 9)])
        model = _Completions()
        complete = self._assemble(plan, {"#E1": _media("cat_photo")}, model)
        self.assertEqual(complete.draft.blocks[1], plan.blocks[1])
        self.assertEqual(len(model.calls), 1)
        compiled = FinalSurfaceCompiler(
            self.registries.widget_registry, media_reference_store=_MediaReferences(),
        ).compile(complete, surface_id="panel", media_session_id="run_cp5")
        self.assertIsNone(compiled.components[0].draft_placeholder)
        self.assertEqual(compiled.components[1].draft_placeholder, plan.blocks[1].description)

    def test_no_usable_evidence_never_calls_model(self):
        plan = _plan([_card("cat")])
        for evidence in ({}, {"#E1": ()}, {"#E1": ({"kind": "media", "data": {"items": [{}]}},)}):
            with self.subTest(evidence=evidence):
                model = _Completions()
                complete = self._assemble(plan, evidence, model)
                self.assertEqual(complete.draft.blocks, plan.blocks)
                self.assertEqual(model.calls, [])

    def test_text_slots_receive_only_their_own_passages(self):
        blocks = [{
            "widget_id": "text", "slot_role": name, "description": f"Tư liệu {name}",
            "grid": {"col": col, "row": 1, "col_span": 7, "row_span": 4}, "props": {"role": "body"},
        } for name, col in (("first", 1), ("second", 9))]
        plan = _plan(blocks, "search_information_task")
        model = _Completions()
        complete = self._assemble(plan, {"#E1": _text("Tư liệu A"), "#E2": _text("Tư liệu B")}, model)
        self.assertEqual([b.props["content"] for b in complete.draft.blocks], ["Tư liệu A", "Tư liệu B"])
        first_payload = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertNotIn(
            "Tư liệu B",
            first_payload["task_outputs"][0]["output_data"]["items"][0]["passage"],
        )

    def test_shared_ref_can_fill_multiple_declared_slots(self):
        tasks = [{"evidence_ref": "#E1", "task_type": "search_image_task", "target_slots": ["cat", "dog"], "instruction": "Ảnh dùng chung."}]
        plan = _plan([_card("cat"), _card("dog", 9)], tasks=tasks)
        complete = self._assemble(plan, {"#E1": _media("shared_photo")}, _Completions())
        self.assertEqual([b.props["front"]["media_id"] for b in complete.draft.blocks], ["shared_photo", "shared_photo"])

    def test_direct_block_and_existing_props_are_preserved(self):
        title = {"widget_id": "text", "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1}, "props": {"content": "Tiêu đề", "role": "title"}}
        plan = _plan([title, _card("cat")])
        model = _Completions()
        complete = self._assemble(plan, {"#E2": _media("cat_photo")}, model)
        self.assertEqual(complete.draft.blocks[0], plan.blocks[0])
        self.assertEqual(complete.draft.blocks[1].grid, plan.blocks[1].grid)
        self.assertEqual(complete.draft.blocks[1].props["back"], plan.blocks[1].props["back"])
        self.assertEqual(len(model.calls), 1)

    def test_new_external_source_is_rejected(self):
        def transform(payload, result, turn):
            result["surface"]["blocks"][0]["props"]["front"]["url"] = "https://invented.invalid/cat.png"
            return result
        with self.assertRaises(AssemblyValidationError):
            self._assemble(_plan([_card("cat")]), {"#E1": _media("cat_photo")}, _Completions(transform))

    def test_unknown_ref_and_mismatched_task_context_are_rejected(self):
        plan = _plan([_card("cat")])
        with self.assertRaises(SurfaceAssemblyError):
            self._assemble(plan, {"#E99": _media("cat_photo")}, _Completions())
        output = _task_outputs(plan, {"#E1": _media("cat_photo")})
        output["#E1"]["task"]["instruction"] = "changed"
        with self.assertRaises(SurfaceAssemblyError):
            asyncio.run(self._agent(_Completions()).assemble(
                draft=SurfaceDraft(blocks=plan.blocks),
                surface_plan=plan,
                task_outputs_by_ref=output,
                run_id="run_cp5",
            ))

    async def _run(self, mode, *, text=False):
        if text:
            blocks = [{"widget_id": "text", "slot_role": "passage", "description": "Tư liệu cần tìm", "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 4}, "props": {"role": "body"}}]
            plan = _plan(blocks, "search_information_task")
        else:
            plan = _plan([_card("cat")])
        store = ArtifactStore()
        registry = build_default_executor_registry()
        model = _Completions()
        media = _MediaReferences()
        counter = itertools.count(1)
        events = []
        complete_results = []
        complete_executor = CompleteSurfaceTaskExecutor(
            self._agent(model), FinalSurfaceCompiler(self.registries.widget_registry, media_reference_store=media), store, media,
        )

        async def complete_surface(context):
            result = await complete_executor(context)
            complete_results.append(result)
            return result

        registry.bind(COMPLETE_SURFACE_TASK, complete_surface)

        async def search(context):
            if mode in {"search_fail", "both_fail"}:
                raise RuntimeError("search failed in CP5 scenario")
            if text:
                data = {"items": [{"payload": {"passage": "Tư liệu đã tìm được."}}]}
                kind = "knowledge"
            else:
                media_id = "cat_search"
                data = {"items": [] if mode == "empty" else [{"media_id": media_id}], "references": [] if mode == "empty" else [self._reference(media_id)]}
                kind = "media"
            artifact = Artifact(
                artifact_id=f"result_{context.task.task_id}", artifact_type=kind,
                run_id=context.run_id, produced_by=context.task.task_id,
                evidence_ref=context.task.evidence_ref, data=data,
            )
            metrics = {}
            if context.task.task_type == SEARCH_IMAGE_TASK:
                count = len(data["items"])
                metrics = {
                    "usable_resource_count": count,
                    "usable_resource_kinds": ["image"] if count else [],
                }
            return TaskResult(
                task_id=context.task.task_id,
                status="succeeded",
                evidence_ref=context.task.evidence_ref,
                artifact=artifact,
                metrics=metrics,
            )

        async def draw(context):
            if mode == "both_fail":
                raise RuntimeError("draw failed in CP5 scenario")
            artifact = Artifact(
                artifact_id=f"result_{context.task.task_id}", artifact_type="generated_image",
                run_id=context.run_id, produced_by=context.task.task_id,
                evidence_ref=context.task.evidence_ref,
                data={"items": [{"media_id": "cat_draw"}], "references": [self._reference("cat_draw")]},
            )
            return TaskResult(task_id=context.task.task_id, status="succeeded", evidence_ref=context.task.evidence_ref, artifact=artifact)

        registry.bind(SEARCH_IMAGE_TASK, search)
        registry.bind(SEARCH_INFORMATION_TASK, search)
        registry.bind(DRAW_IMAGE_TASK, draw)
        manager = TaskManager(registry, store, event_sink=events.append, id_factory=lambda prefix: f"{prefix}_{next(counter)}")
        draft = SurfaceDraft(blocks=plan.blocks)
        DraftSurfaceCompiler(self.registries.widget_registry, self.registries.asset_catalog()).compile(draft)
        submitted = await manager.submit_surface_plan(plan, draft=draft, surface_id="panel_cp5")
        snapshot = await manager.wait_for_run(submitted.run_id)
        artifacts = store.artifacts_for_run(snapshot.run_id)
        final = next(a for a in artifacts if a.artifact_type == "complete_surface")
        compiled = final.data["compiled_surface"]
        complete = final.data["semantic_surface"]
        return snapshot, compiled, complete, model, events, media, complete_results

    @staticmethod
    def _reference(media_id):
        return {"media_id": media_id, "package_id": "cp5_test", "object_key": f"{media_id}.png", "caption": "Ảnh đã tìm", "mime_type": "image/png", "source_uri": f"internal://{media_id}"}

    def test_runtime_text_evidence_completes_target(self):
        snapshot, compiled, _, model, _, _, _ = asyncio.run(self._run("success", text=True))
        self.assertEqual(compiled["components"][0]["props"]["content"], "Tư liệu đã tìm được.")
        self.assertTrue(all(r.status == "SUCCEEDED" for r in snapshot.records))
        payload = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["task_outputs"][0]["task"]["evidence_ref"], "#E1")

    def test_runtime_media_search_completes_target(self):
        snapshot, compiled, _, _, _, media, _ = asyncio.run(self._run("success"))
        self.assertEqual(compiled["components"][0]["props"]["front"]["media_id"], "cat_search")
        self.assertFalse(any(r.task_type == DRAW_IMAGE_TASK for r in snapshot.records))
        self.assertEqual(media.registered[0]["result"]["evidence"][0]["payload"]["media_id"], "cat_search")

    def test_runtime_empty_or_failed_search_draws_into_same_ref_and_slot(self):
        for mode in ("empty", "search_fail"):
            with self.subTest(mode=mode):
                snapshot, compiled, _, model, events, _, _ = asyncio.run(self._run(mode))
                draw = next(r for r in snapshot.records if r.task_type == DRAW_IMAGE_TASK)
                self.assertEqual(draw.evidence_ref, "#E1")
                self.assertIsNotNone(draw.fallback_of_task_id)
                self.assertEqual(compiled["components"][0]["props"]["front"]["media_id"], "cat_draw")
                payload = json.loads(model.calls[0]["messages"][1]["content"])
                self.assertEqual(payload["task_outputs"][0]["task"]["target_slots"], ["cat"])
                self.assertEqual(
                    [output["task"]["evidence_ref"] for output in payload["task_outputs"]],
                    ["#E1"],
                )
                self.assertTrue(any(e.event == "TASK_FALLBACK_SCHEDULED" for e in events))

    def test_runtime_failed_search_and_draw_leave_placeholder(self):
        snapshot, compiled, complete, model, events, _, complete_results = asyncio.run(self._run("both_fail"))
        statuses = {r.task_type: r.status for r in snapshot.records}
        self.assertEqual(statuses[SEARCH_IMAGE_TASK], "FAILED")
        self.assertEqual(statuses[DRAW_IMAGE_TASK], "FAILED")
        self.assertEqual(statuses[COMPLETE_SURFACE_TASK], "SUCCEEDED")
        self.assertEqual(compiled["components"][0]["props"], {})
        self.assertEqual(compiled["components"][0]["draft_placeholder"], _card("cat")["description"])
        self.assertNotIn("media_id", complete["surface"]["blocks"][0]["props"]["front"])
        self.assertEqual(model.calls, [])
        self.assertEqual(len(complete_results), 1)
        self.assertEqual(len(complete_results[0].warnings), 1)
        self.assertIn("giữ placeholder", complete_results[0].warnings[0])
        self.assertTrue(any(e.event == "TASK_SUCCEEDED" and e.task_type == COMPLETE_SURFACE_TASK for e in events))

    def test_manifest_translation_runs_runtime_to_assembly_with_output_contract(self):
        async def scenario():
            temporary, manifest = _load_manifest(_manifest_dict(auth=False))
            self.addCleanup(temporary.cleanup)
            manifests = AsyncTaskManifestRegistry((manifest,))
            plan = _plan(
                [{
                    "widget_id": "text",
                    "slot_role": "translation_result",
                    "description": "Bản dịch đầy đủ do translate_task trả về.",
                    "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 4},
                    "props": {"role": "body"},
                }],
                tasks=[{
                    "evidence_ref": "#E1",
                    "task_type": "translate_task",
                    "target_slots": ["translation_result"],
                    "instruction": "Dịch lời chào sang tiếng Việt.",
                }],
            )
            store = ArtifactStore()
            registry = build_default_executor_registry()
            registry.declare("translate_task")
            remote_jobs = InMemoryRemoteJobRegistry()

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/tasks":
                    return httpx.Response(202, json={"job_id": "translate_1"})
                return httpx.Response(200, json={
                    "status": "completed",
                    "payload": {"translation": "Xin chào. Chúc bạn một buổi sáng tốt lành."},
                })

            def transform(payload, result, _turn):
                result["surface"]["blocks"][0]["props"]["content"] = (
                    payload["task_outputs"][0]["output_data"]["translation"]
                )
                return result

            model = _Completions(transform)
            media = _MediaReferences()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                registry.bind(
                    "translate_task",
                    GenericHttpAsyncAgentExecutor(
                        manifest,
                        remote_jobs,
                        client,
                        _limits(),
                        sleep=_no_sleep,
                    ),
                )
                registry.bind(
                    COMPLETE_SURFACE_TASK,
                    CompleteSurfaceTaskExecutor(
                        self._agent(model),
                        FinalSurfaceCompiler(
                            self.registries.widget_registry,
                            media_reference_store=media,
                        ),
                        store,
                        media,
                        async_task_manifest_registry=manifests,
                    ),
                )
                manager = TaskManager(registry, store)
                submitted = await manager.submit_surface_plan(
                    plan,
                    draft=SurfaceDraft(blocks=plan.blocks),
                    surface_id="panel_translation",
                )
                snapshot = await manager.wait_for_run(submitted.run_id)

            complete_artifact = next(
                artifact
                for artifact in store.artifacts_for_run(snapshot.run_id)
                if artifact.artifact_type == "complete_surface"
            )
            payload = json.loads(model.calls[0]["messages"][1]["content"])
            return snapshot, complete_artifact, payload

        snapshot, complete_artifact, payload = asyncio.run(scenario())
        self.assertTrue(all(record.status == "SUCCEEDED" for record in snapshot.records))
        self.assertEqual(payload["task_outputs"][0]["task"]["task_type"], "translate_task")
        self.assertEqual(
            payload["task_outputs"][0]["output_contract"],
            _manifest_dict(auth=False)["output"]["schema"],
        )
        self.assertEqual(
            payload["task_outputs"][0]["output_data"],
            {"translation": "Xin chào. Chúc bạn một buổi sáng tốt lành."},
        )
        self.assertEqual(
            complete_artifact.data["compiled_surface"]["components"][0]["props"]["content"],
            "Xin chào. Chúc bạn một buổi sáng tốt lành.",
        )


if __name__ == "__main__":
    unittest.main()
