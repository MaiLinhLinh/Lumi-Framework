"""Strict runtime materialization for an assembled OpenUI surface."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lumi_openui.catalogs.assets import AssetCatalog, AssetCatalogError
from lumi_openui.panel.contracts import AnchorBinding, ComponentChild, ComponentNode, PlanBlock
from lumi_openui.widgets import WidgetPropsError, WidgetRegistry

from .assembly import CompleteSurface
from .contracts import AnchorManifest, CompiledSurface
from .draft_compiler import CANVAS_COLUMNS, CANVAS_ROWS, _overlap


logger = logging.getLogger("lumi.final_compiler")


class FinalCompilationError(ValueError):
    """An assembled surface cannot become safe runtime state."""


@dataclass(frozen=True, slots=True)
class FinalSurfaceCompiler:
    """Validate all final props and resolve registered internal media only."""

    widget_registry: WidgetRegistry
    asset_catalog: AssetCatalog | None = None
    media_reference_store: Any | None = None
    canvas_columns: int = CANVAS_COLUMNS
    canvas_rows: int = CANVAS_ROWS

    def __post_init__(self) -> None:
        if not isinstance(self.widget_registry, WidgetRegistry):
            raise FinalCompilationError("widget_registry must be a WidgetRegistry.")
        if self.asset_catalog is not None and not isinstance(self.asset_catalog, AssetCatalog):
            raise FinalCompilationError("asset_catalog must be an AssetCatalog when provided.")
        if self.media_reference_store is not None and not callable(
            getattr(self.media_reference_store, "get", None)
        ):
            raise FinalCompilationError("media_reference_store must provide get().")
        for name in ("canvas_columns", "canvas_rows"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise FinalCompilationError(f"{name} must be a positive integer.")

    def compile(
        self,
        complete: CompleteSurface,
        *,
        surface_id: str,
        media_session_id: str,
        revision: int = 2,
        anchor_manifest: AnchorManifest | None = None,
    ) -> CompiledSurface:
        if not isinstance(complete, CompleteSurface):
            raise FinalCompilationError("complete must be a CompleteSurface.")
        if not isinstance(surface_id, str) or not surface_id.strip():
            raise FinalCompilationError("surface_id must be a non-empty string.")
        if not isinstance(media_session_id, str) or not media_session_id.strip():
            raise FinalCompilationError("media_session_id must be a non-empty string.")
        if anchor_manifest is not None:
            if not isinstance(anchor_manifest, AnchorManifest):
                raise FinalCompilationError("anchor_manifest must be an AnchorManifest.")
            if anchor_manifest.surface_id != surface_id.strip():
                raise FinalCompilationError("anchor manifest surface_id does not match final surface_id.")
            if len(anchor_manifest.component_types) != len(complete.draft.blocks):
                raise FinalCompilationError('Anchor manifest component count does not match surface.')
            final_component_types = tuple(
                (anchor_manifest.component_types[index-1][0], block.widget_id)
                for index, block in enumerate(complete.draft.blocks, start=1)
            )
            if anchor_manifest.component_types != final_component_types:
                raise FinalCompilationError(
                    "final surface component identity does not match the Draft anchor manifest."
                )
        logger.info(
            "[FINAL_COMPILER:START] surface=%s revision=%s blocks=%s",
            surface_id,
            revision,
            len(complete.draft.blocks),
        )
        self._validate_grid(complete.draft.blocks)
        components: list[ComponentNode] = []
        anchors: list[AnchorBinding] = []
        next_anchor = 1
        for index, block in enumerate(complete.draft.blocks, start=1):
            # A component whose semantic description requires unavailable data
            # remains a generic placeholder. This no longer depends on a
            # Plan slot binding: direct content stays in the Draft, while
            # the Browser waits to render the whole incomplete component.
            partial_pending = False
            try:
                widget = self.widget_registry.get(block.widget_id)
                try:
                    props = widget.validate(block.props)
                    self._validate_asset_references(widget, props)
                    children = self._children(block, media_session_id)
                except (WidgetPropsError, AssetCatalogError, FinalCompilationError):
                    if not block.description:
                        raise
                    partial_pending = True
                    props = {}
                    children = self._children_incomplete(block)
                state = widget.materialize_initial_state({
                    **dict(block.initial_state), "visibility": block.initial_visibility,
                }, props=block.props)
            except (WidgetPropsError, AssetCatalogError) as exc:
                raise FinalCompilationError(f"block {index} ({block.widget_id}): {exc}") from exc
            component_id = anchor_manifest.component_types[index-1][0] if anchor_manifest else str(index)
            component = ComponentNode(
                id=component_id,
                type=block.widget_id,
                layout=block.grid,
                props=(self._materialize_media(props, media_session_id) if not partial_pending else props),
                state=state,
                children=children,
                draft_placeholder=(block.description if partial_pending else None),
            )
            if partial_pending:
                logger.warning(
                    "[FINAL_COMPILER:PLACEHOLDER] surface=%s block=%s widget=%s description=%r",
                    surface_id,
                    index,
                    block.widget_id,
                    block.description,
                )
            components.append(component)
            try:
                expected_widget_anchors = (
                    widget.anchors_for_draft(block.props)
                    if partial_pending
                    else widget.anchors_for(props)
                )
            except WidgetPropsError as exc:
                raise FinalCompilationError(
                    f"block {index} ({block.widget_id}) cannot preserve Draft anchors: {exc}"
                ) from exc
            if anchor_manifest is not None:
                reserved = anchor_manifest.anchors_for_component(component_id)
                expected_keys = tuple(anchor.key for anchor in expected_widget_anchors)
                reserved_keys = tuple(anchor.anchor_key for anchor in reserved)
                if reserved_keys != expected_keys:
                    raise FinalCompilationError(
                        f"block {index} ({block.widget_id}) changed its anchor structure "
                        f"from {list(reserved_keys)} to {list(expected_keys)}."
                    )
                anchors.extend(reserved)
            else:
                for anchor in (() if partial_pending else expected_widget_anchors):
                    anchors.append(AnchorBinding(
                        anchor_id=f"a{next_anchor}", component_id=component_id, anchor_key=anchor.key
                    ))
                    next_anchor += 1
        compiled = CompiledSurface(
            surface_id=surface_id.strip(),
            revision=revision,
            components=tuple(components),
            anchors=tuple(anchors),
        )
        logger.info(
            "[FINAL_COMPILER:READY] surface=%s revision=%s components=%s anchors=%s",
            compiled.surface_id,
            compiled.revision,
            len(compiled.components),
            len(compiled.anchors),
        )
        return compiled

    def _children(self, block: PlanBlock, media_session_id: str) -> tuple[ComponentChild, ...]:
        if not block.children:
            if block.widget_id == "choice":
                raise FinalCompilationError("choice block must contain at least one child.")
            return ()
        parent = self.widget_registry.get(block.widget_id)
        child_ids = parent.validate_child_widget_ids(tuple(child.widget_id for child in block.children))
        children: list[ComponentChild] = []
        for child, child_id in zip(block.children, child_ids, strict=True):
            child_widget = self.widget_registry.get(child_id)
            props = child_widget.validate(child.props)
            self._validate_asset_references(child_widget, props)
            children.append(ComponentChild(
                type=child_id,
                props=self._materialize_media(props, media_session_id),
            ))
        return tuple(children)

    def _children_incomplete(self, block: PlanBlock) -> tuple[ComponentChild, ...]:
        """Retain the structural child tree for an incomplete component."""

        if not block.children:
            if block.widget_id == "choice":
                raise FinalCompilationError("choice block must contain at least one child.")
            return ()
        parent = self.widget_registry.get(block.widget_id)
        child_ids = parent.validate_child_widget_ids(
            tuple(child.widget_id for child in block.children)
        )
        return tuple(
            ComponentChild(type=child_id, props=dict(child.props))
            for child, child_id in zip(block.children, child_ids, strict=True)
        )

    def _validate_asset_references(self, widget: Any, props: Mapping[str, Any]) -> None:
        for reference in widget.asset_references:
            asset_id = _path(props, reference.path)
            if asset_id is None and not reference.required:
                continue
            if not isinstance(asset_id, str) or not asset_id:
                raise FinalCompilationError(
                    f"asset reference '{reference.path}' for '{widget.widget_id}' must be a string."
                )
            if self.asset_catalog is None:
                raise FinalCompilationError("asset catalog is required for a static asset reference.")
            asset = self.asset_catalog.get(asset_id)
            if asset.kind not in reference.allowed_kinds:
                raise FinalCompilationError(
                    f"asset_id '{asset_id}' has incompatible kind for '{widget.widget_id}'."
                )

    def _materialize_media(self, value: Any, media_session_id: str) -> Any:
        if isinstance(value, Mapping):
            resolved = {key: self._materialize_media(item, media_session_id) for key, item in value.items()}
            media_id = resolved.get("media_id")
            if media_id is None:
                return resolved
            if not isinstance(media_id, str) or not media_id:
                raise FinalCompilationError("media_id must be a non-empty string.")
            if self.media_reference_store is None:
                raise FinalCompilationError("media_id is unavailable; no MediaReferenceStore is configured.")
            try:
                reference = self.media_reference_store.get(
                    session_id=media_session_id,
                    media_id=media_id,
                )
            except Exception as exc:
                raise FinalCompilationError(str(exc)) from exc
            if reference.local_file_missing:
                raise FinalCompilationError("selected media file is missing locally.")
            resolved["source"] = {
                "url": reference.url,
                "caption": reference.caption,
                "source_url": reference.source_uri,
                "mime_type": reference.mime_type,
            }
            return resolved
        if isinstance(value, list):
            return [self._materialize_media(item, media_session_id) for item in value]
        if isinstance(value, tuple):
            return tuple(self._materialize_media(item, media_session_id) for item in value)
        return value

    def _validate_grid(self, blocks: tuple[PlanBlock, ...]) -> None:
        for index, block in enumerate(blocks, start=1):
            if block.grid.col + block.grid.col_span - 1 > self.canvas_columns:
                raise FinalCompilationError(f"block {index} exceeds canvas column boundary.")
            if block.grid.row + block.grid.row_span - 1 > self.canvas_rows:
                raise FinalCompilationError(f"block {index} exceeds canvas row boundary.")
        for index, left in enumerate(blocks):
            for right_index, right in enumerate(blocks[index + 1 :], start=index + 2):
                if _overlap(left, right):
                    raise FinalCompilationError(f"blocks {index + 1} and {right_index} overlap.")


def _path(value: object, path: str) -> object | None:
    current = value
    parts = path.split(".")
    if parts and parts[0] == "props":
        parts = parts[1:]
    for part in parts:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current
