from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from final_framework.knowledge import (
    EMBEDDING_DIMENSION,
    ChromaKnowledgeStore,
    KnowledgeStoreError,
)


class FakeEmbeddings:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.document_inputs: list[list[str]] = []
        self.query_inputs: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding unavailable")
        self.document_inputs.append(list(texts))
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        self.query_inputs.append(query)
        return self._vector(query)

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSION
        vector[0] = 1.0 if "Hồ Chí Minh" in text else 0.5
        vector[1] = 0.5
        return vector


class ChromaKnowledgeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.embeddings = FakeEmbeddings()
        self.store = ChromaKnowledgeStore(self.embeddings, Path(self.tempdir.name) / "chroma")

    def tearDown(self) -> None:
        self.store.close()
        from chromadb.api.shared_system_client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
        self.tempdir.cleanup()

    def test_stores_global_collections_and_searches_only_selected_active_package(self) -> None:
        self.store.stage_and_activate(*self._revision("r1"))
        self.store.stage_and_activate(*self._revision("r1", package_id="history_docs", document_id="doc-other"))

        hits = self.store.search("passages", "Hồ Chí Minh", ["wikipedia_vi"])

        self.assertEqual([hit.candidate_id for hit in hits], ["passage-doc-1"])
        self.assertEqual(hits[0].metadata["package_id"], "wikipedia_vi")
        self.assertEqual(hits[0].document, "Ông sinh năm 1890.")
        self.assertEqual(self.embeddings.query_inputs, ["Hồ Chí Minh"])

    def test_passage_and_media_embedding_inputs_follow_the_approved_format(self) -> None:
        self.store.stage_and_activate(*self._revision("r1"))

        all_inputs = [text for batch in self.embeddings.document_inputs for text in batch]

        self.assertIn("Tiêu đề bài: Hồ Chí Minh\nMục: Tiểu sử > Xuất thân\nĐoạn: Ông sinh năm 1890.", all_inputs)
        self.assertIn("Title: Chân dung\nCaption: Hồ Chí Minh thời trẻ\nAlt text: Ảnh chân dung", all_inputs)

    def test_failed_new_revision_keeps_active_revision(self) -> None:
        self.store.stage_and_activate(*self._revision("r1"))
        failing_store = ChromaKnowledgeStore(FakeEmbeddings(fail=True), self.store.path)

        try:
            with self.assertRaisesRegex(RuntimeError, "embedding unavailable"):
                failing_store.stage_and_activate(*self._revision("r2", text="Nội dung mới."))
        finally:
            failing_store.close()

        hits = self.store.search("passages", "Hồ Chí Minh", ["wikipedia_vi"])
        self.assertEqual([hit.document for hit in hits], ["Ông sinh năm 1890."])
        self.assertEqual(self.store.count_active("passages", "doc-1"), 1)

    def test_replaces_old_active_revision_only_after_new_revision_is_staged(self) -> None:
        self.store.stage_and_activate(*self._revision("r1"))
        self.store.stage_and_activate(*self._revision("r2", text="Nội dung cập nhật."))

        hits = self.store.search("passages", "Hồ Chí Minh", ["wikipedia_vi"])

        self.assertEqual([hit.document for hit in hits], ["Nội dung cập nhật."])
        self.assertEqual(hits[0].metadata["source_revision"], "r2")
        self.assertEqual(self.store.count_active("documents", "doc-1"), 1)
        self.assertEqual(self.store.count_active("passages", "doc-1"), 1)
        self.assertEqual(self.store.count_active("media", ""), 1)

    def test_rejects_media_without_usable_text(self) -> None:
        document, passages, media = self._revision("r1")
        media[0].pop("title")
        media[0].pop("caption")
        media[0].pop("alt_text")

        with self.assertRaisesRegex(KnowledgeStoreError, "requires title"):
            self.store.stage_and_activate(document, passages, media)

    @staticmethod
    def _revision(
        revision: str,
        *,
        package_id: str = "wikipedia_vi",
        document_id: str = "doc-1",
        text: str = "Ông sinh năm 1890.",
    ) -> tuple[dict[str, str], list[dict[str, str | int]], list[dict[str, str]]]:
        document = {
            "document_id": document_id,
            "package_id": package_id,
            "title": "Hồ Chí Minh",
            "source_uri": f"https://example.test/{document_id}",
            "source_revision": revision,
            "retrieved_at": "2026-09-17T00:00:00Z",
        }
        passages = [{
            "passage_id": f"passage-{document_id}",
            "document_id": document_id,
            "package_id": package_id,
            "text": text,
            "passage_ordinal": 0,
            "section_title": "Xuất thân",
            "section_path": "Tiểu sử > Xuất thân",
            "source_revision": revision,
        }]
        media = [{
            "media_id": f"media-{document_id}",
            "origin_document_id": document_id,
            "package_id": package_id,
            "mime_type": "image/jpeg",
            "title": "Chân dung",
            "caption": "Hồ Chí Minh thời trẻ",
            "alt_text": "Ảnh chân dung",
            "source_uri": f"https://example.test/{document_id}.jpg",
            "source_revision": revision,
        }]
        return document, passages, media
