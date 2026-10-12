"""CP4 tests for the resource-free Generic HTTP Async Agent Executor."""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from final_framework.agent_tasks import AgentTaskRequestFactory, SEARCH_LOCAL_TEXT
from final_framework.async_tasks import (
    AsyncTaskExecutionLimits,
    AsyncTaskHttpError,
    AsyncTaskManifestLoader,
    AsyncTaskResponseError,
    AsyncTaskTimeoutError,
    GenericHttpAsyncAgentExecutor,
    InMemoryRemoteJobRegistry,
    RemoteAsyncTaskError,
    StaleRemoteJobAttemptError,
    render_input_template,
    render_job_url,
)
from final_framework.executors import ExecutorRegistry
from final_framework.knowledge import KNOWLEDGE_TOKEN_HEADER, KnowledgeGrantRegistry
from final_framework.settings import Settings
from final_framework.task_manager import TaskExecutionContext, TaskRecord
from final_framework.tests.plan_fixtures import make_surface_plan


async def _no_sleep(_: float) -> None:
    return None


KNOWLEDGE_CATALOG = ({
    "package_id": "wikipedia_vi",
    "description": "Kiến thức bách khoa tiếng Việt.",
    "languages": ["vi"],
    "content_types": ["text", "image"],
},)


class _TrackingGrantRegistry(KnowledgeGrantRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.issued: list[str] = []
        self.revoked: list[str] = []

    def issue(self, **kwargs):
        context = super().issue(**kwargs)
        self.issued.append(context.transport_token)
        return context

    def revoke(self, access_context):
        self.revoked.append(access_context.transport_token)
        return super().revoke(access_context)


def _manifest_dict(
    *,
    timeout: float = 1.0,
    auth: bool = True,
    allowed_tools: tuple[str, ...] = (),
) -> dict:
    executor = {
        "type": "http_async_agent",
        "submit": {
            "url": "https://agent.example/tasks",
            "method": "POST",
            "job_id_path": "$.job_id",
        },
        "status": {
            "url": "https://agent.example/jobs/{{job_id}}?view=status",
            "method": "GET",
            "state_path": "$.status",
            "states": {
                "queued": ["pending"],
                "running": ["processing"],
                "succeeded": ["completed"],
                "failed": ["error"],
                "cancelled": ["stopped"],
            },
            "error_path": "$.error",
        },
        "cancel": {
            "url": "https://agent.example/jobs/{{job_id}}/cancel",
            "method": "POST",
        },
        "poll_interval_seconds": 0.001,
        "timeout_seconds": timeout,
    }
    if auth:
        executor["auth"] = {"type": "bearer_env", "env": "TRANSLATE_AGENT_TOKEN"}
    return {
        "id": "translate_task",
        "description": "Dịch nội dung theo yêu cầu.",
        "knowledge": {"allowed_tools": list(allowed_tools)},
        "input": {
            "payload": {
                "query": "{{task.instruction}}",
                "task_id": "{{task.task_id}}",
                "task_type": "{{task.task_type}}",
                "evidence_ref": "{{task.evidence_ref}}",
                "run_id": "{{run.run_id}}",
            },
            "schema": {
                "type": "object",
                "required": ["query", "task_id", "task_type", "evidence_ref", "run_id"],
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "task_id": {"type": "string"},
                    "task_type": {"const": "translate_task"},
                    "evidence_ref": {"type": "string"},
                    "run_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "executor": executor,
        "output": {
            "path": "$.payload",
            "schema": {
                "type": "object",
                "required": ["translation"],
                "properties": {"translation": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
        },
    }


def _load_manifest(raw: dict):
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name) / "tasks"
    task_id = raw["id"]
    package = root / task_id
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(
        json.dumps(copy.deepcopy(raw), ensure_ascii=False),
        encoding="utf-8",
    )
    manifest = AsyncTaskManifestLoader(root).load().get(task_id)
    return temporary, manifest


def _context(*, attempt: int = 0, instruction: str = "Dịch xin chào"):
    task = TaskRecord(
        run_id="run_1",
        task_id="task_1",
        task_type="translate_task",
        status="running",
        attempt=attempt,
        evidence_ref="#E1",
        instruction=instruction,
    )
    return TaskExecutionContext("run_1", task, make_surface_plan())


def _limits(**changes) -> AsyncTaskExecutionLimits:
    values = {
        "max_job_id_chars": 2048,
        "max_response_bytes": 16 * 1024 * 1024,
        "max_json_depth": 64,
        "max_collection_items": 100_000,
        "cancel_timeout_seconds": 0.1,
    }
    values.update(changes)
    return AsyncTaskExecutionLimits(**values)


class AsyncTaskExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_running_success_returns_one_candidate_artifact(self) -> None:
        temporary, manifest = _load_manifest(_manifest_dict())
        self.addCleanup(temporary.cleanup)
        remote_jobs = InMemoryRemoteJobRegistry()
        grants = _TrackingGrantRegistry()
        statuses = iter(("pending", "processing", "completed"))
        status_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            if request.url.path == "/tasks":
                payload = json.loads(request.content)
                self.assertEqual(payload, {
                    "task": {
                        "run_id": "run_1",
                        "task_id": "task_1",
                        "task_type": "translate_task",
                        "evidence_ref": "#E1",
                        "instruction": "Dịch xin chào",
                    },
                    "input": {
                        "query": "Dịch xin chào",
                        "task_id": "task_1",
                        "task_type": "translate_task",
                        "evidence_ref": "#E1",
                        "run_id": "run_1",
                    },
                    "framework_context": {
                        "knowledge_catalog": list(KNOWLEDGE_CATALOG),
                        "tool_grants": [],
                    },
                })
                self.assertNotIn("target_slots", json.dumps(payload))
                self.assertNotIn(KNOWLEDGE_TOKEN_HEADER, request.headers)
                return httpx.Response(202, json={"job_id": "job /?&"})
            status_urls.append(str(request.url))
            status = next(statuses)
            body = {"status": status}
            if status == "completed":
                body["payload"] = {"translation": "Xin chào"}
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            executor = GenericHttpAsyncAgentExecutor(
                manifest,
                remote_jobs,
                client,
                _limits(),
                request_factory=AgentTaskRequestFactory(),
                knowledge_catalog=KNOWLEDGE_CATALOG,
                knowledge_grant_registry=grants,
                environment={"TRANSLATE_AGENT_TOKEN": "test-token"},
                sleep=_no_sleep,
            )
            executor_registry = ExecutorRegistry()
            executor_registry.declare("translate_task")
            executor_registry.bind("translate_task", executor)
            result = await executor_registry.resolve("translate_task")(_context())

        self.assertEqual(result.status, "succeeded")
        self.assertIsNotNone(result.artifact)
        assert result.artifact is not None
        self.assertEqual(result.artifact.artifact_type, "async_task_output")
        self.assertEqual(result.artifact.data, {"translation": "Xin chào"})
        self.assertEqual(result.metrics, {
            "usable_resource_count": 0,
            "usable_resource_kinds": [],
        })
        self.assertIsNone(result.lifecycle)
        self.assertTrue(all("job%20%2F%3F%26" in url for url in status_urls))
        record = await remote_jobs.get("run_1", "task_1")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.normalized_remote_state, "succeeded")
        self.assertEqual(record.last_remote_state, "completed")
        self.assertEqual(grants.issued, [])
        self.assertEqual(grants.active_grant_count(), 0)

    async def test_remote_failed_and_cancelled_are_terminal_without_artifact(self) -> None:
        for remote_state, normalized in (("error", "failed"), ("stopped", "cancelled")):
            with self.subTest(remote_state=remote_state):
                temporary, manifest = _load_manifest(_manifest_dict(
                    auth=False,
                    allowed_tools=(SEARCH_LOCAL_TEXT,),
                ))
                self.addCleanup(temporary.cleanup)
                remote_jobs = InMemoryRemoteJobRegistry()
                grants = _TrackingGrantRegistry()

                def handler(request: httpx.Request) -> httpx.Response:
                    if request.url.path == "/tasks":
                        return httpx.Response(202, json={"job_id": "job_1"})
                    return httpx.Response(
                        200,
                        json={"status": remote_state, "error": "remote detail"},
                    )

                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                ) as client:
                    executor = GenericHttpAsyncAgentExecutor(
                        manifest,
                        remote_jobs,
                        client,
                        _limits(),
                        knowledge_catalog=KNOWLEDGE_CATALOG,
                        knowledge_grant_registry=grants,
                        sleep=_no_sleep,
                    )
                    with self.assertRaisesRegex(RemoteAsyncTaskError, normalized):
                        await executor(_context())

                record = await remote_jobs.get("run_1", "task_1")
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record.normalized_remote_state, normalized)
                self.assertEqual(grants.revoked, grants.issued)
                self.assertEqual(grants.active_grant_count(), 0)

    async def test_submit_separates_knowledge_header_and_revokes_grant(self) -> None:
        temporary, manifest = _load_manifest(_manifest_dict(
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        ))
        self.addCleanup(temporary.cleanup)
        grants = _TrackingGrantRegistry()
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request.url.path == "/tasks":
                payload = json.loads(request.content)
                self.assertEqual(
                    payload["framework_context"]["tool_grants"],
                    [SEARCH_LOCAL_TEXT],
                )
                self.assertEqual(
                    payload["framework_context"]["knowledge_catalog"],
                    list(KNOWLEDGE_CATALOG),
                )
                self.assertEqual(request.headers["Authorization"], "Bearer service-token")
                self.assertEqual(
                    request.headers[KNOWLEDGE_TOKEN_HEADER],
                    grants.issued[0],
                )
                return httpx.Response(202, json={"job_id": "job_knowledge"})
            self.assertNotIn(KNOWLEDGE_TOKEN_HEADER, request.headers)
            return httpx.Response(200, json={
                "status": "completed",
                "payload": {"translation": "Xin chào"},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GenericHttpAsyncAgentExecutor(
                manifest,
                InMemoryRemoteJobRegistry(),
                client,
                _limits(),
                request_factory=AgentTaskRequestFactory(),
                knowledge_catalog=KNOWLEDGE_CATALOG,
                knowledge_grant_registry=grants,
                environment={"TRANSLATE_AGENT_TOKEN": "service-token"},
                sleep=_no_sleep,
            )(_context())

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(request_count, 2)
        self.assertEqual(grants.revoked, grants.issued)
        self.assertEqual(grants.active_grant_count(), 0)

    async def test_http_and_malformed_responses_fail_without_artifact(self) -> None:
        cases = (
            (lambda _request: httpx.Response(503, json={}), AsyncTaskHttpError),
            (lambda _request: httpx.Response(200, content=b"not-json"), AsyncTaskResponseError),
            (lambda _request: httpx.Response(200, json={"missing": "job"}), AsyncTaskResponseError),
        )
        for handler, error_type in cases:
            with self.subTest(error_type=error_type.__name__):
                temporary, manifest = _load_manifest(_manifest_dict(
                    auth=False,
                    allowed_tools=(SEARCH_LOCAL_TEXT,),
                ))
                self.addCleanup(temporary.cleanup)
                remote_jobs = InMemoryRemoteJobRegistry()
                grants = _TrackingGrantRegistry()
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                ) as client:
                    executor = GenericHttpAsyncAgentExecutor(
                        manifest,
                        remote_jobs,
                        client,
                        _limits(),
                        knowledge_catalog=KNOWLEDGE_CATALOG,
                        knowledge_grant_registry=grants,
                        sleep=_no_sleep,
                    )
                    with self.assertRaises(error_type):
                        await executor(_context())
                self.assertIsNone(await remote_jobs.get("run_1", "task_1"))
                self.assertEqual(grants.revoked, grants.issued)
                self.assertEqual(grants.active_grant_count(), 0)

    async def test_wrong_output_path_or_schema_creates_no_artifact(self) -> None:
        terminal_bodies = (
            {"status": "completed"},
            {"status": "completed", "payload": {"translation": 123}},
        )
        for terminal_body in terminal_bodies:
            with self.subTest(terminal_body=terminal_body):
                temporary, manifest = _load_manifest(_manifest_dict(auth=False))
                self.addCleanup(temporary.cleanup)
                remote_jobs = InMemoryRemoteJobRegistry()

                def handler(request: httpx.Request) -> httpx.Response:
                    if request.url.path == "/tasks":
                        return httpx.Response(202, json={"job_id": "job_1"})
                    return httpx.Response(200, json=terminal_body)

                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                ) as client:
                    executor = GenericHttpAsyncAgentExecutor(
                        manifest, remote_jobs, client, _limits(), sleep=_no_sleep
                    )
                    with self.assertRaises(AsyncTaskResponseError):
                        await executor(_context())
                record = await remote_jobs.get("run_1", "task_1")
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record.normalized_remote_state, "succeeded")

    async def test_timeout_requests_percent_encoded_best_effort_cancel(self) -> None:
        temporary, manifest = _load_manifest(_manifest_dict(
            timeout=0.02,
            auth=False,
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        ))
        self.addCleanup(temporary.cleanup)
        remote_jobs = InMemoryRemoteJobRegistry()
        grants = _TrackingGrantRegistry()
        cancel_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"job_id": "job /?&"})
            if request.url.path.endswith("/cancel"):
                self.assertNotIn(KNOWLEDGE_TOKEN_HEADER, request.headers)
                cancel_urls.append(str(request.url))
                return httpx.Response(204)
            self.assertNotIn(KNOWLEDGE_TOKEN_HEADER, request.headers)
            return httpx.Response(200, json={"status": "processing"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            executor = GenericHttpAsyncAgentExecutor(
                manifest,
                remote_jobs,
                client,
                _limits(),
                knowledge_catalog=KNOWLEDGE_CATALOG,
                knowledge_grant_registry=grants,
                sleep=asyncio.sleep,
            )
            with self.assertRaises(AsyncTaskTimeoutError):
                await executor(_context())

        self.assertEqual(len(cancel_urls), 1)
        self.assertIn("job%20%2F%3F%26/cancel", cancel_urls[0])
        record = await remote_jobs.get("run_1", "task_1")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertIsNotNone(record.cancel_requested_at)
        self.assertIsNone(record.cancel_error)
        self.assertFalse(record.terminal)
        self.assertEqual(grants.revoked, grants.issued)
        self.assertEqual(grants.active_grant_count(), 0)

    async def test_local_cancellation_calls_remote_cancel_and_re_raises(self) -> None:
        temporary, manifest = _load_manifest(_manifest_dict(
            auth=False,
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        ))
        self.addCleanup(temporary.cleanup)
        remote_jobs = InMemoryRemoteJobRegistry()
        grants = _TrackingGrantRegistry()
        status_started = asyncio.Event()
        never_finish = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"job_id": "job_1"})
            if request.url.path.endswith("/cancel"):
                self.assertNotIn(KNOWLEDGE_TOKEN_HEADER, request.headers)
                return httpx.Response(200, json={"status": "stopped"})
            self.assertNotIn(KNOWLEDGE_TOKEN_HEADER, request.headers)
            status_started.set()
            await never_finish.wait()
            raise AssertionError("unreachable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            executor = GenericHttpAsyncAgentExecutor(
                manifest,
                remote_jobs,
                client,
                _limits(),
                knowledge_catalog=KNOWLEDGE_CATALOG,
                knowledge_grant_registry=grants,
                sleep=_no_sleep,
            )
            pending = asyncio.create_task(executor(_context()))
            await status_started.wait()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending

        record = await remote_jobs.get("run_1", "task_1")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.normalized_remote_state, "cancelled")
        self.assertIsNotNone(record.cancel_requested_at)
        self.assertEqual(grants.revoked, grants.issued)
        self.assertEqual(grants.active_grant_count(), 0)

    async def test_retry_replaces_job_and_stale_poll_cannot_overwrite_it(self) -> None:
        temporary, manifest = _load_manifest(_manifest_dict(
            auth=False,
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        ))
        self.addCleanup(temporary.cleanup)
        remote_jobs = InMemoryRemoteJobRegistry()
        grants = _TrackingGrantRegistry()
        old_status_started = asyncio.Event()
        release_old_status = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                payload = json.loads(request.content)
                job_id = (
                    "old_job" if payload["input"]["query"] == "old" else "new_job"
                )
                return httpx.Response(202, json={"job_id": job_id})
            if request.url.path.endswith("/old_job"):
                old_status_started.set()
                await release_old_status.wait()
                return httpx.Response(
                    200,
                    json={"status": "completed", "payload": {"translation": "old"}},
                )
            return httpx.Response(
                200,
                json={"status": "completed", "payload": {"translation": "new"}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            executor = GenericHttpAsyncAgentExecutor(
                manifest,
                remote_jobs,
                client,
                _limits(),
                knowledge_catalog=KNOWLEDGE_CATALOG,
                knowledge_grant_registry=grants,
                sleep=_no_sleep,
            )
            old_attempt = asyncio.create_task(
                executor(_context(attempt=0, instruction="old"))
            )
            await old_status_started.wait()
            new_result = await executor(_context(attempt=1, instruction="new"))
            release_old_status.set()
            with self.assertRaises(StaleRemoteJobAttemptError):
                await old_attempt

        self.assertEqual(new_result.artifact.data, {"translation": "new"})
        current = await remote_jobs.get("run_1", "task_1")
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual((current.attempt, current.job_id), (1, "new_job"))
        self.assertEqual(current.normalized_remote_state, "succeeded")
        self.assertEqual(len(grants.issued), 2)
        self.assertCountEqual(grants.revoked, grants.issued)
        self.assertEqual(grants.active_grant_count(), 0)

    async def test_response_limits_and_settings_projection(self) -> None:
        settings = Settings(
            gemini_live_api_key="",
            gemini_live_model="model",
            gemini_live_voice="voice",
            live_turn_timeout_seconds=1,
            live_idle_timeout_seconds=1,
            live_reconnect_grace_seconds=1,
            presentation_animation_delay_ms=0,
            async_task_max_job_id_chars=10,
            async_task_max_response_bytes=20,
            async_task_max_json_depth=3,
            async_task_max_collection_items=2,
            async_task_cancel_timeout_seconds=4,
        )
        limits = AsyncTaskExecutionLimits.from_settings(settings)
        self.assertEqual(
            (
                limits.max_job_id_chars,
                limits.max_response_bytes,
                limits.max_json_depth,
                limits.max_collection_items,
                limits.cancel_timeout_seconds,
            ),
            (10, 20, 3, 2, 4.0),
        )
        with self.assertRaisesRegex(ValueError, "positive integer"):
            _limits(max_json_depth=0)

        temporary, manifest = _load_manifest(_manifest_dict(auth=False))
        self.addCleanup(temporary.cleanup)

        def oversized_response(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'{"job_id":"larger-than-limit"}')

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(oversized_response)
        ) as client:
            executor = GenericHttpAsyncAgentExecutor(
                manifest,
                InMemoryRemoteJobRegistry(),
                client,
                _limits(max_response_bytes=8),
                sleep=_no_sleep,
            )
            with self.assertRaisesRegex(
                AsyncTaskResponseError,
                "ASYNC_TASK_MAX_RESPONSE_BYTES",
            ):
                await executor(_context())

    def test_safe_renderers_do_not_interpolate_or_expose_raw_job_id(self) -> None:
        rendered = render_input_template(
            {"query": "{{task.instruction}}", "fixed": [1, True]},
            {"task.instruction": "hello"},
        )
        self.assertEqual(rendered, {"query": "hello", "fixed": [1, True]})
        self.assertEqual(
            render_job_url(
                "https://agent.example/jobs/{{job_id}}?id={{job_id}}",
                "a /?&",
                max_chars=10,
            ),
            "https://agent.example/jobs/a%20%2F%3F%26?id=a%20%2F%3F%26",
        )


if __name__ == "__main__":
    unittest.main()
