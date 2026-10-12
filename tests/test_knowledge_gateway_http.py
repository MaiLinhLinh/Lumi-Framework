from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import httpx
from starlette.applications import Starlette

from final_framework.agent_tasks import AgentTaskRequestFactory
from final_framework.async_tasks import (
    GenericHttpAsyncAgentExecutor,
    InMemoryRemoteJobRegistry,
)
from final_framework.knowledge import (
    KNOWLEDGE_TOKEN_HEADER,
    SEARCH_LOCAL_MEDIA,
    SEARCH_LOCAL_TEXT,
    KnowledgeGateway,
    KnowledgeGrantRegistry,
    KnowledgePackageManifest,
    KnowledgeRegistry,
    LoadedKnowledgePackage,
    KnowledgeAccessError,
)
from final_framework.knowledge.http import knowledge_gateway_routes
from final_framework.settings import load_settings
from final_framework.tests.test_async_task_executor import (
    KNOWLEDGE_CATALOG,
    _context,
    _limits,
    _load_manifest,
    _manifest_dict,
    _no_sleep,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class _Provider:
    def __init__(self, package_id: str) -> None:
        self.package_id = package_id

    def search_text(self, request):
        return [self._candidate("text", request)]

    def search_media(self, request):
        return [self._candidate("image", request)]

    def _candidate(self, kind: str, request) -> dict:
        return {
            "candidate_id": f"{self.package_id}-{kind}",
            "package_id": self.package_id,
            "similarity_score": 0.9,
            "provenance": {"query": request["query"]},
            "payload": {"kind": kind},
        }


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class KnowledgeGatewayHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = _Clock()
        self.grants = KnowledgeGrantRegistry(
            default_ttl_seconds=10,
            clock=self.clock,
        )
        self.registry = KnowledgeRegistry(self.root)
        self._register("wikipedia_vi")
        self.gateway = KnowledgeGateway(self.registry, self.grants)
        self.app = Starlette(routes=list(knowledge_gateway_routes(self.gateway)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.temporary.cleanup()

    async def test_text_media_and_tool_description_routes(self) -> None:
        access = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_1",
            allowed_tools=(SEARCH_LOCAL_TEXT, SEARCH_LOCAL_MEDIA),
        )
        headers = {KNOWLEDGE_TOKEN_HEADER: access.transport_token}

        text = await self.client.post(
            "/api/knowledge/search-text",
            headers=headers,
            json={"query": "Hồ Chí Minh", "package_ids": ["wikipedia_vi"]},
        )
        media = await self.client.post(
            "/api/knowledge/search-media",
            headers=headers,
            json={"query": "chân dung", "package_ids": ["wikipedia_vi"]},
        )
        tool = await self.client.get(
            f"/api/agent-tools/{SEARCH_LOCAL_TEXT}",
            headers=headers,
        )

        self.assertEqual(text.status_code, 200)
        self.assertEqual(text.json()["candidates"][0]["payload"]["kind"], "text")
        self.assertEqual(media.status_code, 200)
        self.assertEqual(media.json()["candidates"][0]["payload"]["kind"], "image")
        self.assertEqual(tool.status_code, 200)
        self.assertEqual(tool.json()["name"], SEARCH_LOCAL_TEXT)
        self.assertEqual(
            tool.json()["input_schema"]["required"],
            ["query", "package_ids"],
        )

    async def test_missing_invalid_expired_and_revoked_tokens_are_401(self) -> None:
        request = {"query": "test", "package_ids": ["wikipedia_vi"]}
        cases: list[dict[str, str]] = [{}, {KNOWLEDGE_TOKEN_HEADER: "invalid-secret"}]

        expired = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_expired",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
            ttl_seconds=1,
        )
        self.clock.value += 1
        cases.append({KNOWLEDGE_TOKEN_HEADER: expired.transport_token})

        revoked = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_revoked",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        self.grants.revoke(revoked)
        cases.append({KNOWLEDGE_TOKEN_HEADER: revoked.transport_token})

        for headers in cases:
            with self.subTest(headers=bool(headers)):
                response = await self.client.post(
                    "/api/knowledge/search-text",
                    headers=headers,
                    json=request,
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "invalid_knowledge_token",
                )

    async def test_tool_and_package_outside_grant_are_403(self) -> None:
        access = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_1",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        headers = {KNOWLEDGE_TOKEN_HEADER: access.transport_token}

        wrong_tool = await self.client.post(
            "/api/knowledge/search-media",
            headers=headers,
            json={"query": "ảnh", "package_ids": ["wikipedia_vi"]},
        )
        described_wrong_tool = await self.client.get(
            f"/api/agent-tools/{SEARCH_LOCAL_MEDIA}",
            headers=headers,
        )
        self._register("history_docs")
        outside_package = await self.client.post(
            "/api/knowledge/search-text",
            headers=headers,
            json={"query": "lịch sử", "package_ids": ["history_docs"]},
        )

        self.assertEqual(wrong_tool.status_code, 403)
        self.assertEqual(described_wrong_tool.status_code, 403)
        self.assertEqual(outside_package.status_code, 403)

    async def test_fake_agent_service_calls_gateway_during_remote_job(self) -> None:
        temporary, manifest = _load_manifest(_manifest_dict(
            auth=False,
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        ))
        self.addCleanup(temporary.cleanup)
        external_candidates: list[dict] = []
        issued_token = ""

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://gateway",
        ) as gateway_client:
            async def agent_handler(request: httpx.Request) -> httpx.Response:
                nonlocal issued_token
                if request.url.path == "/tasks":
                    issued_token = request.headers[KNOWLEDGE_TOKEN_HEADER]
                    gateway_response = await gateway_client.post(
                        "/api/knowledge/search-text",
                        headers={KNOWLEDGE_TOKEN_HEADER: issued_token},
                        json={
                            "query": "Hồ Chí Minh",
                            "package_ids": ["wikipedia_vi"],
                        },
                    )
                    self.assertEqual(gateway_response.status_code, 200)
                    external_candidates.extend(
                        gateway_response.json()["candidates"]
                    )
                    return httpx.Response(202, json={"job_id": "remote_job_1"})
                return httpx.Response(200, json={
                    "status": "completed",
                    "payload": {"translation": "Đã dùng local knowledge"},
                })

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(agent_handler)
            ) as agent_client:
                result = await GenericHttpAsyncAgentExecutor(
                    manifest,
                    InMemoryRemoteJobRegistry(),
                    agent_client,
                    _limits(),
                    request_factory=AgentTaskRequestFactory(),
                    knowledge_catalog=KNOWLEDGE_CATALOG,
                    knowledge_grant_registry=self.grants,
                    sleep=_no_sleep,
                )(_context())

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(external_candidates[0]["payload"]["kind"], "text")
        self.assertTrue(issued_token)
        with self.assertRaises(KnowledgeAccessError):
            self.grants.authenticate(issued_token)

    async def test_malformed_requests_have_stable_status_and_hide_credentials(self) -> None:
        access = self.gateway.issue_access_context(
            run_id="run_secret",
            task_id="task_secret",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        secret = access.transport_token
        headers = {KNOWLEDGE_TOKEN_HEADER: secret}
        log_handler = _ListHandler()
        route_logger = logging.getLogger("lumi.knowledge_gateway_http")
        route_logger.addHandler(log_handler)
        try:
            malformed_json = await self.client.post(
                "/api/knowledge/search-text",
                headers=headers,
                content=b"{not-json",
            )
            malformed_schema = await self.client.post(
                "/api/knowledge/search-text",
                headers=headers,
                json={"query": "test", "package_ids": [], "extra": True},
            )
            missing_tool = await self.client.get(
                "/api/agent-tools/not_registered",
                headers=headers,
            )
        finally:
            route_logger.removeHandler(log_handler)

        self.assertEqual(malformed_json.status_code, 400)
        self.assertEqual(malformed_schema.status_code, 400)
        self.assertEqual(missing_tool.status_code, 404)
        serialized = json.dumps(
            [malformed_json.json(), malformed_schema.json(), missing_tool.json()]
        )
        self.assertNotIn(secret, serialized)
        self.assertNotIn(secret, "\n".join(log_handler.messages))

    def _register(self, package_id: str) -> None:
        package_root = self.root / package_id
        package_root.mkdir(parents=True, exist_ok=True)
        provider = _Provider(package_id)
        manifest = KnowledgePackageManifest(
            package_id=package_id,
            display_name=package_id,
            description=f"Package {package_id}",
            languages=("vi",),
            content_types=("text", "image"),
            ingestion_provider_path=package_root / "ingestion_provider.py",
            retrieval_provider_path=package_root / "retrieval_provider.py",
            schema_paths=MappingProxyType({}),
            storage=MappingProxyType({"collection": package_id}),
        )
        self.registry.register(LoadedKnowledgePackage(
            root=package_root,
            manifest=manifest,
            search_text=provider.search_text,
            search_media=provider.search_media,
        ))


class KnowledgeGatewaySettingsTests(unittest.TestCase):
    def test_token_ttl_is_loaded_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"KNOWLEDGE_TOOL_TOKEN_TTL_SECONDS": "37.5"},
            clear=False,
        ):
            self.assertEqual(load_settings().knowledge_tool_token_ttl_seconds, 37.5)


if __name__ == "__main__":
    unittest.main()
