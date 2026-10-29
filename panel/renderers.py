"""Render-neutral exports of domainless compiled-surface values."""

from __future__ import annotations

from collections import Counter
from math import ceil
import re
import textwrap
from typing import Any, Mapping

from lumi_openui.catalogs.assets import AssetCatalog
from lumi_openui.widgets import StageMapPolicy, WidgetRegistry

from .contracts import AnchorBinding, ComponentChild, ComponentNode, GridRect


class StageMapRenderError(ValueError):
    """The text map cannot represent the compiled panel without data loss."""


_DEFAULT_STAGE_MAP_COLUMN_WIDTH = 8


def render_visual_stage_map(
    stage_slots: tuple[Mapping[str, Any], ...],
    *,
    runtime_components: tuple[ComponentNode, ...],
    anchors: tuple[AnchorBinding, ...],
    widget_registry: WidgetRegistry,
    asset_catalog: AssetCatalog,
    media_resources: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Project the evaluated OpenUI Slot tree into the trusted Stage Map.

    OpenUI source/tree owns layout and content. Lumi runtime contributes only
    current state, stable component identities, anchors and verified media.
    """
    if len(stage_slots) != len(runtime_components):
        raise StageMapRenderError("OpenUI Stage tree does not match runtime Slot bindings.")
    components = tuple(
        _stage_component(slot, runtime, media_resources or {})
        for slot, runtime in zip(stage_slots, runtime_components, strict=True)
    )
    anchors_by_component = _anchors_by_component(anchors)
    column_width = _stage_map_column_width(anchors)
    region_content = {
        component.id: _component_region_lines(
            component,
            anchors_by_component.get(component.id, {}),
            component.layout.col_span * column_width,
            widget_registry=widget_registry,
            asset_catalog=asset_catalog,
        )
        for component in components
    }
    _validate_region_anchors(components, anchors, region_content)
    row_height = max(
        4,
        *(
            ceil(len(region_content[component.id]) / component.layout.row_span)
            for component in components
        ),
    )
    canvas = _draw_stage_canvas(
        components,
        region_content,
        column_width=column_width,
        row_height=row_height,
    )

    rows = [
        "VISUAL STAGE MAP — MÀN HÌNH NGƯỜI DÙNG",
        "Bố cục CSS Grid 16 cột × 10 hàng; vị trí tương đối khớp vùng người dùng đang thấy.",
        "Mỗi nhãn anchor nằm ngay dưới vùng hoặc vật thể mà nó minh hoạ.",
        "",
    ]
    rows.extend("".join(line).rstrip() for line in canvas)
    rendered = "\n".join(rows).rstrip()
    _validate_rendered_anchors(anchors, rendered)
    return rendered


def _stage_component(
    slot: Mapping[str, Any],
    runtime: ComponentNode,
    media_resources: Mapping[str, Mapping[str, Any]],
) -> ComponentNode:
    content = slot.get("content")
    if not isinstance(content, Mapping):
        raise StageMapRenderError("OpenUI Stage Slot is missing evaluated content.")
    widget_id = content.get("widget_id")
    if not isinstance(widget_id, str) or widget_id != runtime.type:
        raise StageMapRenderError("OpenUI Stage widget does not match runtime identity.")
    props = _materialize_media(content.get("props", {}), media_resources)
    raw_children = content.get("children", [])
    if not isinstance(raw_children, list):
        raise StageMapRenderError("OpenUI Stage children must be an array.")
    pending = content.get("pending") is True
    description = slot.get("description")
    return ComponentNode(
        id=runtime.id,
        type=widget_id,
        layout=GridRect.from_dict(slot.get("grid")),
        props=props,
        state=runtime.state,
        children=tuple(_stage_child(child, media_resources) for child in raw_children),
        draft_placeholder=(str(description) if pending and isinstance(description, str) and description.strip()
                           else widget_id if pending else None),
    )


def _stage_child(
    value: object, media_resources: Mapping[str, Mapping[str, Any]]
) -> ComponentChild:
    if not isinstance(value, Mapping) or not isinstance(value.get("widget_id"), str):
        raise StageMapRenderError("OpenUI Stage child is invalid.")
    children = value.get("children", [])
    if not isinstance(children, list):
        raise StageMapRenderError("OpenUI Stage child children must be an array.")
    return ComponentChild(
        type=value["widget_id"],
        props=_materialize_media(value.get("props", {}), media_resources),
        children=tuple(_stage_child(child, media_resources) for child in children),
    )


def _materialize_media(
    value: object, media_resources: Mapping[str, Mapping[str, Any]]
) -> Any:
    if isinstance(value, list):
        return [_materialize_media(item, media_resources) for item in value]
    if not isinstance(value, Mapping):
        return value
    result = {str(key): _materialize_media(child, media_resources) for key, child in value.items()}
    media_id = result.get("media_id")
    if isinstance(media_id, str) and media_id in media_resources:
        result["source"] = dict(media_resources[media_id])
    return result


def _stage_map_column_width(anchors: tuple[AnchorBinding, ...]) -> int:
    """Keep every compiler-owned anchor intact inside a one-column region.

    Content continues to wrap according to the component grid span.  Only the
    text resolution of each CSS-grid track grows when the technical anchor
    token would otherwise be wider than the narrowest possible region.
    """

    longest_anchor = max(
        (len(f"[anchor: {anchor.anchor_id}]") for anchor in anchors),
        default=0,
    )
    return max(_DEFAULT_STAGE_MAP_COLUMN_WIDTH, longest_anchor)


def _anchors_by_component(anchors: tuple[AnchorBinding, ...]) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    for anchor in anchors:
        bindings.setdefault(anchor.component_id, {})[anchor.anchor_key] = anchor.anchor_id
    return bindings


def _draw_stage_canvas(
    components: tuple[ComponentNode, ...],
    region_content: Mapping[str, list[str]],
    *,
    column_width: int,
    row_height: int,
) -> list[list[str]]:
    """Place compact visible content by its real GridRect, without borders."""

    canvas_width = 16 * column_width
    canvas = [[" "] * canvas_width for _ in range(10 * row_height)]

    for component in sorted(components, key=lambda item: (item.layout.row, item.layout.col, item.id)):
        x = (component.layout.col - 1) * column_width
        y = (component.layout.row - 1) * row_height
        width = component.layout.col_span * column_width
        height = component.layout.row_span * row_height
        _place_region(
            canvas,
            x=x,
            y=y,
            width=width,
            height=height,
            content=region_content[component.id],
        )
    return canvas


def _place_region(
    canvas: list[list[str]], *, x: int, y: int, width: int, height: int, content: list[str]
) -> None:
    """Centre compact region lines inside their own CSS-grid rectangle."""

    if len(content) > height:
        raise StageMapRenderError(
            f"stage map region requires {len(content)} lines but only {height} are available."
        )
    if any(len(line) > width for line in content):
        raise StageMapRenderError(
            f"stage map region contains a line wider than its {width}-character area."
        )
    start_row = y + max(0, (height - len(content)) // 2)
    for index, line in enumerate(content):
        row = start_row + index
        start_column = x + max(0, (width - len(line)) // 2)
        for offset, char in enumerate(line):
            canvas[row][start_column + offset] = char


def _component_region_lines(
    component: ComponentNode,
    anchors_by_key: Mapping[str, str],
    width: int,
    *,
    widget_registry: WidgetRegistry,
    asset_catalog: AssetCatalog,
) -> list[str]:
    if component.state["visibility"] == "hidden":
        return _wrapped_lines("NỘI DUNG ĐANG ẨN", width) + _anchor_lines(tuple(anchors_by_key.values()), width)
    if component.draft_placeholder is not None:
        return _wrapped_lines(
            f"ĐANG TÌM DỮ LIỆU: {component.draft_placeholder}", width
        ) + _anchor_lines(tuple(anchors_by_key.values()), width)
    root_policy = _stage_map_policy(widget_registry, component.type)
    policy = root_policy.for_state(component.state)
    lines = _policy_lines(
        policy=policy,
        props=widget_registry.get(component.type).runtime_props(component.props, component.state),
        children=component.children,
        width=width,
        widget_registry=widget_registry,
        asset_catalog=asset_catalog,
        anchors_by_key=anchors_by_key,
    )
    # A state view only selects content.  The compiler-owned anchor belongs to
    # the component policy itself, so it remains stable across a card flip.
    anchor_id = anchors_by_key.get(root_policy.anchor_key or "")
    return lines + _anchor_lines((anchor_id,), width)


def _policy_lines(
    *,
    policy: StageMapPolicy,
    props: Mapping[str, Any],
    children: tuple[ComponentChild, ...],
    width: int,
    widget_registry: WidgetRegistry,
    asset_catalog: AssetCatalog,
    anchors_by_key: Mapping[str, str],
) -> list[str]:
    if policy.children_layout is not None:
        child_items: list[list[str]] = []
        for child in children:
            child_policy = _stage_map_policy(widget_registry, child.type)
            child_items.append(_policy_lines(
                policy=child_policy,
                props=child.props,
                children=child.children,
                width=width,
                widget_registry=widget_registry,
                asset_catalog=asset_catalog,
                anchors_by_key={},
            ))
        return _layout_items(child_items, width=width, layout=policy.children_layout)

    asset_caption = _asset_caption(policy, props, asset_catalog)
    count = _resolve_path(props, policy.count_source)
    # count repeats the whole image, whether sourced from an asset or media.
    if (
        asset_caption is not None
        and isinstance(count, int)
        and policy.items_layout is not None
    ):
        return _repeated_asset_policy_lines(policy, asset_caption, count, anchors_by_key, width)
    lines: list[str] = []
    if asset_caption is not None:
        lines.extend(_prefixed_lines(policy.content_label, asset_caption, width))

    text = _resolve_path(props, policy.text_source)
    if text is not None:
        rendered_text = str(text)
        if policy.quote_text:
            rendered_text = f"“{rendered_text}”"
        lines.extend(_prefixed_lines(policy.content_label, rendered_text, width))
    for source in policy.text_sources:
        value = _resolve_path(props, source.text_source)
        if value is None:
            continue
        rendered_value = str(value)
        if source.quote_text:
            rendered_value = f"“{rendered_value}”"
        lines.extend(_prefixed_lines(source.content_label, rendered_value, width))
    collection = _resolve_path(props, policy.collection_source)
    if isinstance(collection, list):
        lines.extend(_collection_policy_lines(policy, collection, anchors_by_key, width))
    return lines


def _stage_map_policy(widget_registry: WidgetRegistry, component_type: str) -> StageMapPolicy:
    policy = widget_registry.get(component_type).stage_map_policy
    if policy is None:
        raise ValueError(f"widget '{component_type}' does not declare a stage map policy.")
    return policy


def _asset_caption(
    policy: StageMapPolicy, props: Mapping[str, Any], asset_catalog: AssetCatalog
) -> str | None:
    # A remote image is materialized by Compiler as a trusted ``source`` object.
    # Its provider caption is the only description Stage Map may use; the
    # original search query and Agent-provided labels are deliberately ignored.
    source = _source_for_asset_path(props, policy.asset_source)
    if isinstance(source, Mapping):
        caption = source.get("caption")
        if isinstance(caption, str) and caption.strip():
            return caption.strip()
    asset_id = _resolve_path(props, policy.asset_source)
    if not isinstance(asset_id, str):
        return None
    asset = asset_catalog.get(asset_id)
    value = _resolve_path(asset, policy.asset_text_source)
    return str(value) if value is not None else None


def _source_for_asset_path(
    props: Mapping[str, Any], asset_path: str | None
) -> Mapping[str, Any] | None:
    """Find the materialized source adjacent to an asset path.

    A root image has ``props.source``; a flashcard front has
    ``props.front.source``.  The Stage Map policy already names the asset
    path, so deriving its containing object keeps this renderer generic.
    """

    if asset_path is None:
        return None
    parts = asset_path.split(".")
    if parts and parts[0] == "props":
        parts = parts[1:]
    parent = _resolve_path(props, ".".join(parts[:-1])) if parts[:-1] else props
    if not isinstance(parent, Mapping):
        return None
    source = parent.get("source")
    return source if isinstance(source, Mapping) else None


def _resolve_path(root: object, path: str | None) -> object | None:
    if path is None:
        return None
    current = root
    parts = path.split(".")
    if parts and parts[0] == "props":
        parts = parts[1:]
    if parts and parts[0] == "asset":
        parts = parts[1:]
    for part in parts:
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _prefixed_lines(label: str | None, value: str, width: int) -> list[str]:
    text = f"{label}: {value}" if label else value
    return _wrapped_lines(text, width)


def _repeated_asset_policy_lines(
    policy: StageMapPolicy,
    asset_caption: str,
    count: int,
    anchors_by_key: Mapping[str, str],
    width: int,
) -> list[str]:
    lines = _prefixed_lines(policy.content_label, f"{count} × {asset_caption}", width)
    if count <= 0:
        return lines
    item_text = f"ẢNH: {asset_caption}"
    item_anchor_prefix = policy.item_anchor_prefix or ""
    items: list[list[str]] = []
    for index in range(1, count + 1):
        item_lines = [item_text]
        anchor_id = anchors_by_key.get(f"{item_anchor_prefix}{index}")
        if anchor_id:
            item_lines.append(f"[anchor: {anchor_id}]")
        items.append(item_lines)
    return lines + _layout_items(
        items,
        width=width,
        layout=policy.items_layout or "vertical",
    )


def _collection_policy_lines(
    policy: StageMapPolicy,
    collection: list[object],
    anchors_by_key: Mapping[str, str],
    width: int,
) -> list[str]:
    """Render visible fields and a separate compiler-owned anchor for each collection item."""

    items: list[list[str]] = []
    anchor_prefix = policy.collection_anchor_prefix or ""
    for index, item in enumerate(collection, start=1):
        if not isinstance(item, Mapping):
            continue
        item_lines: list[str] = [f"MỐC {index}"]
        for source in policy.collection_text_sources:
            value = _resolve_path(item, source.text_source)
            if value is None:
                continue
            rendered = str(value)
            if source.quote_text:
                rendered = f"“{rendered}”"
            item_lines.append(
                f"{source.content_label}: {rendered}" if source.content_label else rendered
            )
        anchor_id = anchors_by_key.get(f"{anchor_prefix}{index}")
        if anchor_id:
            item_lines.append(f"[anchor: {anchor_id}]")
        items.append(item_lines)
    return _layout_items(
        items,
        width=width,
        layout=policy.collection_layout or "vertical",
    )


def _layout_items(items: list[list[str]], *, width: int, layout: str) -> list[str]:
    """Lay out semantic item lines without knowing which widget produced them."""

    if not items:
        return []
    if layout == "vertical":
        return [
            wrapped
            for item in items
            for line in item
            for wrapped in _wrapped_lines(line, width)
        ]
    if layout == "horizontal":
        return _compose_item_row(items, width=width)
    if layout == "wrap":
        preferred_width = max(
            1,
            max((len(line) for item in items for line in item), default=1) + 2,
        )
        items_per_row = max(1, width // preferred_width)
        lines: list[str] = []
        for start in range(0, len(items), items_per_row):
            lines.extend(_compose_item_row(items[start:start + items_per_row], width=width))
        return lines
    raise StageMapRenderError(f"unsupported stage map item layout: {layout!r}.")


def _compose_item_row(items: list[list[str]], *, width: int) -> list[str]:
    """Render one equal-width row of items, matching Browser grid semantics."""

    if not items:
        return []
    gutter = "   "
    usable_width = width - len(gutter) * (len(items) - 1)
    cell_width = max(1, usable_width // len(items))
    cells = [
        [
            wrapped
            for line in item
            for wrapped in _wrapped_lines(line, cell_width)
        ]
        for item in items
    ]
    anchored_cells = [
        (cell[:-1], cell[-1])
        if cell and _RENDERED_ANCHOR.fullmatch(cell[-1])
        else (cell, None)
        for cell in cells
    ]
    content_height = max(len(content) for content, _anchor in anchored_cells)
    normalized_cells = [
        content
        + ([""] * (content_height - len(content)))
        + ([anchor] if anchor is not None else [])
        for content, anchor in anchored_cells
    ]
    row_height = max(len(cell) for cell in normalized_cells)
    return [
        gutter.join(
            (cell[row] if row < len(cell) else "").center(cell_width)
            for cell in normalized_cells
        )
        for row in range(row_height)
    ]


_RENDERED_ANCHOR = re.compile(r"\[anchor: ([^\]]+)\]")


def _validate_region_anchors(
    components: tuple[ComponentNode, ...],
    anchors: tuple[AnchorBinding, ...],
    region_content: Mapping[str, list[str]],
) -> None:
    """Require every component region to contain exactly its compiler anchors."""

    expected_by_component: dict[str, Counter[str]] = {}
    for anchor in anchors:
        expected_by_component.setdefault(anchor.component_id, Counter())[anchor.anchor_id] += 1
    for component in components:
        rendered = Counter(
            _RENDERED_ANCHOR.findall("\n".join(region_content[component.id]))
        )
        expected = expected_by_component.get(component.id, Counter())
        if rendered != expected:
            raise StageMapRenderError(
                "stage map anchor mismatch for component "
                f"{component.id}: expected {dict(expected)}, rendered {dict(rendered)}."
            )


def _validate_rendered_anchors(anchors: tuple[AnchorBinding, ...], rendered: str) -> None:
    """Reject a final map that loses, duplicates or invents an anchor."""

    expected = Counter(anchor.anchor_id for anchor in anchors)
    actual = Counter(_RENDERED_ANCHOR.findall(rendered))
    if actual != expected:
        raise StageMapRenderError(
            f"stage map anchors are incomplete: expected {dict(expected)}, rendered {dict(actual)}."
        )


def _anchor_lines(anchor_ids: tuple[str | None, ...], width: int) -> list[str]:
    """Keep direct anchor syntax readable without a separate technical key."""

    anchors = [f"[anchor: {anchor_id}]" for anchor_id in anchor_ids if anchor_id]
    if not anchors:
        return []
    lines: list[str] = []
    current = ""
    for anchor in anchors:
        candidate = f"{current}  {anchor}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = anchor
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _wrapped_lines(value: str, width: int) -> list[str]:
    return textwrap.wrap(value, width=max(1, width), break_long_words=True, break_on_hyphens=False) or [""]
