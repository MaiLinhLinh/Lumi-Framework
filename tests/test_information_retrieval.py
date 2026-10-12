"""CP5 tests for the independent information-retrieval task."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from final_framework.agent_tasks import AgentTaskRequestFactory
from final_framework.executors.information import (
    SearchInformationTaskExecutor,
    bind_search_information_task,
)
from final_framework.executors.registry import SEARCH_INFORMATION_TASK, build_default_executor_registry
from final_framework.knowledge.information_retrieval import (
    INFORMATION_RESULT_JSON_SCHEMA,
    InformationRetrievalAgent,
    validate_information_result,
)
from final_framework.knowledge import (
    KNOWLEDGE_TOOL_REGISTRY,
    SEARCH_LOCAL_TEXT,
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
        gemini_information_api_key="information-key",
        gemini_information_model="gemini-information-model",
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

    async def search_text_parallel(self, request: dict) -> dict:
        self.requests.append(request)
        return {
            "candidates": [
                {
                    "candidate_id": "passage_123",
                    "package_id": "history_docs",
                    "similarity_score": 0.91,
                    "provenance": {},
                    "payload": {"passage": "Passage local gốc."},
                },
                {
                    "candidate_id": "below_threshold",
                    "package_id": "history_docs",
                    "similarity_score": 0.4,
                    "provenance": {},
                    "payload": {"passage": "Không được đưa cho model."},
                },
            ],
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
    plan = make_surface_plan((("#E1", "search_information_task", "Dữ liệu kiểm thử."),))
    return TaskExecutionContext(
        run_id="run_1",
        surface_plan=plan,
        task=TaskRecord(
            "run_1",
            "task_knowledge",
            "search_information_task",
            "QUEUED",
            evidence_ref="#E1",
            instruction="Tìm mốc lịch sử.",
        ),
    )


def _response(*, calls=(), text: str | None = None, grounding_uri: str | None = None):
    metadata = None
    if grounding_uri:
        metadata = SimpleNamespace(
            grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri=grounding_uri, title="Nguồn Google"))]
        )
    candidate = SimpleNamespace(content=None, grounding_metadata=metadata)
    return SimpleNamespace(function_calls=list(calls), text=text, candidates=[candidate])


class InformationRetrievalTests(unittest.TestCase):
    def _agent(self, responses: list[object]):
        registry = _Registry()
        client = _Client(responses)
        agent = InformationRetrievalAgent(
            _settings(), client_factory=lambda **_: client
        )
        return agent, registry, client

    @staticmethod
    async def _retrieve(agent, registry):
        gateway = KnowledgeGateway(registry, KnowledgeGrantRegistry())
        factory = AgentTaskRequestFactory(KNOWLEDGE_TOOL_REGISTRY)
        request = factory.build(
            _context(),
            task_input={"query": "Tìm mốc lịch sử."},
            knowledge_catalog=tuple(registry.package_catalog()),
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        access = gateway.issue_access_context(
            run_id="run_1",
            task_id="task_knowledge",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        try:
            return await agent.retrieve(request, gateway, access)
        finally:
            gateway.grant_registry.revoke(access)

    def test_local_search_is_parallel_backend_and_google_is_present_from_first_request(self) -> None:
        call = SimpleNamespace(
            name="search_local_text",
            id="call_1",
            args={"query": "mốc lịch sử", "package_ids": ["history_docs"]},
        )
        agent, registry, client = self._agent([
            _response(calls=(call,)),
            _response(text=json.dumps({
                "items": [{
                    "candidate_id": "passage_123",
                    "payload": {"passage": "Mốc lịch sử đã được trích lọc."},
                }]
            })),
        ])

        result = asyncio.run(self._retrieve(agent, registry))

        self.assertEqual(registry.requests, [{"query": "mốc lịch sử", "package_ids": ["history_docs"]}])
        self.assertEqual(result.items[0]["candidate_id"], "passage_123")
        self.assertEqual(result.local_search_count, 1)
        self.assertFalse(result.google_grounding_used)
        first_config = client.completions.calls[0]["config"]
        self.assertEqual(len(first_config.tools), 2)
        self.assertEqual(first_config.response_mime_type, "application/json")
        self.assertEqual(first_config.response_json_schema, INFORMATION_RESULT_JSON_SCHEMA)
        item_schema = INFORMATION_RESULT_JSON_SCHEMA["properties"]["items"]["items"]
        self.assertEqual(item_schema["required"], ["payload"])
        self.assertIn("candidate_id", item_schema["properties"])
        self.assertIn(
            "BẮT BUỘC lượt truy xuất đầu tiên phải chọn kho phù hợp",
            first_config.system_instruction,
        )
        self.assertIn(
            "Không dùng Google Search/grounding trước khi đã nhận FunctionResponse",
            first_config.system_instruction,
        )
        request_payload = json.loads(client.completions.calls[0]["contents"][0].parts[0].text)
        self.assertEqual(request_payload["framework_context"]["tool_grants"], ["search_local_text"])
        self.assertNotIn("target_slots", json.dumps(request_payload))

    def test_google_passage_can_be_mixed_with_local_item_without_uri(self) -> None:
        call = SimpleNamespace(
            name="search_local_text",
            id="call_1",
            args={"query": "mốc lịch sử", "package_ids": ["history_docs"]},
        )
        agent, registry, _client = self._agent([
            _response(calls=(call,)),
            _response(
                grounding_uri="https://example.test/google-source",
                text=json.dumps({
                    "items": [
                        {"candidate_id": "passage_123", "payload": {"passage": "Local."}},
                        {"payload": {"passage": "Google."}},
                    ]
                }),
            ),
        ])

        result = asyncio.run(self._retrieve(agent, registry))

        self.assertTrue(result.google_grounding_used)
        self.assertEqual([item["payload"]["passage"] for item in result.items], ["Local.", "Google."])

    def test_invalid_json_is_sent_back_to_the_same_agent_once(self) -> None:
        agent, registry, client = self._agent([
            _response(text='{"warnings": []}'),
            _response(text='{"items": []}'),
        ])

        result = asyncio.run(self._retrieve(agent, registry))

        self.assertEqual(result.items, ())
        self.assertEqual(len(client.completions.calls), 2)
        second_messages = client.completions.calls[1]["contents"]
        self.assertIn("JSON chỉ được có field items.", second_messages[-1].parts[0].text)

    def test_executor_stores_only_items_under_task_evidence_ref(self) -> None:
        received = []
        test_case = self

        class _Agent:
            async def retrieve(self, request, gateway, access_context):
                received.append((request, gateway, access_context))
                with test_case.assertRaisesRegex(KnowledgeAccessError, "not granted"):
                    await gateway.search_media(
                        {"query": "ảnh", "package_ids": ["history_media"]},
                        access_context,
                    )
                from final_framework.knowledge.information_retrieval import InformationRetrievalResult
                return InformationRetrievalResult(
                    items=({"candidate_id": "passage_123", "payload": {"passage": "Nội dung."}},),
                    local_search_count=1,
                    google_grounding_used=False,
                    duration_ms=12,
                )

        registry = _Registry()
        gateway = KnowledgeGateway(registry, KnowledgeGrantRegistry())
        factory = AgentTaskRequestFactory(KNOWLEDGE_TOOL_REGISTRY)
        task_result = asyncio.run(
            SearchInformationTaskExecutor(_Agent(), factory, gateway)(_context())
        )

        self.assertEqual(task_result.evidence_ref, "#E1")
        self.assertEqual(task_result.artifact.artifact_type, "knowledge")
        self.assertFalse(hasattr(task_result.artifact, "target_slots"))
        self.assertEqual(task_result.artifact.data, {
            "items": [{"candidate_id": "passage_123", "payload": {"passage": "Nội dung."}}]
        })
        request = received[0][0]
        self.assertEqual(request.framework_context.tool_grants, (SEARCH_LOCAL_TEXT,))
        self.assertEqual(request.input["query"], "Tìm mốc lịch sử.")
        self.assertEqual(gateway.grant_registry.active_grant_count(), 0)

        executor_registry = build_default_executor_registry()
        bound = bind_search_information_task(
            executor_registry,
            _Agent(),
            factory,
            gateway,
        )
        self.assertIs(executor_registry.resolve(SEARCH_INFORMATION_TASK), bound)

    def test_executor_revokes_grant_when_agent_fails(self) -> None:
        class _FailingAgent:
            async def retrieve(self, request, gateway, access_context):
                raise RuntimeError("agent failed")

        registry = _Registry()
        gateway = KnowledgeGateway(registry, KnowledgeGrantRegistry())
        factory = AgentTaskRequestFactory(KNOWLEDGE_TOOL_REGISTRY)

        with self.assertRaisesRegex(RuntimeError, "agent failed"):
            asyncio.run(
                SearchInformationTaskExecutor(
                    _FailingAgent(),
                    factory,
                    gateway,
                )(_context())
            )

        self.assertEqual(gateway.grant_registry.active_grant_count(), 0)

    def test_validator_accepts_google_passage_without_checking_grounding(self) -> None:
        errors = validate_information_result(
            json.dumps({"items": [{"payload": {"passage": "Có nội dung."}}]}),
            local_evidence=(),
        )
        self.assertEqual(errors, [])

    def test_validator_rejects_unknown_local_candidate_and_google_uri_field(self) -> None:
        errors = validate_information_result(
            json.dumps({
                "items": [
                    {
                        "candidate_id": "missing",
                        "payload": {"passage": "Có nội dung."},
                    },
                    {
                        "source_uri": "https://example.test/not-allowed",
                        "payload": {"passage": "Không dùng URI Google."},
                    },
                ]
            }),
            local_evidence=(),
        )
        self.assertEqual(errors, [
            "items[0].candidate_id không tồn tại trong local evidence.",
            "items[1] chỉ được có candidate_id và payload.",
        ])


if __name__ == "__main__":
    unittest.main()
