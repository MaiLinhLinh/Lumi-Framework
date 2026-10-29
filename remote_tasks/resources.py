"""Session-scoped resources normalized from Async Agent Task outputs."""

from __future__ import annotations

import ipaddress
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from .jsonpath import CompiledJsonPath


class ResourceStoreError(ValueError):
    """A resource batch or lookup violates the framework trust boundary."""


@dataclass(frozen=True, slots=True)
class ResourceReference:
    """One Runtime-only external resource; its URI never enters Assembly."""

    resource_id: str
    owner_task_id: str
    attempt: int
    manifest_id: str
    kind: str
    mime_type: str
    source_uri: str
    caption: str = ""
    object_key: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "resource_id",
            "owner_task_id",
            "manifest_id",
            "kind",
            "mime_type",
            "source_uri",
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise ResourceStoreError("resource attempt must be a non-negative integer.")
        if not isinstance(self.caption, str):
            raise ResourceStoreError("resource caption must be a string.")
        object.__setattr__(self, "caption", self.caption.strip())
        if self.object_key is not None:
            object.__setattr__(self, "object_key", _text(self.object_key, "object_key"))


class ResourceStore(ABC):
    """Storage contract for pending and committed external resource batches."""

    @abstractmethod
    def register_batch(
        self,
        session_id: str,
        references: tuple[ResourceReference, ...],
    ) -> tuple[ResourceReference, ...]: ...

    @abstractmethod
    def commit_batch(self, session_id: str, owner_task_id: str, attempt: int) -> None: ...

    @abstractmethod
    def get(self, session_id: str, resource_id: str) -> ResourceReference: ...

    @abstractmethod
    def contains(self, session_id: str, resource_id: str) -> bool: ...

    @abstractmethod
    def discard_batch(self, session_id: str, owner_task_id: str, attempt: int) -> None: ...

    @abstractmethod
    def clear_session(self, session_id: str) -> None: ...


class InMemoryResourceStore(ResourceStore):
    """Atomic in-memory implementation owned by one application Runtime."""

    def __init__(self, allowed_mime_types: tuple[str, ...]) -> None:
        normalized = tuple(_mime_type(value) for value in allowed_mime_types)
        if not normalized:
            raise ResourceStoreError("allowed_mime_types must not be empty.")
        if len(normalized) != len(set(normalized)):
            raise ResourceStoreError("allowed_mime_types must not contain duplicates.")
        self._allowed_mime_types = frozenset(normalized)
        self._pending: dict[tuple[str, str, int], tuple[ResourceReference, ...]] = {}
        self._committed: dict[tuple[str, str], ResourceReference] = {}
        self._committed_batches: dict[
            tuple[str, str, int], tuple[ResourceReference, ...]
        ] = {}
        self._lock = Lock()

    @property
    def allowed_mime_types(self) -> frozenset[str]:
        return self._allowed_mime_types

    def register_batch(
        self,
        session_id: str,
        references: tuple[ResourceReference, ...],
    ) -> tuple[ResourceReference, ...]:
        safe_session = _text(session_id, "session_id")
        if not isinstance(references, tuple) or not references:
            raise ResourceStoreError("resource batch must be a non-empty tuple.")
        if not all(isinstance(reference, ResourceReference) for reference in references):
            raise ResourceStoreError("resource batch must contain ResourceReference values.")
        owner = references[0].owner_task_id
        attempt = references[0].attempt
        if any(
            reference.owner_task_id != owner or reference.attempt != attempt
            for reference in references
        ):
            raise ResourceStoreError("one resource batch must share owner_task_id and attempt.")
        resource_ids = tuple(reference.resource_id for reference in references)
        if len(resource_ids) != len(set(resource_ids)):
            raise ResourceStoreError("resource batch contains duplicate resource_id values.")
        for reference in references:
            self._validate_reference(reference)

        batch_key = (safe_session, owner, attempt)
        with self._lock:
            if batch_key in self._pending or batch_key in self._committed_batches:
                raise ResourceStoreError("resource batch already exists for owner task attempt.")
            for resource_id in resource_ids:
                resource_key = (safe_session, resource_id)
                if resource_key in self._committed or any(
                    resource_id in {item.resource_id for item in batch}
                    for batch in self._pending.values()
                ):
                    raise ResourceStoreError(f"resource_id '{resource_id}' already exists.")
            self._pending[batch_key] = references
        return references

    def commit_batch(self, session_id: str, owner_task_id: str, attempt: int) -> None:
        batch_key = _batch_key(session_id, owner_task_id, attempt)
        with self._lock:
            try:
                references = self._pending.pop(batch_key)
            except KeyError as exc:
                raise ResourceStoreError("pending resource batch does not exist.") from exc
            for reference in references:
                self._committed[(batch_key[0], reference.resource_id)] = reference
            self._committed_batches[batch_key] = references

    def get(self, session_id: str, resource_id: str) -> ResourceReference:
        key = (_text(session_id, "session_id"), _text(resource_id, "resource_id"))
        with self._lock:
            reference = self._committed.get(key)
        if reference is None:
            raise ResourceStoreError("resource_id is unavailable for this Live session.")
        return reference

    def contains(self, session_id: str, resource_id: str) -> bool:
        key = (_text(session_id, "session_id"), _text(resource_id, "resource_id"))
        with self._lock:
            return key in self._committed

    def discard_batch(self, session_id: str, owner_task_id: str, attempt: int) -> None:
        batch_key = _batch_key(session_id, owner_task_id, attempt)
        with self._lock:
            pending = self._pending.pop(batch_key, ())
            committed = self._committed_batches.pop(batch_key, ())
            for reference in (*pending, *committed):
                self._committed.pop((batch_key[0], reference.resource_id), None)

    def clear_session(self, session_id: str) -> None:
        safe_session = _text(session_id, "session_id")
        with self._lock:
            for key in tuple(self._pending):
                if key[0] == safe_session:
                    del self._pending[key]
            for key in tuple(self._committed_batches):
                if key[0] == safe_session:
                    del self._committed_batches[key]
            for key in tuple(self._committed):
                if key[0] == safe_session:
                    del self._committed[key]

    def _validate_reference(self, reference: ResourceReference) -> None:
        if reference.mime_type not in self._allowed_mime_types:
            raise ResourceStoreError(
                f"resource MIME type '{reference.mime_type}' is not allowed."
            )
        expected_kind = resource_kind(reference.mime_type)
        if reference.kind != expected_kind:
            raise ResourceStoreError(
                f"resource kind must be '{expected_kind}' for MIME '{reference.mime_type}'."
            )
        validate_external_resource_uri(reference.source_uri)


class ResourceBatchLifecycle:
    """TaskResult lifecycle which commits or rolls back one resource batch."""

    def __init__(
        self,
        store: ResourceStore,
        *,
        session_id: str,
        owner_task_id: str,
        attempt: int,
    ) -> None:
        if not isinstance(store, ResourceStore):
            raise TypeError("store must implement ResourceStore.")
        self._store = store
        self._session_id = _text(session_id, "session_id")
        self._owner_task_id = _text(owner_task_id, "owner_task_id")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise ResourceStoreError("attempt must be a non-negative integer.")
        self._attempt = attempt
        self._state = "pending"
        self._lock = Lock()

    def commit(self) -> None:
        with self._lock:
            if self._state != "pending":
                raise ResourceStoreError("resource lifecycle is no longer pending.")
            self._store.commit_batch(
                self._session_id,
                self._owner_task_id,
                self._attempt,
            )
            self._state = "committed"

    def rollback(self) -> None:
        with self._lock:
            if self._state == "rolled_back":
                return
            self._store.discard_batch(
                self._session_id,
                self._owner_task_id,
                self._attempt,
            )
            self._state = "rolled_back"


@dataclass(frozen=True, slots=True)
class ResourceMediaReference:
    """Compatibility shape consumed by the current Final Surface Compiler."""

    url: str
    caption: str
    source_uri: str
    mime_type: str
    local_file_missing: bool = False


class ResourceMediaReferenceAdapter:
    """Resolve generic resource IDs before falling back to legacy media refs."""

    def __init__(
        self,
        resource_store: ResourceStore,
        *,
        resource_session_id: str,
        legacy_store: Any | None = None,
    ) -> None:
        if not isinstance(resource_store, ResourceStore):
            raise TypeError("resource_store must implement ResourceStore.")
        if legacy_store is not None and not callable(getattr(legacy_store, "get", None)):
            raise TypeError("legacy_store must provide get().")
        self._resources = resource_store
        self._resource_session_id = _text(resource_session_id, "resource_session_id")
        self._legacy = legacy_store

    @property
    def media_store(self) -> Any:
        if self._legacy is None or not hasattr(self._legacy, "media_store"):
            raise ResourceStoreError("no legacy media_store is configured.")
        return self._legacy.media_store

    def register_worker_result(self, **kwargs: Any) -> tuple[str, ...]:
        if self._legacy is None:
            return ()
        return self._legacy.register_worker_result(**kwargs)

    def get(self, *, session_id: str, media_id: str) -> Any:
        if self._resources.contains(self._resource_session_id, media_id):
            reference = self._resources.get(self._resource_session_id, media_id)
            return ResourceMediaReference(
                url=reference.source_uri,
                caption=reference.caption,
                source_uri=reference.source_uri,
                mime_type=reference.mime_type,
            )
        if self._legacy is None:
            raise ResourceStoreError("media_id is not a registered resource.")
        return self._legacy.get(session_id=session_id, media_id=media_id)

    def clear_session(self, session_id: str) -> None:
        self._resources.clear_session(self._resource_session_id)
        if self._legacy is not None and callable(
            getattr(self._legacy, "clear_session", None)
        ):
            self._legacy.clear_session(session_id)


def resource_kind(mime_type: str) -> str:
    normalized = _mime_type(mime_type)
    for prefix, kind in (("image/", "image"), ("video/", "video"), ("audio/", "audio")):
        if normalized.startswith(prefix):
            return kind
    return "file"


def validate_external_resource_uri(value: str) -> str:
    uri = _text(value, "source_uri")
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise ResourceStoreError("resource URI is invalid.") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ResourceStoreError("resource URI must use HTTPS and include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ResourceStoreError("resource URI must not contain credentials.")
    if port is not None and not 1 <= port <= 65535:
        raise ResourceStoreError("resource URI contains an invalid port.")

    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal", ".home.arpa"))
    ):
        raise ResourceStoreError("resource URI hostname is local or internal.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname:
            raise ResourceStoreError("resource URI hostname is local or internal.")
        return uri
    if not address.is_global:
        raise ResourceStoreError("resource URI IP address must be globally routable.")
    return uri


def normalized_resource_output_contract(
    schema: Any,
    resource_rules: tuple[Any, ...],
) -> dict[str, Any]:
    """Return the post-normalization schema shown to Assembly.

    The service schema remains the raw-response validator. This copied contract
    replaces each declared URI field with the framework-owned resource_id and
    records the transformation as descriptive extension metadata.
    """

    contract = _json_copy(schema)
    transformations: list[dict[str, str]] = []
    for rule in resource_rules:
        item_schema = _schema_at_instance_path(contract, contract, rule.items_path)
        if not rule.uri_path.segments or rule.uri_path.segments[-1].kind != "field":
            raise ResourceStoreError("resource uri_path must end in an object field.")
        parent_path = CompiledJsonPath(
            "$",
            rule.uri_path.segments[:-1],
        )
        parent_schema = _schema_at_instance_path(contract, item_schema, parent_path)
        parent_schema = _resolve_schema_ref(contract, parent_schema)
        properties = parent_schema.get("properties")
        if not isinstance(properties, dict):
            raise ResourceStoreError(
                "resource uri_path must point into an object schema with properties."
            )
        uri_field = rule.uri_path.segments[-1].value
        if not isinstance(uri_field, str) or uri_field not in properties:
            raise ResourceStoreError("resource uri_path is absent from output.schema.")
        del properties[uri_field]
        properties["resource_id"] = {
            "type": "string",
            "description": (
                "Framework-registered resource identifier. Copy exactly; never create a URL."
            ),
        }
        required = parent_schema.get("required")
        if isinstance(required, list):
            parent_schema["required"] = [
                "resource_id" if value == uri_field else value for value in required
            ]
            if uri_field not in required and "resource_id" not in parent_schema["required"]:
                parent_schema["required"].append("resource_id")
        transformations.append({
            "items_path": rule.items_path.source,
            "removed_uri_path": rule.uri_path.source,
            "replacement_field": "resource_id",
        })
    if transformations:
        contract["x-lumi-normalized-resources"] = transformations
        existing = contract.get("description")
        prefix = f"{existing} " if isinstance(existing, str) and existing.strip() else ""
        contract["description"] = (
            prefix
            + "External resource URI fields were removed and replaced by trusted resource_id values."
        )
    return contract


def _schema_at_instance_path(
    root_schema: dict[str, Any],
    start_schema: dict[str, Any],
    path: CompiledJsonPath,
) -> dict[str, Any]:
    current = start_schema
    for segment in path.segments:
        current = _resolve_schema_ref(root_schema, current)
        if segment.kind == "field":
            properties = current.get("properties")
            if not isinstance(properties, dict) or segment.value not in properties:
                raise ResourceStoreError(
                    f"resource path '{path.source}' is absent from output.schema."
                )
            child = properties[segment.value]
        elif segment.kind in {"index", "wildcard"}:
            child = current.get("items")
        else:
            child = None
        if not isinstance(child, dict):
            raise ResourceStoreError(
                f"resource path '{path.source}' cannot be represented by output.schema."
            )
        current = child
    return _resolve_schema_ref(root_schema, current)


def _resolve_schema_ref(
    root_schema: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
        raise ResourceStoreError("resource schema uses an unsupported reference.")
    current: Any = root_schema
    for token in reference[2:].split("/"):
        if not isinstance(current, dict) or token not in current:
            raise ResourceStoreError("resource schema reference cannot be resolved.")
        current = current[token]
    if not isinstance(current, dict):
        raise ResourceStoreError("resource schema reference must resolve to an object.")
    return current


def _json_copy(value: Any) -> Any:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _json_copy(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(child) for child in value]
    return value


def _batch_key(session_id: str, owner_task_id: str, attempt: int) -> tuple[str, str, int]:
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        raise ResourceStoreError("attempt must be a non-negative integer.")
    return (
        _text(session_id, "session_id"),
        _text(owner_task_id, "owner_task_id"),
        attempt,
    )


def _mime_type(value: object) -> str:
    normalized = _text(value, "mime_type").lower()
    if "/" not in normalized or any(character.isspace() for character in normalized):
        raise ResourceStoreError("mime_type must use type/subtype syntax.")
    return normalized


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResourceStoreError(f"{field} must be a non-empty string.")
    return value.strip()


__all__ = [
    "InMemoryResourceStore",
    "ResourceBatchLifecycle",
    "ResourceMediaReference",
    "ResourceMediaReferenceAdapter",
    "ResourceReference",
    "ResourceStore",
    "ResourceStoreError",
    "resource_kind",
    "normalized_resource_output_contract",
    "validate_external_resource_uri",
]
