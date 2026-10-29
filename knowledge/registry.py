"""Load independent knowledge packages and dispatch text/media searches."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


_PACKAGE_ID = re.compile(r"^[a-z]+(?:_[a-z]+)*$")
_CONTENT_TYPES = frozenset({"text", "image"})
_MANIFEST_FIELDS = {
    "package_id",
    "display_name",
    "description",
    "languages",
    "content_types",
    "ingestion_provider",
    "retrieval_provider",
    "schemas",
    "storage",
}
_CANDIDATE_FIELDS = {
    "candidate_id",
    "package_id",
    "similarity_score",
    "provenance",
    "payload",
}
_REQUEST_FIELDS = {"query", "package_ids"}


class KnowledgeManifestError(ValueError):
    """A package manifest or provider does not meet the public contract."""


class KnowledgeRegistryError(ValueError):
    """A registry request is invalid or cannot be dispatched."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeManifestError(f"{field} must be a non-empty string.")
    return value.strip()


def _package_id(value: object, field: str = "package_id") -> str:
    safe_id = _text(value, field)
    if not _PACKAGE_ID.fullmatch(safe_id):
        raise KnowledgeManifestError(f"{field} must match ^[a-z]+(?:_[a-z]+)*$.")
    return safe_id


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise KnowledgeManifestError(f"{field} must be a non-empty array of strings.")
    normalized = tuple(_text(item, f"{field}[]") for item in value)
    if len(normalized) != len(set(normalized)):
        raise KnowledgeManifestError(f"{field} must not contain duplicates.")
    return normalized


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise KnowledgeManifestError(f"{field} must be an object.")
    if not all(isinstance(key, str) for key in value):
        raise KnowledgeManifestError(f"{field} keys must be strings.")
    return dict(value)


def _safe_file(value: object, *, field: str, package_root: Path) -> Path:
    relative = Path(_text(value, field))
    if relative.is_absolute() or ".." in relative.parts:
        raise KnowledgeManifestError(f"{field} must stay inside its knowledge package.")
    path = (package_root / relative).resolve()
    try:
        path.relative_to(package_root)
    except ValueError as exc:
        raise KnowledgeManifestError(f"{field} resolves outside its knowledge package.") from exc
    if not path.is_file():
        raise KnowledgeManifestError(f"{field} does not name an existing file.")
    return path


@dataclass(frozen=True, slots=True)
class KnowledgePackageManifest:
    package_id: str
    display_name: str
    description: str
    languages: tuple[str, ...]
    content_types: tuple[str, ...]
    ingestion_provider_path: Path
    retrieval_provider_path: Path
    schema_paths: Mapping[str, Path]
    storage: Mapping[str, str]

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "description": self.description,
            "languages": list(self.languages),
            "content_types": list(self.content_types),
        }


@dataclass(frozen=True, slots=True)
class KnowledgeProviderRuntimeContext:
    """Framework dependencies injected once when a package is loaded."""

    package_root: Path
    manifest: KnowledgePackageManifest
    knowledge_store: object | None = None
    media_store: object | None = None


ProviderSearch = Callable[[Mapping[str, Any]], list[Mapping[str, Any]]]


@dataclass(frozen=True, slots=True)
class LoadedKnowledgePackage:
    root: Path
    manifest: KnowledgePackageManifest
    search_text: ProviderSearch | None
    search_media: ProviderSearch | None


class KnowledgeRegistry:
    """One registry for every package and both retrieval operations."""

    def __init__(
        self,
        packages_root: Path,
        *,
        knowledge_store: object | None = None,
        media_store: object | None = None,
    ) -> None:
        self._packages_root = packages_root.resolve()
        self._knowledge_store = knowledge_store
        self._media_store = media_store
        self._packages: dict[str, LoadedKnowledgePackage] = {}

    @property
    def packages_root(self) -> Path:
        return self._packages_root

    def load_all(self) -> tuple[LoadedKnowledgePackage, ...]:
        if not self._packages_root.is_dir():
            return ()
        loaded: list[LoadedKnowledgePackage] = []
        for package_root in sorted(self._packages_root.iterdir(), key=lambda path: path.name):
            if package_root.is_dir() and (package_root / "manifest.json").is_file():
                package = self._load_package(package_root)
                if package.manifest.package_id != package_root.name:
                    raise KnowledgeManifestError(
                        "manifest.package_id must match its package directory."
                    )
                self.register(package)
                loaded.append(package)
        return tuple(loaded)

    def register(self, package: LoadedKnowledgePackage) -> None:
        if not isinstance(package, LoadedKnowledgePackage):
            raise KnowledgeRegistryError("package must be a LoadedKnowledgePackage.")
        package_id = package.manifest.package_id
        if package_id in self._packages:
            raise KnowledgeRegistryError(
                f"knowledge package '{package_id}' is already registered."
            )
        self._packages[package_id] = package

    def list_packages(self) -> tuple[LoadedKnowledgePackage, ...]:
        return tuple(self._packages.values())

    def package_catalog(self) -> list[dict[str, Any]]:
        entries = [package.manifest.catalog_entry() for package in self._packages.values()]
        inventory = getattr(self._knowledge_store, "catalog_inventory", None)
        if callable(inventory):
            for entry in entries:
                entry["description"] += "\n" + inventory(entry["package_id"])
        return entries

    def search_text(self, request: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        return self._search(request, operation="text")

    def search_media(self, request: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        return self._search(request, operation="image")

    async def search_text_parallel(
        self, request: Mapping[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        return await self._search_parallel(request, operation="text")

    async def search_media_parallel(
        self, request: Mapping[str, Any]
    ) -> dict[str, list[dict[str, Any]]]:
        return await self._search_parallel(request, operation="image")

    def _search(
        self,
        request: Mapping[str, Any],
        *,
        operation: str,
    ) -> dict[str, list[dict[str, Any]]]:
        normalized = self._request(request, operation=operation)
        candidates: list[dict[str, Any]] = []
        package_errors: list[dict[str, str]] = []
        for package_id in normalized["package_ids"]:
            package = self._packages[package_id]
            provider = self._provider(package, operation)
            try:
                provider_candidates = provider(
                    {"query": normalized["query"], "package_ids": [package_id]}
                )
                candidates.extend(
                    self._provider_candidates(package_id, provider_candidates)
                )
            except Exception as exc:
                package_errors.append(
                    {"package_id": package_id, "message": str(exc) or exc.__class__.__name__}
                )
        return {"candidates": candidates, "package_errors": package_errors}

    async def _search_parallel(
        self,
        request: Mapping[str, Any],
        *,
        operation: str,
    ) -> dict[str, list[dict[str, Any]]]:
        normalized = self._request(request, operation=operation)

        async def run_package(
            package_id: str,
        ) -> tuple[str, list[dict[str, Any]] | None, str | None]:
            package = self._packages[package_id]
            provider = self._provider(package, operation)
            try:
                raw = await asyncio.to_thread(
                    provider,
                    {"query": normalized["query"], "package_ids": [package_id]},
                )
                return package_id, self._provider_candidates(package_id, raw), None
            except Exception as exc:
                return package_id, None, str(exc) or exc.__class__.__name__

        settled = await asyncio.gather(
            *(run_package(package_id) for package_id in normalized["package_ids"])
        )
        candidates: list[dict[str, Any]] = []
        package_errors: list[dict[str, str]] = []
        for package_id, provider_candidates, error in settled:
            if error is not None:
                package_errors.append({"package_id": package_id, "message": error})
            elif provider_candidates is not None:
                candidates.extend(provider_candidates)
        return {"candidates": candidates, "package_errors": package_errors}

    def _request(self, request: Mapping[str, Any], *, operation: str) -> dict[str, Any]:
        if operation not in _CONTENT_TYPES:
            raise KnowledgeRegistryError(f"unsupported knowledge operation '{operation}'.")
        if not isinstance(request, Mapping):
            raise KnowledgeRegistryError("search request must be an object.")
        data = dict(request)
        unsupported = set(data) - _REQUEST_FIELDS
        if unsupported:
            raise KnowledgeRegistryError(
                f"search request has unsupported fields: {sorted(unsupported)}."
            )
        if "query" not in data or "package_ids" not in data:
            raise KnowledgeRegistryError("search request requires query and package_ids.")
        query = data["query"]
        if not isinstance(query, str) or not query.strip():
            raise KnowledgeRegistryError("search request.query must be a non-empty string.")
        raw_ids = data["package_ids"]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise KnowledgeRegistryError(
                "search request.package_ids must be a non-empty array."
            )
        package_ids: list[str] = []
        for raw_id in raw_ids:
            try:
                package_id = _package_id(raw_id, "search request.package_ids[]")
            except KnowledgeManifestError as exc:
                raise KnowledgeRegistryError(str(exc)) from exc
            package = self._packages.get(package_id)
            if package is None:
                raise KnowledgeRegistryError(f"unknown knowledge package '{package_id}'.")
            if operation not in package.manifest.content_types:
                raise KnowledgeRegistryError(
                    f"knowledge package '{package_id}' does not support {operation} search."
                )
            package_ids.append(package_id)
        if len(package_ids) != len(set(package_ids)):
            raise KnowledgeRegistryError(
                "search request.package_ids must not contain duplicates."
            )
        return {"query": query.strip(), "package_ids": package_ids}

    @staticmethod
    def _provider(package: LoadedKnowledgePackage, operation: str) -> ProviderSearch:
        provider = package.search_text if operation == "text" else package.search_media
        if provider is None:
            raise KnowledgeRegistryError(
                f"knowledge package '{package.manifest.package_id}' has no {operation} provider."
            )
        return provider

    def _load_package(self, package_root: Path) -> LoadedKnowledgePackage:
        manifest_path = package_root / "manifest.json"
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeManifestError(
                f"cannot load knowledge manifest '{manifest_path}': {exc}"
            ) from exc
        data = _mapping(raw, "knowledge manifest root")
        missing = _MANIFEST_FIELDS - set(data)
        unsupported = set(data) - _MANIFEST_FIELDS
        if missing or unsupported:
            raise KnowledgeManifestError(
                "knowledge manifest is missing "
                f"{sorted(missing)} or has unsupported fields {sorted(unsupported)}."
            )
        content_types = _strings(data["content_types"], "content_types")
        unknown_types = set(content_types) - _CONTENT_TYPES
        if unknown_types:
            raise KnowledgeManifestError(
                f"content_types has unsupported values {sorted(unknown_types)}."
            )
        schema_data = _mapping(data["schemas"], "manifest.schemas")
        required_schemas = {
            "text" if content_type == "text" else "media"
            for content_type in content_types
        }
        missing_schemas = required_schemas - set(schema_data)
        if missing_schemas:
            raise KnowledgeManifestError(
                f"manifest.schemas is missing {sorted(missing_schemas)}."
            )
        storage_data = _mapping(data["storage"], "manifest.storage")
        if not storage_data:
            raise KnowledgeManifestError("manifest.storage must not be empty.")
        manifest = KnowledgePackageManifest(
            package_id=_package_id(data["package_id"]),
            display_name=_text(data["display_name"], "display_name"),
            description=_text(data["description"], "description"),
            languages=_strings(data["languages"], "languages"),
            content_types=content_types,
            ingestion_provider_path=_safe_file(
                data["ingestion_provider"],
                field="ingestion_provider",
                package_root=package_root,
            ),
            retrieval_provider_path=_safe_file(
                data["retrieval_provider"],
                field="retrieval_provider",
                package_root=package_root,
            ),
            schema_paths=MappingProxyType(
                {
                    _text(name, "manifest.schemas key"): _safe_file(
                        value,
                        field=f"manifest.schemas.{name}",
                        package_root=package_root,
                    )
                    for name, value in schema_data.items()
                }
            ),
            storage=MappingProxyType(
                {
                    _text(name, "manifest.storage key"): _text(
                        value, f"manifest.storage.{name}"
                    )
                    for name, value in storage_data.items()
                }
            ),
        )
        return self._load_provider(package_root, manifest)

    def _load_provider(
        self,
        package_root: Path,
        manifest: KnowledgePackageManifest,
    ) -> LoadedKnowledgePackage:
        module_name = f"lumi_openui_knowledge_provider_{manifest.package_id}"
        spec = importlib.util.spec_from_file_location(
            module_name, manifest.retrieval_provider_path
        )
        if spec is None or spec.loader is None:
            raise KnowledgeManifestError(
                f"cannot load retrieval provider for '{manifest.package_id}'."
            )
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise KnowledgeManifestError(
                f"cannot execute retrieval provider for '{manifest.package_id}': {exc}"
            ) from exc
        create_provider = getattr(module, "create_provider", None)
        if not callable(create_provider):
            raise KnowledgeManifestError(
                f"retrieval provider for '{manifest.package_id}' must export "
                "create_provider(runtime_context)."
            )
        context = KnowledgeProviderRuntimeContext(
            package_root=package_root,
            manifest=manifest,
            knowledge_store=self._knowledge_store,
            media_store=self._media_store,
        )
        try:
            provider = create_provider(context)
        except Exception as exc:
            raise KnowledgeManifestError(
                f"cannot create retrieval provider for '{manifest.package_id}': {exc}"
            ) from exc
        search_text = getattr(provider, "search_text", None)
        search_media = getattr(provider, "search_media", None)
        if "text" in manifest.content_types and not callable(search_text):
            raise KnowledgeManifestError(
                f"retrieval provider for '{manifest.package_id}' must provide "
                "search_text(request)."
            )
        if "image" in manifest.content_types and not callable(search_media):
            raise KnowledgeManifestError(
                f"retrieval provider for '{manifest.package_id}' must provide "
                "search_media(request)."
            )
        return LoadedKnowledgePackage(
            root=package_root,
            manifest=manifest,
            search_text=search_text if callable(search_text) else None,
            search_media=search_media if callable(search_media) else None,
        )

    @staticmethod
    def _provider_candidates(package_id: str, raw: object) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            raise KnowledgeRegistryError(
                "provider search method must return a list of candidates."
            )
        if len(raw) > 10:
            raise KnowledgeRegistryError(
                "provider search method must return at most 10 candidates."
            )
        candidates: list[dict[str, Any]] = []
        previous_score: float | None = None
        for index, value in enumerate(raw):
            if not isinstance(value, Mapping):
                raise KnowledgeRegistryError(f"provider candidate {index} must be an object.")
            candidate = dict(value)
            missing = _CANDIDATE_FIELDS - set(candidate)
            if missing:
                raise KnowledgeRegistryError(
                    f"provider candidate {index} is missing fields {sorted(missing)}."
                )
            if candidate["package_id"] != package_id:
                raise KnowledgeRegistryError(
                    "provider candidate.package_id must match the searched package."
                )
            score = candidate["similarity_score"]
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise KnowledgeRegistryError(
                    "provider candidate.similarity_score must be a finite number."
                )
            if previous_score is not None and score > previous_score:
                raise KnowledgeRegistryError(
                    "provider candidates must be ordered by similarity_score descending."
                )
            if not isinstance(candidate["provenance"], Mapping):
                raise KnowledgeRegistryError(
                    "provider candidate.provenance must be an object."
                )
            if not isinstance(candidate["payload"], Mapping):
                raise KnowledgeRegistryError(
                    "provider candidate.payload must be an object."
                )
            previous_score = float(score)
            candidates.append(candidate)
        return candidates[:5]


__all__ = [
    "KnowledgeManifestError",
    "KnowledgePackageManifest",
    "KnowledgeProviderRuntimeContext",
    "KnowledgeRegistry",
    "KnowledgeRegistryError",
    "LoadedKnowledgePackage",
    "ProviderSearch",
]
