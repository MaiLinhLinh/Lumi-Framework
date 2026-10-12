from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from final_framework.knowledge import ChromaKnowledgeStore, FilesystemMediaStore
from final_framework.knowledge.chroma_store import EMBEDDING_DIMENSION
from final_framework.knowledge.wikipedia_ingestion import WikipediaIngestionProvider


class FakeEmbeddings:
    def embed_documents(self, texts): return [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in texts]
    def embed_query(self, query): return [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)


class FakeWikipedia:
    def parse_page(self, title):
        return {"title": "Hồ Chí Minh", "revid": 123, "text": "<p>Đoạn mở đầu.</p><h2>Tiểu sử</h2><p>Đoạn tiểu sử.</p>"}
    def list_images(self, title):
        return [{"title": "File:portrait.jpg", "url": "https://upload.example/portrait.jpg", "mime_type": "image/jpeg", "license": "CC BY-SA"}, {"title": "File:bad.jpg", "url": "https://upload.example/bad.jpg", "mime_type": "image/jpeg"}]
    def download(self, url):
        if "bad" in url:
            raise RuntimeError("download failed")
        return b"image", "image/jpeg"


class WikipediaIngestionTests(unittest.TestCase):
    def test_ingests_paragraphs_all_available_images_and_warns_for_failed_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chroma = ChromaKnowledgeStore(FakeEmbeddings(), root / "chroma")
            try:
                provider = WikipediaIngestionProvider(chroma, FilesystemMediaStore(root / "media"), FakeWikipedia())
                result = provider.ingest("https://vi.wikipedia.org/wiki/H%E1%BB%93_Ch%C3%AD_Minh")

                self.assertEqual((result.passages, result.media), (2, 1))
                self.assertEqual(result.source_revision, "123")
                self.assertEqual(len(result.warnings), 1)
                hits = chroma.search("passages", "Hồ Chí Minh", ["wikipedia_vi"])
                self.assertEqual(hits[1].metadata["section_path"], "Tiểu sử")
                media = chroma.search("media", "Hồ Chí Minh", ["wikipedia_vi"])
                self.assertEqual(media[0].metadata["title"], "portrait.jpg")
            finally:
                chroma.close()
                from chromadb.api.shared_system_client import SharedSystemClient
                SharedSystemClient.clear_system_cache()
