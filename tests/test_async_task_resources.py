"""CP6 resource normalization, lifecycle and Surface integration tests."""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from final_framework.artifacts import ArtifactStore, ArtifactStoreError
from final_framework.async_tasks import (
    AsyncTaskExecutionLimits,
    AsyncTaskManifestRegistry,
    GenericHttpAsyncAgentExecutor,
    InMemoryRemoteJobRegistry,
    InMemoryResourceStore,
    ResourceBatchLifecycle,
    ResourceMediaReferenceAdapter,
    ResourceReference,
    ResourceStoreError,
)
from final_framework.executors.complete_surface import CompleteSurfaceTaskExecutor
from final_framework.executors.registry import (
    COMPLETE_SURFACE_TASK,
    build_default_executor_registry,
)
from final_framework.registry_composition import load_framework_registries
from final_framework.surface.assembly import SurfaceAssemblyAgent
from final_framework.surface.assembly import SurfaceAssemblyError
from final_framework.surface.contracts import SurfaceDraft
from final_framework.surface.final_compiler import FinalSurfaceCompiler
from final_framework.task_manager import (
    Artifact,
    SurfaceRetrievalPlan,
    TaskExecutionContext,
    TaskManager,
    TaskRecord,
    TaskResult,
)
from final_framework.tests.test_async_task_executor import (
    _load_manifest,
    _manifest_dict,
    _no_sleep,
)
from final_framework.tests.test_complete_surface_task import _settings


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_MIME_TYPES = ("image/jpeg", "image/png")


def _resource_manifest() -> dict:
    manifest = copy.deepcopy(_manifest_dict(auth=False))
    manifest["id"] = "remote_image_task"
    manifest["description"] = "Tìm ảnh bằng Async Agent Service."
    manifest["input"]["schema"]["properties"]["task_type"] = {
        "const": "remote_image_task"
    }
    manifest["output"] = {
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
    return manifest


def _plan() -> SurfaceRetrievalPlan:
    return SurfaceRetrievalPlan.from_dict({
        "action": "create_surface_plan",
        "template_description": "Khung ảnh từ Async Agent Task.",
        "surface": {
            "blocks": [{
                "widget_id": "image",
                "slot_role": "hero_image",
                "description": "Ảnh chính do remote_image_task trả về.",
                "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 8},
                "props": {},
            }]
        },
        "tasks": [{
            "evidence_ref": "#E1",
            "task_type": "remote_image_task",
            "target_slots": ["hero_image"],
            "instruction": "Tìm ảnh một con mèo rõ chủ thể.",
        }],
    })


def _reference(
    resource_id: str,
    *,
    uri: str = "https://cdn.example.com/cat.jpg",
    mime_type: str = "image/jpeg",
    owner: str = "task_1",
    attempt: int = 0,
) -> ResourceReference:
    return ResourceReference(
        resource_id=resource_id,
        owner_task_id=owner,
        attempt=attempt,
        manifest_id="remote_image_task",
        kind="image",
        mime_type=mime_type,
        source_uri=uri,
        caption="Con mèo",
    )


def _limits() -> AsyncTaskExecutionLimits:
    return AsyncTaskExecutionLimits(2048, 16 * 1024 * 1024, 64, 100_000, 1)


class _AssemblyCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        payload = json.loads(kwargs["messages"][1]["content"])
        result = copy.deepcopy(payload["surface_draft"])
        resource_id = payload["task_outputs"][0]["output_data"]["images"][0][
            "resource_id"
        ]
        result["surface"]["blocks"][0]["props"]["media_id"] = resource_id
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))]
        )


class _Client:
    def __init__(self, completions: _AssemblyCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


class _FailingAsyncArtifactStore(ArtifactStore):
    def write(self, artifact: Artifact) -> Artifact:
        if artifact.artifact_type == "async_task_output":
            raise ArtifactStoreError("forced async artifact failure")
        return super().write(artifact)


class AsyncTaskResourceStoreTests(unittest.TestCase):
    def test_batch_is_atomic_scoped_and_discardable(self) -> None:
        store = InMemoryResourceStore(ALLOWED_MIME_TYPES)
        good = _reference("resource_good")
        bad = _reference("resource_bad", mime_type="application/x-executable")

        with self.assertRaises(ResourceStoreError):
            store.register_batch("live_a", (good, bad))
        self.assertFalse(store.contains("live_a", "resource_good"))

        store.register_batch("live_a", (good,))
        self.assertFalse(store.contains("live_a", "resource_good"))
        store.commit_batch("live_a", "task_1", 0)
        self.assertTrue(store.contains("live_a", "resource_good"))
        self.assertFalse(store.contains("live_b", "resource_good"))
        with self.assertRaises(ResourceStoreError):
            store.get("live_b", "resource_good")

        store.discard_batch("live_a", "task_1", 0)
        self.assertFalse(store.contains("live_a", "resource_good"))

    def test_rejects_non_https_and_private_or_internal_hosts(self) -> None:
        cases = (
            "http://cdn.example.com/cat.jpg",
            "https://localhost/cat.jpg",
            "https://127.0.0.1/cat.jpg",
            "https://10.0.0.1/cat.jpg",
            "https://service.internal/cat.jpg",
        )
        for index, uri in enumerate(cases):
            with self.subTest(uri=uri):
                store = InMemoryResourceStore(ALLOWED_MIME_TYPES)
                with self.assertRaises(ResourceStoreError):
                    store.register_batch(
                        "live_a",
                        (_reference(f"resource_{index}", uri=uri),),
                    )

    def test_task_result_lifecycle_commits_and_rolls_back(self) -> None:
        store = InMemoryResourceStore(ALLOWED_MIME_TYPES)
        store.register_batch("live_a", (_reference("resource_1"),))
        lifecycle = ResourceBatchLifecycle(
            store,
            session_id="live_a",
            owner_task_id="task_1",
            attempt=0,
        )
        lifecycle.commit()
        self.assertTrue(store.contains("live_a", "resource_1"))
        lifecycle.rollback()
        self.assertFalse(store.contains("live_a", "resource_1"))


class AsyncTaskResourcePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registries = load_framework_registries(ROOT)

    def test_executor_normalizes_resource_and_reports_generic_metrics(self) -> None:
        async def scenario():
            temporary, manifest = _load_manifest(_resource_manifest())
            self.addCleanup(temporary.cleanup)
            resources = InMemoryResourceStore(ALLOWED_MIME_TYPES)

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/tasks":
                    return httpx.Response(202, json={"job_id": "direct_job"})
                return httpx.Response(200, json={
                    "status": "completed",
                    "payload": {"images": [{
                        "title": "Cat",
                        "url": "https://cdn.example.com/direct.jpg",
                        "mime_type": "image/jpeg",
                    }]},
                })

            context = TaskExecutionContext(
                "run_direct",
                TaskRecord(
                    "run_direct",
                    "task_direct",
                    "remote_image_task",
                    "RUNNING",
                    evidence_ref="#E1",
                    instruction="Find a cat image.",
                ),
                _plan(),
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await GenericHttpAsyncAgentExecutor(
                    manifest,
                    InMemoryRemoteJobRegistry(),
                    client,
                    _limits(),
                    resource_store=resources,
                    resource_session_id="live_direct",
                    resource_id_factory=lambda: "resource_direct",
                    sleep=_no_sleep,
                )(context)
            return result, resources

        result, resources = asyncio.run(scenario())
        self.assertEqual(result.metrics, {
            "usable_resource_count": 1,
            "usable_resource_kinds": ["image"],
        })
        self.assertEqual(
            result.artifact.data["images"][0]["resource_id"],
            "resource_direct",
        )
        self.assertNotIn("url", result.artifact.data["images"][0])
        self.assertFalse(resources.contains("live_direct", "resource_direct"))
        result.lifecycle.commit()
        self.assertTrue(resources.contains("live_direct", "resource_direct"))
        result.lifecycle.rollback()
        self.assertFalse(resources.contains("live_direct", "resource_direct"))

    def test_resource_output_runs_to_compiled_surface_without_raw_url(self) -> None:
        async def scenario():
            temporary, manifest = _load_manifest(_resource_manifest())
            self.addCleanup(temporary.cleanup)
            manifests = AsyncTaskManifestRegistry((manifest,))
            resources = InMemoryResourceStore(ALLOWED_MIME_TYPES)
            remote_jobs = InMemoryRemoteJobRegistry()
            legacy = SimpleNamespace(
                register_worker_result=lambda **_: (),
                get=lambda **_: (_ for _ in ()).throw(AssertionError("legacy lookup")),
            )
            media_adapter = ResourceMediaReferenceAdapter(
                resources,
                resource_session_id="live_session_1",
                legacy_store=legacy,
            )
            store = ArtifactStore()
            registry = build_default_executor_registry()
            registry.declare("remote_image_task")
            completions = _AssemblyCompletions()
            assembly = SurfaceAssemblyAgent(
                _settings(),
                self.registries.widget_registry,
                client_factory=lambda **_: _Client(completions),
            )
            registry.bind(
                COMPLETE_SURFACE_TASK,
                CompleteSurfaceTaskExecutor(
                    assembly,
                    FinalSurfaceCompiler(
                        self.registries.widget_registry,
                        media_reference_store=media_adapter,
                    ),
                    store,
                    media_adapter,
                    async_task_manifest_registry=manifests,
                    resource_store=resources,
                    resource_session_id="live_session_1",
                ),
            )

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/tasks":
                    return httpx.Response(202, json={"job_id": "image_job_1"})
                return httpx.Response(200, json={
                    "status": "completed",
                    "payload": {
                        "images": [{
                            "title": "Con mèo",
                            "url": "https://cdn.example.com/cat.jpg",
                            "mime_type": "image/jpeg",
                        }]
                    },
                })

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                registry.bind(
                    "remote_image_task",
                    GenericHttpAsyncAgentExecutor(
                        manifest,
                        remote_jobs,
                        client,
                        _limits(),
                        resource_store=resources,
                        resource_session_id="live_session_1",
                        resource_id_factory=lambda: "resource_cat_1",
                        sleep=_no_sleep,
                    ),
                )
                counter = itertools.count(1)
                manager = TaskManager(
                    registry,
                    store,
                    id_factory=lambda prefix: f"{prefix}_{next(counter)}",
                )
                submitted = await manager.submit_surface_plan(
                    _plan(),
                    draft=SurfaceDraft(blocks=_plan().blocks),
                    surface_id="panel_resource",
                )
                snapshot = await manager.wait_for_run(submitted.run_id)
            return snapshot, store, resources, completions

        snapshot, store, resources, completions = asyncio.run(scenario())
        self.assertTrue(all(record.status == "SUCCEEDED" for record in snapshot.records))
        async_artifact = next(
            artifact
            for artifact in store.artifacts_for_run(snapshot.run_id)
            if artifact.artifact_type == "async_task_output"
        )
        image = async_artifact.data["images"][0]
        self.assertEqual(image["resource_id"], "resource_cat_1")
        self.assertNotIn("url", image)
        reference = resources.get("live_session_1", "resource_cat_1")
        self.assertEqual(reference.source_uri, "https://cdn.example.com/cat.jpg")

        payload = json.loads(completions.calls[0]["messages"][1]["content"])
        self.assertNotIn("https://cdn.example.com", json.dumps(payload))
        item_contract = payload["task_outputs"][0]["output_contract"]["properties"][
            "images"
        ]["items"]
        self.assertIn("resource_id", item_contract["properties"])
        self.assertNotIn("url", item_contract["properties"])

        complete = next(
            artifact
            for artifact in store.artifacts_for_run(snapshot.run_id)
            if artifact.artifact_type == "complete_surface"
        )
        props = complete.data["compiled_surface"]["components"][0]["props"]
        self.assertEqual(props["media_id"], "resource_cat_1")
        self.assertEqual(props["source"]["url"], "https://cdn.example.com/cat.jpg")

    def test_artifact_commit_failure_rolls_back_committed_resource_batch(self) -> None:
        async def scenario():
            temporary, manifest = _load_manifest(_resource_manifest())
            self.addCleanup(temporary.cleanup)
            resources = InMemoryResourceStore(ALLOWED_MIME_TYPES)
            store = _FailingAsyncArtifactStore()
            registry = build_default_executor_registry()
            registry.declare("remote_image_task")

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

            registry.bind(COMPLETE_SURFACE_TASK, complete)

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/tasks":
                    return httpx.Response(202, json={"job_id": "image_job_2"})
                return httpx.Response(200, json={
                    "status": "completed",
                    "payload": {"images": [{
                        "title": "Cat",
                        "url": "https://cdn.example.com/cat.jpg",
                        "mime_type": "image/jpeg",
                    }]},
                })

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                registry.bind(
                    "remote_image_task",
                    GenericHttpAsyncAgentExecutor(
                        manifest,
                        InMemoryRemoteJobRegistry(),
                        client,
                        _limits(),
                        resource_store=resources,
                        resource_session_id="live_session_2",
                        resource_id_factory=lambda: "resource_rollback",
                        sleep=_no_sleep,
                    ),
                )
                manager = TaskManager(registry, store)
                submitted = await manager.submit_surface_plan(
                    _plan(),
                    draft=SurfaceDraft(blocks=_plan().blocks),
                    surface_id="panel_rollback",
                )
                snapshot = await manager.wait_for_run(submitted.run_id)
            return snapshot, resources

        snapshot, resources = asyncio.run(scenario())
        task = next(
            record for record in snapshot.records if record.task_type == "remote_image_task"
        )
        self.assertEqual(task.status, "FAILED")
        self.assertFalse(resources.contains("live_session_2", "resource_rollback"))

    def test_complete_surface_rejects_resource_owned_by_another_task(self) -> None:
        temporary, manifest = _load_manifest(_resource_manifest())
        self.addCleanup(temporary.cleanup)
        resources = InMemoryResourceStore(ALLOWED_MIME_TYPES)
        resources.register_batch(
            "live_session_owner",
            (_reference("resource_foreign", owner="another_task"),),
        )
        resources.commit_batch("live_session_owner", "another_task", 0)
        store = ArtifactStore()
        plan = _plan()
        store.write(Artifact(
            artifact_id="draft_owner",
            artifact_type="surface_draft",
            run_id="run_owner",
            produced_by="surface_task",
            data={**SurfaceDraft(blocks=plan.blocks).to_dict(), "surface_id": "panel_owner"},
        ))
        store.write(Artifact(
            artifact_id="async_owner",
            artifact_type="async_task_output",
            run_id="run_owner",
            produced_by="actual_task",
            evidence_ref="#E1",
            data={
                "images": [{
                    "title": "Cat",
                    "mime_type": "image/jpeg",
                    "resource_id": "resource_foreign",
                }]
            },
        ))
        adapter = ResourceMediaReferenceAdapter(
            resources,
            resource_session_id="live_session_owner",
        )
        completions = _AssemblyCompletions()
        executor = CompleteSurfaceTaskExecutor(
            SurfaceAssemblyAgent(
                _settings(),
                self.registries.widget_registry,
                client_factory=lambda **_: _Client(completions),
            ),
            FinalSurfaceCompiler(
                self.registries.widget_registry,
                media_reference_store=adapter,
            ),
            store,
            adapter,
            async_task_manifest_registry=AsyncTaskManifestRegistry((manifest,)),
            resource_store=resources,
            resource_session_id="live_session_owner",
        )
        context = TaskExecutionContext(
            "run_owner",
            TaskRecord(
                "run_owner",
                "complete_owner",
                COMPLETE_SURFACE_TASK,
                "RUNNING",
            ),
            plan,
        )

        with self.assertRaisesRegex(SurfaceAssemblyError, "belongs to another task"):
            asyncio.run(executor(context))
        self.assertEqual(completions.calls, [])


if __name__ == "__main__":
    unittest.main()
