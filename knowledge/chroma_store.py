"""Persistent ChromaDB storage for the V1 knowledge schema.

The public IDs (``document_id``, ``passage_id`` and ``media_id``) stay stable.
Chroma record IDs additionally contain a source revision so a new revision can be
fully staged before the active revision is removed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import chromadb

from .storage_paths import DEFAULT_STORAGE_ROOT, storage_path


EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIMENSION = 768
COLLECTION_NAMES = ("documents", "passages", "media")
DEFAULT_CHROMA_PATH = DEFAULT_STORAGE_ROOT / "chroma"


class KnowledgeStoreError(ValueError):
    """A record cannot be safely stored in the V1 knowledge index."""


class EmbeddingProvider(Protocol):
    """Embed stored text and search queries with compatible task types."""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one 768-dimensional embedding for every stored text."""

    def embed_query(self, query: str) -> list[float]:
        """Return one 768-dimensional embedding for a retrieval query."""


class GeminiEmbeddingProvider:
    """Gemini embedding adapter, instantiated only when an API key is supplied."""

    def __init__(self, api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise KnowledgeStoreError("A Gemini API key is required for live embeddings.")
        from google import genai

        self._client = genai.Client(api_key=api_key.strip())

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, query: str) -> list[float]:
        return self._embed([query], task_type="RETRIEVAL_QUERY")[0]

    def _embed(self, texts: Sequence[str], *, task_type: str) -> list[list[float]]:
        values = _non_empty_texts(texts, "embedding texts")
        from google.genai import types

        # ``gemini-embedding-2`` currently returns one vector per request,
        # even when ``contents`` is supplied as a list. Issue one request per
        # input so passage/media records cannot silently receive a pooled or
        # truncated vector.
        embeddings: list[list[float]] = []
        for value in values:
            response = self._client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=[value],
                config=types.EmbedContentConfig(
                    taskType=task_type,
                    outputDimensionality=EMBEDDING_DIMENSION,
                ),
            )
            response_embeddings = getattr(response, "embeddings", None) or ()
            if len(response_embeddings) != 1:
                raise KnowledgeStoreError(
                    "embedding provider must return exactly one vector per input."
                )
            embeddings.append(list(response_embeddings[0].values))
        _validate_embeddings(embeddings, len(values))
        return embeddings


@dataclass(frozen=True, slots=True)
class ChromaSearchHit:
    candidate_id: str
    similarity_score: float
    metadata: dict[str, Any]
    document: str


class ChromaKnowledgeStore:
    """Own the three global, package-filtered Chroma collections."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        path: Path | str | None = None,
    ) -> None:
        self._embedding_provider = embedding_provider
        self.path = (Path(path).resolve() if path is not None else
                     storage_path("KNOWLEDGE_CHROMA_PATH", DEFAULT_CHROMA_PATH))
        self.path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.path))
        self._collections = {
            name: self._collection(name)
            for name in COLLECTION_NAMES
        }

    def stage_and_activate(
        self,
        document: Mapping[str, Any],
        passages: Sequence[Mapping[str, Any]],
        media: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        """Atomically replace one document revision after all embeddings are ready.

        New data is first written as ``staging``. Only then are prior active
        records deleted and the new revision marked ``active``. If validation or
        embedding fails, the active revision is never touched.
        """

        staged = self._prepare_revision(document, passages)
        try:
            for collection_name, records in staged.items():
                self._upsert(collection_name, records)
        except Exception:
            self._delete_staged(staged)
            raise

        document_id = staged["documents"][0]["metadata"]["document_id"]
        for name, records in staged.items():
            self._collections[name].delete(where={
                "$and": [
                    {"document_id": document_id},
                    {"ingest_state": "active"},
                ]
            })
            self._collections[name].update(
                ids=[record["physical_id"] for record in records],
                metadatas=[{**record["metadata"], "ingest_state": "active"} for record in records],
            )
        if media:
            self.stage_media_and_activate(media)

    def stage_media_and_activate(self, media: Sequence[Mapping[str, Any]]) -> list[str]:
        """Activate independent MediaRecords and return replaced object keys.

        Media is deliberately not scoped to a document: the same Wikimedia image
        can occur in several articles while retaining one media ID and file.
        """

        staged: list[dict[str, Any]] = []
        for item in media:
            value = _media_record(item)
            staged.append(_prepared("media", value, _media_embedding_text(value), value["source_revision"]))
        if not staged:
            return []
        embeddings = self._embedding_provider.embed_documents([record["document"] for record in staged])
        _validate_embeddings(embeddings, len(staged))
        for record, embedding in zip(staged, embeddings, strict=True):
            record["embedding"] = embedding
        try:
            self._upsert("media", staged)
        except Exception:
            self._delete_staged({"media": staged})
            raise

        old_keys: list[str] = []
        collection = self._collections["media"]
        for record in staged:
            media_id = record["metadata"]["media_id"]
            old = collection.get(
                where={"$and": [{"media_id": media_id}, {"ingest_state": "active"}]},
                include=["metadatas"],
            )
            old_keys.extend(
                metadata["object_key"]
                for metadata in old["metadatas"]
                if metadata and metadata.get("object_key")
            )
            collection.delete(where={"$and": [{"media_id": media_id}, {"ingest_state": "active"}]})
        collection.update(
            ids=[record["physical_id"] for record in staged],
            metadatas=[{**record["metadata"], "ingest_state": "active"} for record in staged],
        )
        return old_keys

    def search(
        self,
        collection_name: str,
        query: str,
        package_ids: Sequence[str],
        *,
        limit: int = 10,
    ) -> list[ChromaSearchHit]:
        """Search active records from Worker-selected packages by cosine similarity."""

        collection = self._collections.get(collection_name)
        if collection is None:
            raise KnowledgeStoreError(f"Unknown collection '{collection_name}'.")
        clean_query = _non_empty_text(query, "query")
        clean_packages = _non_empty_texts(package_ids, "package_ids")
        if limit < 1 or limit > 10:
            raise KnowledgeStoreError("limit must be between 1 and 10.")
        package_filter: dict[str, Any]
        if len(clean_packages) == 1:
            package_filter = {"package_id": clean_packages[0]}
        else:
            package_filter = {"package_id": {"$in": clean_packages}}
        result = collection.query(
            query_embeddings=[self._embedding_provider.embed_query(clean_query)],
            n_results=limit,
            where={"$and": [package_filter, {"ingest_state": "active"}]},
            include=["documents", "metadatas", "distances"],
        )
        return [
            ChromaSearchHit(
                candidate_id=metadata[_logical_id_field(collection_name)],
                similarity_score=1.0 - float(distance),
                metadata=dict(metadata),
                document=metadata["text"] if collection_name == "passages" else document,
            )
            for metadata, document, distance in zip(
                result["metadatas"][0], result["documents"][0], result["distances"][0], strict=True
            )
        ]

    def count_active(self, collection_name: str, document_id: str) -> int:
        """Test/support helper: count active records for one logical document."""

        collection = self._collections.get(collection_name)
        if collection is None:
            raise KnowledgeStoreError(f"Unknown collection '{collection_name}'.")
        return collection.count() if not document_id else len(collection.get(where={
            "$and": [{"document_id": document_id}, {"ingest_state": "active"}]
        })["ids"])

    def catalog_inventory(self, package_id: str) -> str:
        """Read current indexed subjects, without embedding or generation calls."""
        from .catalog_inventory import describe_inventory

        return describe_inventory(self._collections, package_id)

    def close(self) -> None:
        """Release Chroma file handles; callers should use this at process shutdown."""

        self._client.close()

    def _collection(self, name: str) -> Any:
        collection = self._client.get_or_create_collection(
            name=name,
            configuration={"hnsw": {"space": "cosine"}},
            embedding_function=None,
        )
        space = collection.configuration.get("hnsw", {}).get("space")
        if space != "cosine":
            raise KnowledgeStoreError(f"Collection '{name}' must use cosine similarity, found '{space}'.")
        return collection

    def _prepare_revision(
        self,
        document: Mapping[str, Any],
        passages: Sequence[Mapping[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        doc = _document_record(document)
        document_id = doc["document_id"]
        revision = doc["source_revision"]
        prepared: dict[str, list[dict[str, Any]]] = {
            "documents": [_prepared("documents", doc, doc["title"], revision)],
            "passages": [],
        }
        for passage in passages:
            value = _passage_record(passage, document_id, revision)
            prepared["passages"].append(_prepared("passages", value, _passage_embedding_text(doc, value), revision))

        for records in prepared.values():
            if not records:
                continue
            embeddings = self._embedding_provider.embed_documents([record["document"] for record in records])
            _validate_embeddings(embeddings, len(records))
            for record, embedding in zip(records, embeddings, strict=True):
                record["embedding"] = embedding
        return prepared

    def _upsert(self, collection_name: str, records: Sequence[Mapping[str, Any]]) -> None:
        if not records:
            return
        self._collections[collection_name].upsert(
            ids=[record["physical_id"] for record in records],
            documents=[record["document"] for record in records],
            metadatas=[record["metadata"] for record in records],
            embeddings=[record["embedding"] for record in records],
        )

    def _delete_staged(self, staged: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        for collection_name, records in staged.items():
            if records:
                self._collections[collection_name].delete(ids=[record["physical_id"] for record in records])


def _document_record(value: Mapping[str, Any]) -> dict[str, str]:
    data = _mapping(value, "document")
    return {
        "document_id": _non_empty_text(data.get("document_id"), "document.document_id"),
        "package_id": _non_empty_text(data.get("package_id"), "document.package_id"),
        "title": _non_empty_text(data.get("title"), "document.title"),
        "source_uri": _non_empty_text(data.get("source_uri"), "document.source_uri"),
        "source_revision": _non_empty_text(data.get("source_revision"), "document.source_revision"),
        "retrieved_at": _non_empty_text(data.get("retrieved_at"), "document.retrieved_at"),
    }


def _passage_record(value: Mapping[str, Any], document_id: str, revision: str) -> dict[str, str]:
    data = _mapping(value, "passage")
    if data.get("document_id") != document_id:
        raise KnowledgeStoreError("passage.document_id must match document.document_id.")
    if data.get("source_revision") != revision:
        raise KnowledgeStoreError("passage.source_revision must match document.source_revision.")
    return {
        "passage_id": _non_empty_text(data.get("passage_id"), "passage.passage_id"),
        "document_id": document_id,
        "package_id": _non_empty_text(data.get("package_id"), "passage.package_id"),
        "text": _non_empty_text(data.get("text"), "passage.text"),
        "passage_ordinal": str(_positive_int(data.get("passage_ordinal"), "passage.passage_ordinal")),
        "section_title": _non_empty_text(data.get("section_title"), "passage.section_title"),
        "section_path": _non_empty_text(data.get("section_path"), "passage.section_path"),
        "source_uri": _optional_text(data.get("source_uri"), "passage.source_uri") or "",
        "retrieved_at": _optional_text(data.get("retrieved_at"), "passage.retrieved_at") or "",
        "source_revision": revision,
    }


def _media_record(value: Mapping[str, Any]) -> dict[str, str]:
    data = _mapping(value, "media")
    title = _optional_text(data.get("title"), "media.title")
    caption = _optional_text(data.get("caption"), "media.caption")
    alt_text = _optional_text(data.get("alt_text"), "media.alt_text")
    if not any((title, caption, alt_text)):
        raise KnowledgeStoreError("media requires title, caption, or alt_text for V1 storage.")
    result = {
        "media_id": _non_empty_text(data.get("media_id"), "media.media_id"),
        "package_id": _non_empty_text(data.get("package_id"), "media.package_id"),
        "mime_type": _non_empty_text(data.get("mime_type"), "media.mime_type"),
        "source_uri": _non_empty_text(data.get("source_uri"), "media.source_uri"),
        "source_revision": _non_empty_text(data.get("source_revision"), "media.source_revision"),
    }
    for key, item in (("title", title), ("caption", caption), ("alt_text", alt_text)):
        if item:
            result[key] = item
    for key in ("object_key", "local_path", "license", "provenance", "origin_document_id"):
        item = _optional_text(data.get(key), f"media.{key}")
        if item:
            result[key] = item
    return result


def _prepared(collection_name: str, data: Mapping[str, str], document: str, revision: str) -> dict[str, Any]:
    logical_id = data[_logical_id_field(collection_name)]
    metadata = {**data, "ingest_state": "staging", "embedding_model": EMBEDDING_MODEL, "embedding_dimension": EMBEDDING_DIMENSION}
    return {
        "physical_id": f"{logical_id}:{revision}",
        "document": document,
        "metadata": metadata,
    }


def _passage_embedding_text(document: Mapping[str, str], passage: Mapping[str, str]) -> str:
    return f"Tiêu đề bài: {document['title']}\nMục: {passage['section_path']}\nĐoạn: {passage['text']}"


def _media_embedding_text(media: Mapping[str, str]) -> str:
    fields = (("Title", media.get("title")), ("Caption", media.get("caption")), ("Alt text", media.get("alt_text")))
    return "\n".join(f"{label}: {value}" for label, value in fields if value)


def _logical_id_field(collection_name: str) -> str:
    return {"documents": "document_id", "passages": "passage_id", "media": "media_id"}[collection_name]


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise KnowledgeStoreError(f"{field} must be an object.")
    return value


def _non_empty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeStoreError(f"{field} must be a non-empty string.")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _non_empty_text(value, field)


def _non_empty_texts(values: Sequence[object], field: str) -> list[str]:
    if not values:
        raise KnowledgeStoreError(f"{field} must not be empty.")
    return [_non_empty_text(value, field) for value in values]


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise KnowledgeStoreError(f"{field} must be a non-negative integer.")
    return value


def _validate_embeddings(embeddings: Sequence[Sequence[float]], expected_count: int) -> None:
    if len(embeddings) != expected_count:
        raise KnowledgeStoreError("embedding provider returned the wrong number of vectors.")
    for embedding in embeddings:
        if len(embedding) != EMBEDDING_DIMENSION:
            raise KnowledgeStoreError(f"embedding provider must return {EMBEDDING_DIMENSION}-dimensional vectors.")
