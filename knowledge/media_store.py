"""Local binary-media storage for V1 knowledge packages."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


DEFAULT_MEDIA_PATH = Path(__file__).resolve().parent.parent / "knowledge_data" / "media"
_MEDIA_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_MIME_EXTENSIONS = {
    "image/avif": "avif",
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/svg+xml": "svg",
    "image/webp": "webp",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


class MediaStoreError(ValueError):
    """A binary-media operation cannot be completed safely."""


class FilesystemMediaStore:
    """Store binary media below one independent, persistent directory.

    ``object_key`` is deliberately simple: ``<media_id>.<source_revision>.<MIME extension>``.
    The media ID is an application ID, not a content hash and not a secret.
    """

    def __init__(self, root: Path | str = DEFAULT_MEDIA_PATH) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, media_id: str, source_revision: str, mime_type: str, data: bytes) -> str:
        """Write one revision safely and return its versioned object key."""

        safe_id = _media_id(media_id)
        safe_revision = _revision(source_revision)
        extension = _extension(mime_type)
        if not isinstance(data, bytes) or not data:
            raise MediaStoreError("media data must be non-empty bytes.")
        object_key = f"{safe_id}.{safe_revision}.{extension}"
        target = self._path(object_key)

        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{safe_id}.", suffix=".tmp", dir=self.root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return object_key

    def read(self, object_key: str) -> bytes:
        """Return stored bytes, or report a missing local file explicitly."""

        path = self._path(object_key)
        if not path.is_file():
            raise FileNotFoundError(f"Local media file is missing: {object_key}")
        return path.read_bytes()

    def path(self, object_key: str) -> Path:
        """Return a validated local path for a browser-serving boundary."""

        return self._path(object_key)

    def local_file_missing(self, object_key: str) -> bool:
        """Return the retrieval flag required when a Chroma record has no file."""

        return not self._path(object_key).is_file()

    def delete(self, object_key: str) -> None:
        """Remove one no-longer-referenced, versioned local object."""

        self._path(object_key).unlink(missing_ok=True)

    def _path(self, object_key: str) -> Path:
        if not isinstance(object_key, str) or not object_key or Path(object_key).name != object_key:
            raise MediaStoreError("object_key must be a filename inside the media store.")
        path = (self.root / object_key).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise MediaStoreError("object_key resolves outside the media store.") from exc
        return path


def _media_id(value: object) -> str:
    if not isinstance(value, str) or not _MEDIA_ID.fullmatch(value):
        raise MediaStoreError("media_id may contain only letters, digits, underscores, and hyphens.")
    return value


def _extension(mime_type: object) -> str:
    if not isinstance(mime_type, str) or mime_type not in _MIME_EXTENSIONS:
        raise MediaStoreError(f"Unsupported media MIME type: {mime_type!r}.")
    return _MIME_EXTENSIONS[mime_type]


def _revision(value: object) -> str:
    if not isinstance(value, str) or not _MEDIA_ID.fullmatch(value):
        raise MediaStoreError("source_revision may contain only letters, digits, underscores, and hyphens.")
    return value
