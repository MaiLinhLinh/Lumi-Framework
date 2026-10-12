from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from final_framework.knowledge import (
    SEARCH_LOCAL_MEDIA,
    SEARCH_LOCAL_TEXT,
    KnowledgeAccessError,
    KnowledgeGateway,
    KnowledgeGrantRegistry,
    KnowledgePackageManifest,
    KnowledgeRegistry,
    KnowledgeToolArgumentsError,
    LoadedKnowledgePackage,
)


class FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RecordingProvider:
    def __init__(self, package_id: str) -> None:
        self.package_id = package_id
        self.text_calls: list[dict] = []
        self.media_calls: list[dict] = []

    def search_text(self, request):
        self.text_calls.append(dict(request))
        return [self._candidate("text")]

    def search_media(self, request):
        self.media_calls.append(dict(request))
        return [self._candidate("image")]

    def _candidate(self, kind: str) -> dict:
        return {
            "candidate_id": f"{self.package_id}-{kind}",
            "package_id": self.package_id,
            "similarity_score": 0.9,
            "provenance": {},
            "payload": {"kind": kind},
        }


class KnowledgeGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = FakeClock()
        self.grants = KnowledgeGrantRegistry(
            default_ttl_seconds=10,
            clock=self.clock,
        )
        self.registry = KnowledgeRegistry(self.root)
        self.provider = self._register("wikipedia_vi")
        self.gateway = KnowledgeGateway(self.registry, self.grants)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_granted_tool_and_catalog_package_are_dispatched(self) -> None:
        text_access = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_text",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        media_access = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_media",
            allowed_tools=(SEARCH_LOCAL_MEDIA,),
        )

        text = await self.gateway.search_text(
            {"query": "  Hồ Chí Minh  ", "package_ids": ["wikipedia_vi"]},
            text_access,
        )
        media = await self.gateway.search_media(
            {"query": "chân dung", "package_ids": ["wikipedia_vi"]},
            media_access,
        )

        self.assertEqual(text["candidates"][0]["payload"]["kind"], "text")
        self.assertEqual(media["candidates"][0]["payload"]["kind"], "image")
        self.assertEqual(
            self.provider.text_calls,
            [{"query": "Hồ Chí Minh", "package_ids": ["wikipedia_vi"]}],
        )
        self.assertEqual(
            self.provider.media_calls,
            [{"query": "chân dung", "package_ids": ["wikipedia_vi"]}],
        )

    async def test_wrong_tool_never_reaches_the_other_provider_method(self) -> None:
        text_access = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_1",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )

        with self.assertRaisesRegex(KnowledgeAccessError, "not granted"):
            await self.gateway.search_media(
                {"query": "ảnh", "package_ids": ["wikipedia_vi"]},
                text_access,
            )

        self.assertEqual(self.provider.text_calls, [])
        self.assertEqual(self.provider.media_calls, [])

    async def test_package_outside_catalog_snapshot_is_denied_before_dispatch(self) -> None:
        access = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_1",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        self._register("history_docs")

        with self.assertRaisesRegex(KnowledgeAccessError, "outside this task grant"):
            await self.gateway.search_text(
                {"query": "lịch sử", "package_ids": ["history_docs"]},
                access,
            )

    async def test_expired_and_revoked_tokens_are_denied(self) -> None:
        expired = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_expired",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
            ttl_seconds=1,
        )
        self.clock.value += 1
        with self.assertRaisesRegex(KnowledgeAccessError, "expired"):
            await self.gateway.search_text(
                {"query": "test", "package_ids": ["wikipedia_vi"]},
                expired,
            )

        active = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_revoked",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        self.assertTrue(self.grants.revoke(active))
        with self.assertRaisesRegex(KnowledgeAccessError, "invalid or revoked"):
            await self.gateway.search_text(
                {"query": "test", "package_ids": ["wikipedia_vi"]},
                active,
            )

    async def test_retry_grant_is_independent_from_previous_attempt_cleanup(self) -> None:
        previous_attempt = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_1",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        retry_attempt = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_1",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        self.assertNotEqual(
            previous_attempt.transport_token,
            retry_attempt.transport_token,
        )

        self.assertTrue(self.grants.revoke(previous_attempt))
        result = await self.gateway.search_text(
            {"query": "retry", "package_ids": ["wikipedia_vi"]},
            retry_attempt,
        )
        self.assertEqual(result["candidates"][0]["payload"]["kind"], "text")
        with self.assertRaises(KnowledgeAccessError):
            await self.gateway.search_text(
                {"query": "old", "package_ids": ["wikipedia_vi"]},
                previous_attempt,
            )
        self.assertEqual(self.grants.revoke_run("run_1"), 1)
        self.assertEqual(self.grants.active_grant_count(), 0)

    def test_token_is_absent_from_repr_and_loggable_fields(self) -> None:
        access = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_1",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        secret = access.transport_token

        self.assertNotIn(secret, repr(access))
        self.assertNotIn(secret, repr(access.log_fields()))
        self.assertEqual(access.log_fields()["credential"], "[REDACTED]")

    async def test_invalid_tool_arguments_are_rejected_before_dispatch(self) -> None:
        access = self.gateway.issue_access_context(
            run_id="run_1",
            task_id="task_1",
            allowed_tools=(SEARCH_LOCAL_TEXT,),
        )
        with self.assertRaises(KnowledgeToolArgumentsError):
            await self.gateway.search_text(
                {"query": "test", "package_ids": ["wikipedia_vi"], "extra": True},
                access,
            )
        self.assertEqual(self.provider.text_calls, [])

    def _register(self, package_id: str) -> RecordingProvider:
        package_root = self.root / package_id
        package_root.mkdir(parents=True, exist_ok=True)
        provider = RecordingProvider(package_id)
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
        self.registry.register(
            LoadedKnowledgePackage(
                root=package_root,
                manifest=manifest,
                search_text=provider.search_text,
                search_media=provider.search_media,
            )
        )
        return provider


if __name__ == "__main__":
    unittest.main()
