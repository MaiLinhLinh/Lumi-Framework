"""Authorized Python boundary for all local knowledge retrieval."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .access import KnowledgeAccessContext, KnowledgeGrantRegistry
from .registry import KnowledgeRegistry
from .tools import (
    SEARCH_LOCAL_MEDIA,
    SEARCH_LOCAL_TEXT,
    validate_knowledge_tool_arguments,
)


class KnowledgeGateway:
    """Validate, authorize and dispatch one isolated knowledge operation."""

    def __init__(
        self,
        registry: KnowledgeRegistry,
        grant_registry: KnowledgeGrantRegistry,
    ) -> None:
        if not isinstance(registry, KnowledgeRegistry):
            raise TypeError("registry must be a KnowledgeRegistry.")
        if not isinstance(grant_registry, KnowledgeGrantRegistry):
            raise TypeError("grant_registry must be a KnowledgeGrantRegistry.")
        self._registry = registry
        self._grant_registry = grant_registry

    @property
    def registry(self) -> KnowledgeRegistry:
        return self._registry

    @property
    def grant_registry(self) -> KnowledgeGrantRegistry:
        return self._grant_registry

    def issue_access_context(
        self,
        *,
        run_id: str,
        task_id: str,
        allowed_tools: Iterable[str],
        ttl_seconds: float | None = None,
    ) -> KnowledgeAccessContext:
        """Issue a grant using an immutable snapshot of the current catalog."""

        package_ids = tuple(
            entry["package_id"] for entry in self._registry.package_catalog()
        )
        return self._grant_registry.issue(
            run_id=run_id,
            task_id=task_id,
            allowed_tools=allowed_tools,
            allowed_package_ids=package_ids,
            ttl_seconds=ttl_seconds,
        )

    async def search_text(
        self,
        arguments: Mapping[str, Any],
        access_context: KnowledgeAccessContext,
    ) -> dict[str, list[dict[str, Any]]]:
        return await self._dispatch(
            SEARCH_LOCAL_TEXT,
            arguments,
            access_context,
        )

    async def search_media(
        self,
        arguments: Mapping[str, Any],
        access_context: KnowledgeAccessContext,
    ) -> dict[str, list[dict[str, Any]]]:
        return await self._dispatch(
            SEARCH_LOCAL_MEDIA,
            arguments,
            access_context,
        )

    async def _dispatch(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        access_context: KnowledgeAccessContext,
    ) -> dict[str, list[dict[str, Any]]]:
        normalized = validate_knowledge_tool_arguments(tool_name, arguments)
        self._grant_registry.authorize(
            access_context,
            tool_name=tool_name,
            package_ids=normalized["package_ids"],
        )
        if tool_name == SEARCH_LOCAL_TEXT:
            return await self._registry.search_text_parallel(normalized)
        return await self._registry.search_media_parallel(normalized)


__all__ = ["KnowledgeGateway"]
