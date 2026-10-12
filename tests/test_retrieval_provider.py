from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from final_framework.knowledge import ChromaKnowledgeStore, FilesystemMediaStore
from final_framework.knowledge.chroma_store import EMBEDDING_DIMENSION
from final_framework.knowledge.retrieval_provider import MediaRetrievalProvider, TextRetrievalProvider


class FakeEmbeddings:
    def embed_documents(self, texts): return [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in texts]
    def embed_query(self, query): return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)


class RetrievalProviderTests(unittest.TestCase):
    def test_role_binding_and_candidate_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ChromaKnowledgeStore(FakeEmbeddings(), root / "chroma")
            media_store = FilesystemMediaStore(root / "media")
            try:
                store.stage_and_activate({"document_id": "doc", "package_id": "wikipedia_vi", "title": "Title", "source_uri": "https://example/doc", "source_revision": "r1", "retrieved_at": "now"}, [{"passage_id": "passage", "document_id": "doc", "package_id": "wikipedia_vi", "text": "A passage", "passage_ordinal": 0, "section_title": "Section", "section_path": "Section", "source_revision": "r1"}])
                key = media_store.put("media", "r1", "image/jpeg", b"bytes")
                store.stage_media_and_activate([{"media_id": "media", "package_id": "wikipedia_vi", "mime_type": "image/jpeg", "title": "Caption", "source_uri": "https://example/image", "source_revision": "r1", "object_key": key}])

                text = TextRetrievalProvider(store).search({"query": "question", "package_ids": ["wikipedia_vi"]})
                image = MediaRetrievalProvider(store, media_store).search({"query": "question", "package_ids": ["wikipedia_vi"]})

                self.assertEqual(set(text[0]), {"candidate_id", "package_id", "similarity_score", "provenance", "payload"})
                self.assertEqual(text[0]["payload"]["passage"], "A passage")
                self.assertFalse(image[0]["payload"]["local_file_missing"])
                self.assertNotIn("target", text[0])
            finally:
                store.close()
                from chromadb.api.shared_system_client import SharedSystemClient
                SharedSystemClient.clear_system_cache()
