"""Draft presentation adapter using the copied renderer contracts."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from final_framework.catalogs.assets import AssetCatalog
from final_framework.panel import render_visual_stage_map, surface_document_client_payload
from final_framework.widgets import WidgetRegistry

from .contracts import CompiledSurface, SurfaceDraft


DraftPresentationSink = Callable[["DraftPresentation"], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class DraftPresentation:
    """One browser-safe draft payload and its Gemini-facing ASCII map."""

    draft: SurfaceDraft
    compiled: CompiledSurface
    browser_payload: Mapping[str, Any]
    ascii_map: str


class DraftPresenter:
    """Materialize only presentation outputs; it owns no Task Manager state."""

    def __init__(
        self,
        *,
        widget_registry: WidgetRegistry,
        asset_catalog: AssetCatalog,
        asset_urls: Mapping[str, str] | None = None,
        sink: DraftPresentationSink | None = None,
    ) -> None:
        self._widget_registry = widget_registry
        self._asset_catalog = asset_catalog
        self._asset_urls = dict(asset_urls or {})
        self._sink = sink

    async def __call__(self, draft: SurfaceDraft, compiled: CompiledSurface) -> None:
        result = self.build(draft, compiled)
        if self._sink is not None:
            delivered = self._sink(result)
            if inspect.isawaitable(delivered):
                await delivered

    def build(self, draft: SurfaceDraft, compiled: CompiledSurface) -> DraftPresentation:
        return DraftPresentation(
            draft=draft,
            compiled=compiled,
            browser_payload=surface_document_client_payload(
                compiled, asset_urls=self._asset_urls
            ),
            ascii_map=render_visual_stage_map(
                compiled,
                widget_registry=self._widget_registry,
                asset_catalog=self._asset_catalog,
            ),
        )
