"""Session-scoped references to media candidates selected by a Worker.

The knowledge index owns MediaRecords and the filesystem owns their binary
objects.  This small runtime store is the trust boundary between a Worker
result and the Surface compiler: a model may select a ``media_id`` only after
the corresponding candidate has been registered for the current Live session.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.parse import quote

from .media_store import FilesystemMediaStore, MediaStoreError


class MediaReferenceStoreError(ValueError):
    """A selected media reference is not available for the current session."""


@dataclass(frozen=True, slots=True)
class MediaReference:
    media_id: str
    package_id: str
    object_key: str | None
    mime_type: str
    caption: str
    source_uri: str
    url: str
    local_file_missing: bool


class MediaReferenceStore:
    """Keep Worker-selected media references scoped to one Live session."""

    LOCAL_MEDIA_URL_PREFIX = "/knowledge/media"

    def __init__(self, media_store: FilesystemMediaStore) -> None:
        if not isinstance(media_store, FilesystemMediaStore):
            raise MediaReferenceStoreError("media_store must be a FilesystemMediaStore.")
        self._media_store = media_store
        self._references: dict[tuple[str, str], MediaReference] = {}
        self._lock = Lock()

    @property
    def media_store(self) -> FilesystemMediaStore:
        """The shared binary store for trusted retrieved and generated media."""

        return self._media_store

    def register_worker_result(self, *, session_id: str, result: Mapping[str, Any]) -> tuple[str, ...]:
        """Register valid media candidates from one Media Worker result.

        Non-media or malformed items are ignored here; the Worker result
        validator is responsible for reporting those to the Plan Agent.  A
        media reference is still marked missing when its Chroma record exists
        but its local binary has disappeared.
        """

        safe_session = _text(session_id, "session_id")
        if not isinstance(result, Mapping):
            return ()
        # The Media Worker may return a local media_id or a Google image URL.
        # The complete trusted metadata is attached to the runtime result as
        # non-serialized evidence. Keep the items fallback for direct callers
        # that already provide complete candidate payloads.
        raw_items = getattr(result, "evidence", None)
        if not isinstance(raw_items, list):
            raw_items = result.get("evidence")
        if not isinstance(raw_items, list):
            raw_items = result.get("items")
        if not isinstance(raw_items, list):
            return ()
        registered: list[str] = []
        with self._lock:
            for item in raw_items:
                if not isinstance(item, Mapping):
                    continue
                payload = item.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                media_id = _optional_text(payload.get("media_id"))
                package_id = _optional_text(item.get("package_id"))
                object_key = _optional_text(payload.get("object_key"))
                mime_type = _optional_text(payload.get("mime_type"))
                source_uri = _optional_text(payload.get("source_uri"))
                if not all((media_id, package_id, mime_type, source_uri)):
                    continue
                missing = False
                if object_key:
                    try:
                        missing = self._media_store.local_file_missing(object_key)
                    except MediaStoreError:
                        continue
                caption = (
                    _optional_text(payload.get("caption"))
                    or _optional_text(payload.get("alt_text"))
                    or _optional_text(payload.get("title"))
                    or ""
                )
                url = (
                    f"{self.LOCAL_MEDIA_URL_PREFIX}/{quote(object_key, safe='')}"
                    if object_key
                    else source_uri
                )
                reference = MediaReference(
                    media_id=media_id,
                    package_id=package_id,
                    object_key=object_key or None,
                    mime_type=mime_type,
                    caption=caption,
                    source_uri=source_uri,
                    url=url,
                    local_file_missing=missing or payload.get("local_file_missing") is True,
                )
                self._references[(safe_session, media_id)] = reference
                registered.append(media_id)
        return tuple(registered)

    def get(self, *, session_id: str, media_id: str) -> MediaReference:
        safe_session = _text(session_id, "session_id")
        safe_media_id = _text(media_id, "media_id")
        with self._lock:
            reference = self._references.get((safe_session, safe_media_id))
        if reference is None:
            raise MediaReferenceStoreError(
                "media_id is unavailable for this session; Media Worker must find it first."
            )
        return reference

    def clear_session(self, session_id: str) -> None:
        safe_session = _text(session_id, "session_id")
        with self._lock:
            for key in tuple(self._references):
                if key[0] == safe_session:
                    del self._references[key]


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MediaReferenceStoreError(f"{field} must be a non-empty string.")
    return value.strip()


def _optional_text(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""
