"""Role-bound retrieval providers backed by the shared Chroma collections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .chroma_store import ChromaKnowledgeStore
from .media_store import FilesystemMediaStore


class RetrievalProviderError(ValueError):
    """A Worker retrieval request or stored candidate is invalid."""


class ChromaRetrievalProvider:
    """Bind one provider instance to one Worker role/collection."""

    def __init__(self, store: ChromaKnowledgeStore, collection_name: str, *, media_store: FilesystemMediaStore | None = None) -> None:
        if collection_name not in {"passages", "media"}:
            raise RetrievalProviderError("collection_name must be 'passages' or 'media'.")
        if collection_name == "media" and media_store is None:
            raise RetrievalProviderError("media_store is required for the Media Worker provider.")
        self._store, self.collection_name, self._media_store = store, collection_name, media_store

    def search(self, request: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(request, Mapping):
            raise RetrievalProviderError("search request must be an object.")
        unsupported = set(request) - {"query", "package_ids"}
        if unsupported:
            raise RetrievalProviderError(f"search request has unsupported fields: {sorted(unsupported)}.")
        query = request.get("query")
        package_ids = request.get("package_ids")
        if not isinstance(query, str) or not query.strip():
            raise RetrievalProviderError("search request.query must be a non-empty string.")
        if not isinstance(package_ids, list) or not package_ids or not all(isinstance(item, str) and item.strip() for item in package_ids):
            raise RetrievalProviderError("search request.package_ids must be a non-empty array of strings.")
        hits = self._store.search(self.collection_name, query, package_ids, limit=10)
        return [self._candidate(hit.candidate_id, hit.similarity_score, hit.metadata, hit.document) for hit in hits]

    def _candidate(self, candidate_id: str, score: float, metadata: Mapping[str, Any], document: str) -> dict[str, Any]:
        if self.collection_name == "passages":
            payload = {
                "passage_id": metadata["passage_id"],
                "document_id": metadata["document_id"],
                "passage": metadata["text"],
                "passage_ordinal": int(metadata["passage_ordinal"]),
                "section_title": metadata["section_title"],
                "section_path": metadata["section_path"],
            }
        else:
            object_key = metadata.get("object_key")
            payload = {key: metadata[key] for key in ("media_id", "title", "caption", "alt_text", "mime_type", "object_key", "source_uri") if metadata.get(key)}
            payload["local_file_missing"] = bool(object_key and self._media_store and self._media_store.local_file_missing(object_key))
        provenance = {key: metadata[key] for key in ("source_uri", "source_revision", "retrieved_at") if metadata.get(key)}
        return {"candidate_id": candidate_id, "package_id": metadata["package_id"], "similarity_score": score, "provenance": provenance, "payload": payload}


class TextRetrievalProvider(ChromaRetrievalProvider):
    def __init__(self, store: ChromaKnowledgeStore) -> None:
        super().__init__(store, "passages")


class MediaRetrievalProvider(ChromaRetrievalProvider):
    def __init__(self, store: ChromaKnowledgeStore, media_store: FilesystemMediaStore) -> None:
        super().__init__(store, "media", media_store=media_store)
