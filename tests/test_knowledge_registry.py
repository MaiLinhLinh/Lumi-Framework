from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from final_framework.knowledge import (
    EMBEDDING_DIMENSION,
    ChromaKnowledgeStore,
    FilesystemMediaStore,
    KnowledgeManifestError,
    KnowledgeRegistry,
    KnowledgeRegistryError,
)
from final_framework.knowledge.runtime import build_knowledge_registry


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in texts]

    def embed_query(self, query):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)


class KnowledgeRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "knowledge_packages"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_loads_text_media_and_combined_packages_into_one_catalog(self) -> None:
        self._package("history_docs", content_types=("text",))
        self._package("museum_images", content_types=("image",))
        self._package("wikipedia_vi", content_types=("text", "image"))
        registry = KnowledgeRegistry(self.root)

        registry.load_all()

        self.assertEqual(
            registry.package_catalog(),
            [
                {
                    "package_id": "history_docs",
                    "description": "Knowledge package history_docs.",
                    "languages": ["vi"],
                    "content_types": ["text"],
                },
                {
                    "package_id": "museum_images",
                    "description": "Knowledge package museum_images.",
                    "languages": ["vi"],
                    "content_types": ["image"],
                },
                {
                    "package_id": "wikipedia_vi",
                    "description": "Knowledge package wikipedia_vi.",
                    "languages": ["vi"],
                    "content_types": ["text", "image"],
                },
            ],
        )
        self.assertEqual(
            [card["source_id"] for card in registry.information_source_cards()],
            ["history_docs", "wikipedia_vi"],
        )
        self.assertEqual(
            [card["source_id"] for card in registry.media_source_cards()],
            ["museum_images", "wikipedia_vi"],
        )

    def test_dispatches_text_and_media_to_the_matching_provider_method(self) -> None:
        self._package("wikipedia_vi", content_types=("text", "image"))
        registry = KnowledgeRegistry(self.root)
        registry.load_all()

        text = registry.search_text(
            {"query": "Hồ Chí Minh", "package_ids": ["wikipedia_vi"]}
        )
        media = registry.search_media(
            {"query": "Hồ Chí Minh", "package_ids": ["wikipedia_vi"]}
        )

        self.assertEqual(text["candidates"][0]["payload"]["kind"], "text")
        self.assertEqual(media["candidates"][0]["payload"]["kind"], "image")
        self.assertEqual(text["package_errors"], [])
        self.assertEqual(media["package_errors"], [])

    def test_rejects_operation_not_supported_by_selected_package(self) -> None:
        self._package("history_docs", content_types=("text",))
        self._package("museum_images", content_types=("image",))
        registry = KnowledgeRegistry(self.root)
        registry.load_all()

        with self.assertRaisesRegex(KnowledgeRegistryError, "does not support image"):
            registry.search_media(
                {"query": "ảnh", "package_ids": ["history_docs"]}
            )
        with self.assertRaisesRegex(KnowledgeRegistryError, "does not support text"):
            registry.search_text(
                {"query": "tư liệu", "package_ids": ["museum_images"]}
            )

    def test_parallel_search_preserves_success_when_another_package_fails(self) -> None:
        self._package("wikipedia_vi", content_types=("text",), scores=[0.99])
        self._package(
            "history_docs",
            content_types=("text",),
            failing_operation="text",
        )
        registry = KnowledgeRegistry(self.root)
        registry.load_all()

        result = asyncio.run(
            registry.search_text_parallel(
                {
                    "query": "test",
                    "package_ids": ["wikipedia_vi", "history_docs"],
                }
            )
        )

        self.assertEqual(
            [item["package_id"] for item in result["candidates"]],
            ["wikipedia_vi"],
        )
        self.assertEqual(
            result["package_errors"],
            [{"package_id": "history_docs", "message": "provider timeout"}],
        )

    def test_one_registry_dispatches_text_and_media(self) -> None:
        self._package("wikipedia_vi", content_types=("text", "image"))
        registry = KnowledgeRegistry(self.root)
        registry.load_all()

        self.assertEqual(
            registry.search_text(
                {"query": "fact", "package_ids": ["wikipedia_vi"]}
            )["candidates"][0]["payload"]["kind"],
            "text",
        )
        self.assertEqual(
            registry.search_media(
                {"query": "image", "package_ids": ["wikipedia_vi"]}
            )["candidates"][0]["payload"]["kind"],
            "image",
        )

    def test_rejects_invalid_content_type_and_missing_provider_method(self) -> None:
        package = self._package("wikipedia_vi", content_types=("text",))
        manifest_path = package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["content_types"] = ["audio"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeManifestError, "unsupported values"):
            KnowledgeRegistry(self.root).load_all()

        manifest["content_types"] = ["text"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self._write_provider(
            package,
            "class Provider:\n    pass\n\n"
            "def create_provider(runtime_context):\n    return Provider()\n",
        )
        with self.assertRaisesRegex(KnowledgeManifestError, "search_text"):
            KnowledgeRegistry(self.root).load_all()

    def test_rejects_provider_without_factory(self) -> None:
        package = self._package("wikipedia_vi", content_types=("text",))
        self._write_provider(package, "VALUE = 1\n")
        with self.assertRaisesRegex(KnowledgeManifestError, "create_provider"):
            KnowledgeRegistry(self.root).load_all()

    def test_rejects_duplicate_registration_unknown_package_and_bad_candidates(self) -> None:
        self._package("wikipedia_vi", content_types=("text",))
        registry = KnowledgeRegistry(self.root)
        package = registry.load_all()[0]
        with self.assertRaisesRegex(KnowledgeRegistryError, "already registered"):
            registry.register(package)
        with self.assertRaisesRegex(KnowledgeRegistryError, "unknown knowledge package"):
            registry.search_text({"query": "test", "package_ids": ["missing"]})

        self._package(
            "history_docs",
            content_types=("text",),
            scores=list(range(11, 0, -1)),
        )
        registry = KnowledgeRegistry(self.root)
        registry.load_all()
        result = registry.search_text(
            {"query": "test", "package_ids": ["history_docs"]}
        )
        self.assertIn("at most 10", result["package_errors"][0]["message"])

    def _package(
        self,
        package_id: str,
        *,
        content_types: tuple[str, ...],
        scores: list[float] | None = None,
        failing_operation: str | None = None,
    ) -> Path:
        package = self.root / package_id
        (package / "schemas").mkdir(parents=True)
        schemas: dict[str, str] = {}
        if "text" in content_types:
            (package / "schemas" / "document.schema.json").write_text(
                "{}", encoding="utf-8"
            )
            schemas["text"] = "schemas/document.schema.json"
        if "image" in content_types:
            (package / "schemas" / "media.schema.json").write_text(
                "{}", encoding="utf-8"
            )
            schemas["media"] = "schemas/media.schema.json"
        (package / "ingestion_provider.py").write_text("", encoding="utf-8")

        values = scores if scores is not None else [0.9]
        methods: list[str] = []
        for content_type, method in (("text", "search_text"), ("image", "search_media")):
            if content_type not in content_types:
                continue
            if failing_operation == content_type:
                body = "        raise RuntimeError('provider timeout')"
            else:
                candidates = [
                    {
                        "candidate_id": f"{package_id}-{content_type}-{index}",
                        "package_id": package_id,
                        "similarity_score": score,
                        "provenance": {
                            "source_uri": f"https://example.test/{index}"
                        },
                        "payload": {"kind": content_type},
                    }
                    for index, score in enumerate(values)
                ]
                body = (
                    f"        assert request['package_ids'] == ['{package_id}']\n"
                    f"        return {candidates!r}"
                )
            methods.append(f"    def {method}(self, request):\n{body}\n")
        provider_body = (
            "class Provider:\n"
            + "\n".join(methods)
            + "\ndef create_provider(runtime_context):\n    return Provider()\n"
        )
        self._write_provider(package, provider_body)
        manifest = {
            "package_id": package_id,
            "display_name": package_id,
            "description": f"Knowledge package {package_id}.",
            "languages": ["vi"],
            "content_types": list(content_types),
            "ingestion_provider": "ingestion_provider.py",
            "retrieval_provider": "retrieval_provider.py",
            "schemas": schemas,
            "storage": {"collection": f"{package_id}_records"},
        }
        (package / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return package

    @staticmethod
    def _write_provider(package: Path, body: str) -> None:
        (package / "retrieval_provider.py").write_text(body, encoding="utf-8")


class WikipediaPackageRuntimeTests(unittest.TestCase):
    def test_discovered_wikipedia_package_uses_injected_stores_for_text_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ChromaKnowledgeStore(FakeEmbeddings(), root / "chroma")
            media_store = FilesystemMediaStore(root / "media")
            try:
                store.stage_and_activate(
                    {
                        "document_id": "doc",
                        "package_id": "wikipedia_vi",
                        "title": "Hồ Chí Minh",
                        "source_uri": "https://example.test/doc",
                        "source_revision": "r1",
                        "retrieved_at": "now",
                    },
                    [{
                        "passage_id": "passage",
                        "document_id": "doc",
                        "package_id": "wikipedia_vi",
                        "text": "Ông sinh năm 1890.",
                        "passage_ordinal": 0,
                        "section_title": "Tiểu sử",
                        "section_path": "Tiểu sử",
                        "source_revision": "r1",
                    }],
                )
                object_key = media_store.put("portrait", "r1", "image/jpeg", b"image")
                store.stage_media_and_activate([{
                    "media_id": "portrait",
                    "package_id": "wikipedia_vi",
                    "mime_type": "image/jpeg",
                    "title": "Chân dung",
                    "source_uri": "https://example.test/portrait.jpg",
                    "source_revision": "r1",
                    "object_key": object_key,
                }])

                registry = build_knowledge_registry(store, media_store=media_store)
                text = registry.search_text(
                    {"query": "Hồ Chí Minh", "package_ids": ["wikipedia_vi"]}
                )
                media = registry.search_media(
                    {"query": "Hồ Chí Minh", "package_ids": ["wikipedia_vi"]}
                )

                self.assertEqual(
                    registry.package_catalog()[0]["package_id"], "wikipedia_vi"
                )
                self.assertEqual(
                    text["candidates"][0]["payload"]["passage"],
                    "Ông sinh năm 1890.",
                )
                self.assertEqual(
                    media["candidates"][0]["payload"]["media_id"], "portrait"
                )
            finally:
                store.close()
                from chromadb.api.shared_system_client import SharedSystemClient

                SharedSystemClient.clear_system_cache()


if __name__ == "__main__":
    unittest.main()
