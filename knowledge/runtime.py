"""Composition helpers for the unified manifest-loaded Knowledge Registry."""

from __future__ import annotations

from pathlib import Path

from .chroma_store import ChromaKnowledgeStore
from .media_store import FilesystemMediaStore
from .registry import KnowledgeRegistry


DEFAULT_KNOWLEDGE_PACKAGES_ROOT = (
    Path(__file__).resolve().parent.parent / "knowledge_packages"
)


def build_knowledge_registry(
    store: ChromaKnowledgeStore,
    *,
    media_store: FilesystemMediaStore,
    packages_root: Path = DEFAULT_KNOWLEDGE_PACKAGES_ROOT,
) -> KnowledgeRegistry:
    """Discover all packages and bind them to framework-owned storage clients."""

    if not isinstance(store, ChromaKnowledgeStore):
        raise TypeError("store must be a ChromaKnowledgeStore.")
    if not isinstance(media_store, FilesystemMediaStore):
        raise TypeError("media_store must be a FilesystemMediaStore.")
    registry = KnowledgeRegistry(
        packages_root,
        knowledge_store=store,
        media_store=media_store,
    )
    registry.load_all()
    return registry


__all__ = ["DEFAULT_KNOWLEDGE_PACKAGES_ROOT", "build_knowledge_registry"]
