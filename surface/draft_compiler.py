"""Compile a domainless Surface Draft into renderer-ready placeholder state."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any, Mapping
from uuid import uuid4

from lumi_openui.catalogs.assets import AssetCatalog, AssetCatalogError
from lumi_openui.panel.contracts import AnchorBinding, ComponentChild, ComponentNode, PlanBlock
from lumi_openui.widgets import WidgetPropsError, WidgetRegistry

from .contracts import CompiledSurface, SurfaceDraft


CANVAS_COLUMNS = 16
CANVAS_ROWS = 10


class DraftCompilationError(ValueError):
    """A Surface Draft cannot become a safe placeholder panel."""

    def __init__(self, issues: str | Iterable[str]) -> None:
        normalized = (issues,) if isinstance(issues, str) else tuple(issues)
        if not normalized or not all(isinstance(issue, str) and issue for issue in normalized):
            raise ValueError("DraftCompilationError requires at least one non-empty issue.")
        self.issues = normalized
        super().__init__("; ".join(normalized))


@dataclass(frozen=True, slots=True)
class DraftSurfaceCompiler:
    """Validate widget structure without domain resources or factual data.

    Drafts may contain partial props for a component waiting for available data.
    A partial component is identified by failure of its widget-prop validation
    plus a semantic ``description``; the whole component becomes the existing
    generic placeholder. A component with valid props renders normally even
    when it has a description.
    """

    widget_registry: WidgetRegistry
    asset_catalog: AssetCatalog | None = None
    canvas_columns: int = CANVAS_COLUMNS
    canvas_rows: int = CANVAS_ROWS

    def __post_init__(self) -> None:
        if not isinstance(self.widget_registry, WidgetRegistry):
            raise DraftCompilationError("widget_registry must be a WidgetRegistry.")
        if self.asset_catalog is not None and not isinstance(self.asset_catalog, AssetCatalog):
            raise DraftCompilationError("asset_catalog must be an AssetCatalog when provided.")
        for name in ("canvas_columns", "canvas_rows"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DraftCompilationError(f"{name} must be a positive integer.")

    def compile(self, draft: SurfaceDraft, *, surface_id: str | None = None) -> CompiledSurface:
        if not isinstance(draft, SurfaceDraft):
            raise DraftCompilationError("draft must be a SurfaceDraft.")
        self._validate_grid(draft.blocks)
        components: list[ComponentNode] = []
        anchors: list[AnchorBinding] = []
        next_anchor = 1
        for index, block in enumerate(draft.blocks, start=1):
            try:
                widget = self.widget_registry.get(block.widget_id)
                state = self._initial_state(block, widget)
                try:
                    props = widget.validate(block.props)
                    children = self._children(block, incomplete=False)
                except WidgetPropsError:
                    if block.description is None:
                        raise
                    props = {}
                    children = self._children(block, incomplete=True)
                    placeholder = self._placeholder(block)
                    widget_anchors = widget.anchors_for_draft(block.props)
                else:
                    self._validate_asset_references(widget, props)
                    placeholder = None
                    widget_anchors = widget.anchors_for(props)
            except (WidgetPropsError, AssetCatalogError) as exc:
                raise DraftCompilationError(f"block {index} ({block.widget_id}): {exc}") from exc
            component_id = str(index)
            component = ComponentNode(
                id=component_id,
                type=block.widget_id,
                layout=block.grid,
                props=props,
                state=state,
                children=children,
                draft_placeholder=placeholder,
            )
            components.append(component)
            for anchor in widget_anchors:
                anchors.append(AnchorBinding(
                    anchor_id=f"a{next_anchor}", component_id=component_id, anchor_key=anchor.key
                ))
                next_anchor += 1
        return CompiledSurface(
            surface_id=surface_id or f"panel-{uuid4().hex}",
            revision=1,
            components=tuple(components),
            anchors=tuple(anchors),
        )

    def _children(self, block: PlanBlock, *, incomplete: bool) -> tuple[ComponentChild, ...]:
        if not block.children:
            if block.widget_id == "choice":
                raise DraftCompilationError("choice block must contain at least one child.")
            return ()
        parent = self.widget_registry.get(block.widget_id)
        child_ids = parent.validate_child_widget_ids(tuple(child.widget_id for child in block.children))
        result: list[ComponentChild] = []
        for child, child_id in zip(block.children, child_ids, strict=True):
            child_widget = self.widget_registry.get(child_id)
            if incomplete:
                props = dict(child.props)
            else:
                props = child_widget.validate(child.props)
                self._validate_asset_references(child_widget, props)
            result.append(ComponentChild(type=child_id, props=props))
        return tuple(result)

    @staticmethod
    def _placeholder(block: PlanBlock) -> str:
        if block.description is None:
            raise DraftCompilationError(
                f"Partial block '{block.widget_id}' requires description for its placeholder."
            )
        return block.description

    @staticmethod
    def _initial_state(block: PlanBlock, widget: Any) -> dict[str, Any]:
        state = dict(block.initial_state)
        state.setdefault("visibility", block.initial_visibility)
        return widget.materialize_initial_state(state, props=block.props)

    def _validate_asset_references(self, widget: Any, props: Mapping[str, Any]) -> None:
        for reference in widget.asset_references:
            asset_id = _path(props, reference.path)
            if asset_id is None and not reference.required:
                continue
            if not isinstance(asset_id, str) or not asset_id:
                raise DraftCompilationError(
                    f"asset reference '{reference.path}' for '{widget.widget_id}' must be a string."
                )
            if self.asset_catalog is None:
                raise DraftCompilationError("asset catalog is required for a static asset reference.")
            asset = self.asset_catalog.get(asset_id)
            if asset.kind not in reference.allowed_kinds:
                raise DraftCompilationError(
                    f"asset_id '{asset_id}' has incompatible kind for '{widget.widget_id}'."
                )

    def _validate_grid(self, blocks: tuple[PlanBlock, ...]) -> None:
        issues: list[str] = []
        for index, block in enumerate(blocks, start=1):
            if block.grid.col + block.grid.col_span - 1 > self.canvas_columns:
                issues.append(f"block {index} exceeds canvas column boundary.")
            if block.grid.row + block.grid.row_span - 1 > self.canvas_rows:
                issues.append(f"block {index} exceeds canvas row boundary.")
        for index, left in enumerate(blocks):
            for right_index, right in enumerate(blocks[index + 1 :], start=index + 2):
                if _overlap(left, right):
                    issues.append(f"blocks {index + 1} and {right_index} overlap.")
        if issues:
            raise DraftCompilationError(issues)


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


def _overlap(left: PlanBlock, right: PlanBlock) -> bool:
    return not (
        left.grid.col + left.grid.col_span <= right.grid.col
        or right.grid.col + right.grid.col_span <= left.grid.col
        or left.grid.row + left.grid.row_span <= right.grid.row
        or right.grid.row + right.grid.row_span <= left.grid.row
    )
