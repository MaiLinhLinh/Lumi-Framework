"""CP8 tests for manifest-only activation, merged catalog and session isolation."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from final_framework.knowledge import (
    FilesystemMediaStore,
    KnowledgeAccessError,
    KnowledgeGateway,
    KnowledgeGrantRegistry,
    KnowledgeRegistry,
    MediaReferenceStore,
)
from final_framework.live.task_runtime import LiveTaskRuntime
from final_framework.registry_composition import (
    FrameworkRegistryError,
    load_framework_registries,
)
from final_framework.settings import Settings
from final_framework.task_manager_agent import TaskManagerAgent, TaskManagerAgentRequest


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
        cerebras_api_key="manager-and-assembly-key",
        cerebras_manager_model="manager-model",
        cerebras_assembly_model="assembly-model",
        enable_search_image_task=False,
    )


def _manifest(task_id: str, *, resource: bool) -> dict:
    output = {
        "path": "$.payload",
        "schema": {
            "type": "object",
            "required": ["passage"],
            "properties": {"passage": {"type": "string"}},
            "additionalProperties": False,
        },
    }
    if resource:
        output = {
            "path": "$.payload",
            "schema": {
                "type": "object",
                "required": ["images"],
                "properties": {
                    "images": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["title", "url", "mime_type"],
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string", "format": "uri"},
                                "mime_type": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    }
                },
                "additionalProperties": False,
            },
            "resources": [{
                "items_path": "$.images[*]",
                "uri_path": "$.url",
                "mime_type_path": "$.mime_type",
                "caption_path": "$.title",
            }],
        }
    route = "image" if resource else "text"
    return {
        "id": task_id,
        "description": "Tìm ảnh từ Agent Service." if resource else "Lấy nội dung từ Agent Service.",
        "knowledge": {"allowed_tools": []},
        "input": {
            "payload": {"query": "{{task.instruction}}"},
            "schema": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
        },
        "executor": {
            "type": "http_async_agent",
            "submit": {
                "url": f"https://agent.example.com/{route}/tasks",
                "method": "POST",
                "job_id_path": "$.job_id",
            },
            "status": {
                "url": "https://agent.example.com/jobs/{{job_id}}",
                "method": "GET",
                "state_path": "$.status",
                "states": {
                    "queued": ["queued"],
                    "running": ["running"],
                    "succeeded": ["completed"],
                    "failed": ["failed"],
                    "cancelled": ["cancelled"],
                },
                "error_path": "$.error",
            },
            "poll_interval_seconds": 0.001,
            "timeout_seconds": 5,
        },
        "output": output,
    }


def _copy_framework_root(destination: Path) -> Path:
    shutil.copytree(ROOT / "resources", destination / "resources")
    (destination / "extensions").mkdir(parents=True)
    shutil.copytree(ROOT / "extensions" / "widgets", destination / "extensions" / "widgets")
    shutil.copytree(ROOT / "extensions" / "effects", destination / "extensions" / "effects")
    (destination / "extensions" / "tasks").mkdir()
    return destination


def _write_manifest(root: Path, task_id: str, manifest: dict) -> None:
    package = root / "extensions" / "tasks" / task_id
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class _Completions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


class _OpenAIClient:
    def __init__(self, completions: _Completions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _completion(*, content=None, tool_calls=()):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content,
        tool_calls=list(tool_calls),
    ))])


def _tool_call(call_id: str, widget_id: str):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="describe_widget",
            arguments=json.dumps({"widget_id": widget_id}),
        ),
    )


def _plan_json() -> dict:
    return {
        "action": "create_surface_plan",
        "template_description": "Một vùng nội dung và một ảnh minh họa.",
        "surface": {"blocks": [
            {
                "widget_id": "text",
                "slot_role": "remote_text",
                "description": "Nội dung do remote_text_task trả về.",
                "grid": {"col": 1, "row": 1, "col_span": 8, "row_span": 5},
                "props": {"role": "body"},
            },
            {
                "widget_id": "image",
                "slot_role": "remote_image",
                "description": "Ảnh do remote_image_task trả về.",
                "grid": {"col": 9, "row": 1, "col_span": 8, "row_span": 5},
                "props": {},
            },
        ]},
        "tasks": [
            {
                "evidence_ref": "#E1",
                "task_type": "remote_text_task",
                "target_slots": ["remote_text"],
                "instruction": "Lấy đoạn nội dung kiểm thử.",
            },
            {
                "evidence_ref": "#E2",
                "task_type": "remote_image_task",
                "target_slots": ["remote_image"],
                "instruction": "Tìm ảnh một con mèo.",
            },
        ],
    }


class _AssemblyCompletions:
    async def create(self, **kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        result = copy.deepcopy(payload["surface_draft"])
        output = payload["task_outputs"][0]["output_data"]
        block = result["surface"]["blocks"][0]
        if "passage" in output:
            block["props"]["content"] = output["passage"]
        else:
            block["props"]["media_id"] = output["images"][0]["resource_id"]
        return _completion(content=json.dumps(result, ensure_ascii=False))


class _LegacyMediaStore:
    media_store = object()

    def register_worker_result(self, **_kwargs):
        return ()

    def get(self, **_kwargs):
        raise KeyError("legacy media not present")


class DynamicAsyncTaskActivationTests(unittest.TestCase):
    def test_manifest_becomes_catalog_entry_after_reload_and_conflicts_fail_startup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = _copy_framework_root(Path(temp_dir) / "framework")
            before = load_framework_registries(root)
            self.assertNotIn(
                "remote_text_task",
                {item["task_type"] for item in before.task_catalog(_settings())},
            )

            _write_manifest(root, "remote_text_task", _manifest("remote_text_task", resource=False))
            after_restart = load_framework_registries(root)
            catalog_ids = [item["task_type"] for item in after_restart.task_catalog(_settings())]
            self.assertEqual(catalog_ids.count("remote_text_task"), 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = _copy_framework_root(Path(temp_dir) / "framework")
            _write_manifest(
                root,
                "search_information_task",
                _manifest("search_information_task", resource=False),
            )
            with self.assertRaisesRegex(FrameworkRegistryError, "conflict"):
                load_framework_registries(root)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = _copy_framework_root(Path(temp_dir) / "framework")
            invalid = _manifest("invalid_remote_task", resource=False)
            invalid.pop("output")
            _write_manifest(root, "invalid_remote_task", invalid)
            with self.assertRaises(FrameworkRegistryError):
                load_framework_registries(root)

    def test_agent_to_runtime_to_assembly_for_text_and_resource_is_session_scoped(self):
        async def scenario():
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = _copy_framework_root(Path(temporary.name) / "framework")
            _write_manifest(root, "remote_text_task", _manifest("remote_text_task", resource=False))
            _write_manifest(root, "remote_image_task", _manifest("remote_image_task", resource=True))
            knowledge_registry = KnowledgeRegistry(root / "knowledge_packages")
            registries = load_framework_registries(
                root,
                knowledge_registry=knowledge_registry,
            )
            grants = KnowledgeGrantRegistry()
            gateway = KnowledgeGateway(knowledge_registry, grants)
            settings = _settings()
            self.assertIs(registries.knowledge_registry, knowledge_registry)

            planner_completions = _Completions([
                _completion(tool_calls=(
                    _tool_call("describe_text", "text"),
                    _tool_call("describe_image", "image"),
                )),
                _completion(content=json.dumps(_plan_json(), ensure_ascii=False)),
            ])
            planner = TaskManagerAgent.from_settings(
                settings=settings,
                registries=registries,
                client_factory=lambda **_: _OpenAIClient(planner_completions),
            )
            self.assertEqual(
                {item["task_type"] for item in planner.task_catalog}
                & {"remote_text_task", "remote_image_task"},
                {"remote_text_task", "remote_image_task"},
            )
            plan = await planner.plan(TaskManagerAgentRequest(request="Tạo nội dung có chữ và ảnh."))

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/text/tasks":
                    return httpx.Response(202, json={"job_id": "text-job"})
                if request.url.path == "/image/tasks":
                    return httpx.Response(202, json={"job_id": "image-job"})
                if request.url.path == "/jobs/text-job":
                    return httpx.Response(200, json={
                        "status": "completed",
                        "payload": {"passage": "Nội dung từ Async Agent Service."},
                    })
                if request.url.path == "/jobs/image-job":
                    return httpx.Response(200, json={
                        "status": "completed",
                        "payload": {"images": [{
                            "title": "Con mèo",
                            "url": "https://cdn.example.com/cat.jpg",
                            "mime_type": "image/jpeg",
                        }]},
                    })
                raise AssertionError(f"unexpected URL {request.url}")

            complete_a: list[dict] = []
            transport = httpx.MockTransport(handler)
            async with (
                httpx.AsyncClient(transport=transport) as client_a,
                httpx.AsyncClient(transport=transport) as client_b,
            ):
                runtime_a = LiveTaskRuntime(
                    session_id="session_a",
                    settings=settings,
                    registries=registries,
                    media_reference_store=_LegacyMediaStore(),
                    on_task_event=lambda _event: None,
                    on_draft=lambda *_: None,
                    on_complete=lambda payload, _effects: complete_a.append(payload),
                    knowledge_gateway=gateway,
                    async_http_client=client_a,
                    assembly_client_factory=lambda **_: _OpenAIClient(_AssemblyCompletions()),
                )
                runtime_b = LiveTaskRuntime(
                    session_id="session_b",
                    settings=settings,
                    registries=registries,
                    media_reference_store=_LegacyMediaStore(),
                    on_task_event=lambda _event: None,
                    on_draft=lambda *_: None,
                    on_complete=lambda *_: None,
                    knowledge_gateway=gateway,
                    async_http_client=client_b,
                    assembly_client_factory=lambda **_: _OpenAIClient(_AssemblyCompletions()),
                )
                submitted = await runtime_a.submit_surface_plan(plan, active_surface=None)
                snapshot = await runtime_a.manager.wait_for_run(submitted.run_id)

                text_record = next(
                    record for record in snapshot.records if record.task_type == "remote_text_task"
                )
                image_record = next(
                    record for record in snapshot.records if record.task_type == "remote_image_task"
                )
                remote_a = await runtime_a.remote_job_registry.get(
                    snapshot.run_id, text_record.task_id
                )
                remote_b = await runtime_b.remote_job_registry.get(
                    snapshot.run_id, text_record.task_id
                )
                compiled = complete_a[0]["compiled"].to_dict()
                resource_id = compiled["components"][1]["props"]["media_id"]

                assertions = {
                    "statuses": {record.status for record in snapshot.records},
                    "text": compiled["components"][0]["props"]["content"],
                    "resource_id": resource_id,
                    "resource_a": runtime_a.resource_store.contains("session_a", resource_id),
                    "resource_b": runtime_b.resource_store.contains("session_b", resource_id),
                    "remote_a": remote_a,
                    "remote_b": remote_b,
                    "image_task_id": image_record.task_id,
                }
                close_grant = gateway.issue_access_context(
                    run_id=snapshot.run_id,
                    task_id="task_still_open",
                    allowed_tools=(),
                )
                await runtime_a.close()
                try:
                    grants.authenticate(close_grant.transport_token)
                except KnowledgeAccessError:
                    assertions["close_revoked_grant"] = True
                else:
                    assertions["close_revoked_grant"] = False
                await runtime_b.close()
            return assertions

        result = asyncio.run(scenario())
        self.assertEqual(result["statuses"], {"SUCCEEDED"})
        self.assertEqual(result["text"], "Nội dung từ Async Agent Service.")
        self.assertTrue(result["resource_id"].startswith("resource_"))
        self.assertTrue(result["resource_a"])
        self.assertFalse(result["resource_b"])
        self.assertIsNotNone(result["remote_a"])
        self.assertIsNone(result["remote_b"])
        self.assertTrue(result["close_revoked_grant"])

    def test_single_registry_binds_all_builtin_and_manifest_tasks(self):
        async def scenario():
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = _copy_framework_root(Path(temporary.name) / "framework")
            _write_manifest(
                root,
                "remote_text_task",
                _manifest("remote_text_task", resource=False),
            )
            knowledge_registry = KnowledgeRegistry(root / "knowledge_packages")
            registries = load_framework_registries(
                root,
                knowledge_registry=knowledge_registry,
            )
            gateway = KnowledgeGateway(
                knowledge_registry,
                KnowledgeGrantRegistry(),
            )
            base = _settings()
            settings = Settings(
                **{
                    **base.__dict__,
                    "enable_search_image_task": True,
                    "gemini_information_api_key": "information-key",
                    "gemini_information_model": "information-model",
                    "gemini_media_key": "media-key",
                    "gemini_media_model": "media-model",
                    "gemini_draw_key": "draw-key",
                    "gemini_draw_model": "draw-model",
                }
            )
            media_store = FilesystemMediaStore(root / "knowledge_media")
            references = MediaReferenceStore(media_store)
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(500, json={})
                )
            ) as client:
                runtime = LiveTaskRuntime(
                    session_id="binding_session",
                    settings=settings,
                    registries=registries,
                    media_reference_store=references,
                    on_task_event=lambda _event: None,
                    on_draft=lambda *_: None,
                    on_complete=lambda *_: None,
                    knowledge_gateway=gateway,
                    async_http_client=client,
                    assembly_client_factory=lambda **_: _OpenAIClient(
                        _AssemblyCompletions()
                    ),
                )
                executor_registry = runtime.manager._executor_registry
                task_types = (
                    "search_information_task",
                    "search_image_task",
                    "draw_image_task",
                    "complete_surface_task",
                    "remote_text_task",
                )
                result = {
                    task_type: executor_registry.is_bound(task_type)
                    for task_type in task_types
                }
                await runtime.close()
                return result

        self.assertTrue(all(asyncio.run(scenario()).values()))


if __name__ == "__main__":
    unittest.main()
