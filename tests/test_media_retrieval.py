"""CP6 tests for the independent media-retrieval task."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from final_framework.agent_tasks import AgentTaskRequestFactory
from final_framework.executors.media import SearchImageTaskExecutor, bind_search_image_task
from final_framework.executors.registry import SEARCH_IMAGE_TASK, build_default_executor_registry
from final_framework.media.retrieval import (
    MEDIA_RESULT_JSON_SCHEMA,
    MediaRetrievalAgent,
    MediaRetrievalResult,
    validate_media_result,
)
from final_framework.knowledge import (
    KNOWLEDGE_TOOL_REGISTRY,
    SEARCH_LOCAL_MEDIA,
    KnowledgeAccessError,
    KnowledgeGateway,
    KnowledgeGrantRegistry,
    KnowledgeRegistry,
)
from final_framework.settings import Settings
from final_framework.tests.plan_fixtures import make_surface_plan
from final_framework.task_manager import TaskExecutionContext, TaskRecord


def _settings() -> Settings:
    return Settings(
        gemini_live_api_key="live",
        gemini_live_model="live-model",
        gemini_live_voice="voice",
        live_turn_timeout_seconds=45,
        live_idle_timeout_seconds=900,
        live_reconnect_grace_seconds=30,
        presentation_animation_delay_ms=0,
        gemini_media_key="media-key",
        gemini_media_model="gemini-media-model",
    )


class _Registry(KnowledgeRegistry):
    def __init__(self) -> None:
        super().__init__(Path("."))
        self.requests: list[dict] = []

    def package_catalog(self) -> list[dict]:
        return [
            {
                "package_id": "history_docs",
                "description": "Historical passages.",
                "languages": ["vi"],
                "content_types": ["text"],
            },
            {
                "package_id": "history_media",
                "description": "Historical images.",
                "languages": ["vi"],
                "content_types": ["image"],
            },
        ]

    async def search_media_parallel(self, request: dict) -> dict:
        self.requests.append(request)
        return {
            "candidates": [{
                "candidate_id": "candidate_media_123",
                "package_id": "history_media",
                "similarity_score": 0.91,
                "provenance": {"source_uri": "https://local.test/record"},
                "payload": {
                    "media_id": "media_123",
                    "mime_type": "image/jpeg",
                    "object_key": "media_123.rev.jpg",
                    "source_uri": "https://local.test/record",
                    "caption": "Ảnh local.",
                },
            }],
            "package_errors": [],
        }


class _Completions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _Client:
    def __init__(self, responses: list[object]) -> None:
        self.completions = _Completions(responses)
        self.aio = SimpleNamespace(models=self.completions)


def _context() -> TaskExecutionContext:
    plan = make_surface_plan((("#E2", "search_image_task", "Dữ liệu kiểm thử."),))
    return TaskExecutionContext(
        run_id="run_1",
        surface_plan=plan,
        task=TaskRecord(
            "run_1",
            "task_media",
            SEARCH_IMAGE_TASK,
            "QUEUED",
            evidence_ref="#E2",
            instruction="Tìm ảnh nhân vật.",
        ),
    )


def _response(*, calls=(), text: str | None = None, grounding_uri: str | None = None):
    metadata = None
    if grounding_uri:
        metadata = SimpleNamespace(
            grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri=grounding_uri, title="Ảnh Google"))]
        )
    candidate = SimpleNamespace(content=None, grounding_metadata=metadata)
    return SimpleNamespace(function_calls=list(calls), text=text, candidates=[candidate])


class MediaRetrievalTests(unittest.TestCase):
    def _agent(self, responses: list[object]):
        registry = _Registry()
        client = _Client(responses)
        agent = MediaRetrievalAgent(_settings(), client_factory=lambda **_: client)
        return agent, registry, client

    @staticmethod
    async def _retrieve(agent, registry):
        gateway = KnowledgeGateway(registry, KnowledgeGrantRegistry())
        factory = AgentTaskRequestFactory(KNOWLEDGE_TOOL_REGISTRY)
        request = factory.build(
            _context(),
            task_input={"query": "Tìm ảnh nhân vật."},
            knowledge_catalog=tuple(registry.package_catalog()),
            allowed_tools=(SEARCH_LOCAL_MEDIA,),
        )
        access = gateway.issue_access_context(
            run_id="run_1",
            task_id="task_media",
            allowed_tools=(SEARCH_LOCAL_MEDIA,),
        )
        try:
            return await agent.retrieve(request, gateway, access)
        finally:
            gateway.grant_registry.revoke(access)

    def test_local_and_grounded_google_image_become_internal_media_ids(self) -> None:
        call = SimpleNamespace(
            name="search_local_media",
            id="call_1",
            args={"query": "ảnh nhân vật", "package_ids": ["history_media"]},
        )
        image_url = "https://images.example.test/hero.jpg"
        agent, registry, client = self._agent([
            _response(calls=(call,)),
            _response(text=json.dumps({
                "items": [{"media_id": "media_123"}, {"url": image_url}]
            }), grounding_uri=image_url),
        ])

        result = asyncio.run(self._retrieve(agent, registry))

        self.assertEqual(registry.requests, [{"query": "ảnh nhân vật", "package_ids": ["history_media"]}])
        self.assertEqual(result.items[0], {"media_id": "media_123"})
        self.assertTrue(result.items[1]["media_id"].startswith("remote_"))
        self.assertEqual(result.references[1]["source_uri"], image_url)
        self.assertTrue(result.google_grounding_used)
        first_config = client.completions.calls[0]["config"]
        self.assertEqual(len(first_config.tools), 2)
        self.assertEqual(first_config.response_mime_type, "application/json")
        self.assertEqual(first_config.response_json_schema, MEDIA_RESULT_JSON_SCHEMA)
        self.assertEqual(len(MEDIA_RESULT_JSON_SCHEMA["properties"]["items"]["items"]["oneOf"]), 2)
        self.assertIn(
            "BẮT BUỘC lượt truy xuất đầu tiên phải chọn kho phù hợp",
            first_config.system_instruction,
        )
        self.assertIn(
            "Không dùng Google Search/image grounding trước khi đã nhận FunctionResponse",
            first_config.system_instruction,
        )
        request_payload = json.loads(client.completions.calls[0]["contents"][0].parts[0].text)
        self.assertEqual(request_payload["framework_context"]["tool_grants"], ["search_local_media"])
        self.assertNotIn("target_slots", json.dumps(request_payload))

    def test_invalid_json_is_returned_to_same_media_agent_once(self) -> None:
        agent, registry, client = self._agent([
            _response(text='{"items":[{"caption":"not allowed"}]}'),
            _response(text='{"items":[]}'),
        ])

        result = asyncio.run(self._retrieve(agent, registry))

        self.assertEqual(result.items, ())
        self.assertEqual(len(client.completions.calls), 2)
        self.assertIn("items[0] chỉ được có media_id hoặc url.", client.completions.calls[1]["contents"][-1].parts[0].text)

    def test_executor_keeps_items_and_provenance_under_evidence_ref(self) -> None:
        received = []
        test_case = self

        class _Agent:
            async def retrieve(self, request, gateway, access_context):
                received.append((request, gateway, access_context))
                with test_case.assertRaisesRegex(KnowledgeAccessError, "not granted"):
                    await gateway.search_text(
                        {"query": "tư liệu", "package_ids": ["history_docs"]},
                        access_context,
                    )
                return MediaRetrievalResult(
                    items=({"media_id": "media_123"},),
                    references=({
                        "media_id": "media_123",
                        "package_id": "history_media",
                        "object_key": "media_123.rev.jpg",
                        "mime_type": "image/jpeg",
                        "source_uri": "https://local.test/record",
                        "caption": "Ảnh local.",
                        "local_file_missing": False,
                    },),
                    local_search_count=1,
                    google_grounding_used=False,
                    duration_ms=12,
                )

        registry = _Registry()
        gateway = KnowledgeGateway(registry, KnowledgeGrantRegistry())
        factory = AgentTaskRequestFactory(KNOWLEDGE_TOOL_REGISTRY)
        task_result = asyncio.run(
            SearchImageTaskExecutor(_Agent(), factory, gateway)(_context())
        )

        artifact = task_result.artifact
        self.assertEqual(task_result.evidence_ref, "#E2")
        self.assertEqual(artifact.artifact_type, "media")
        self.assertFalse(hasattr(artifact, "target_slots"))
        self.assertEqual(artifact.data["items"], [{"media_id": "media_123"}])
        self.assertEqual(artifact.data["references"][0]["caption"], "Ảnh local.")
        self.assertEqual(task_result.metrics["usable_resource_count"], 1)
        self.assertEqual(task_result.metrics["usable_resource_kinds"], ["image"])
        self.assertEqual(
            received[0][0].framework_context.tool_grants,
            (SEARCH_LOCAL_MEDIA,),
        )
        self.assertEqual(gateway.grant_registry.active_grant_count(), 0)

        executor_registry = build_default_executor_registry()
        bound = bind_search_image_task(
            executor_registry,
            _Agent(),
            factory,
            gateway,
        )
        self.assertIs(executor_registry.resolve(SEARCH_IMAGE_TASK), bound)

    def test_validator_rejects_unknown_google_image_url(self) -> None:
        errors = validate_media_result(
            json.dumps({"items": [{"url": "https://wrong.example/hero.jpg"}]}),
            local_evidence=(),
            grounded_media_urls=(),
        )
        self.assertEqual(errors, ["items[0].url không tồn tại trong Google image grounding evidence."])


if __name__ == "__main__":
    unittest.main()
