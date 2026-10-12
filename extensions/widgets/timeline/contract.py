"""Contract for a compact timeline used by history, lifecycle and process activities."""

from __future__ import annotations

from typing import Any, Mapping

from final_framework.widgets import (
    StageMapCollectionTextSource, StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetPropDefinition,
    WidgetPropsError, WidgetStateDefinition,
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetPropsError(f"{field} must be a non-empty string.")
    return value.strip()


def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"title", "items"}
    if unknown:
        raise WidgetPropsError(f"timeline.props has unsupported fields: {sorted(unknown)}.")
    items = props.get("items")
    if not isinstance(items, list) or not 2 <= len(items) <= 6:
        raise WidgetPropsError("timeline.items must contain from 2 to 6 milestones.")
    normalized_items: list[dict[str, str]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping) or set(item) != {"label", "title", "description"}:
            raise WidgetPropsError(f"timeline.items[{index}] must contain exactly label, title and description.")
        normalized_items.append({
            "label": _text(item.get("label"), f"timeline.items[{index}].label"),
            "title": _text(item.get("title"), f"timeline.items[{index}].title"),
            "description": _text(item.get("description"), f"timeline.items[{index}].description"),
        })
    result: dict[str, Any] = {"items": normalized_items}
    if props.get("title") is not None:
        result["title"] = _text(props["title"], "timeline.title")
    return result


def _draft_anchor_policy(props: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    items = props.get("items")
    if not isinstance(items, list) or not 2 <= len(items) <= 6:
        raise WidgetPropsError(
            "partial timeline.props.items must declare a structural skeleton of 2 to 6 items."
        )
    if not all(isinstance(item, Mapping) for item in items):
        raise WidgetPropsError("partial timeline items must be objects.")
    return tuple(
        WidgetAnchor(f"milestone_{index}")
        for index in range(1, len(items) + 1)
    )


_VISIBILITY = WidgetStateDefinition(
    name="visibility", value_type="string", default_value="visible",
    allowed_values=("visible", "hidden"),
    transitions={"visible": ("hidden",), "hidden": ("visible",)},
)


WIDGET_EXTENSION = WidgetDefinition(
    validate_props=_validate_props,
    anchor_policy=lambda props: tuple(
        WidgetAnchor(f"milestone_{index}")
        for index in range(1, len(props["items"]) + 1)
    ),
    draft_anchor_policy=_draft_anchor_policy,
    purpose=(
        "Trực quan hoá từ 2 đến 6 mốc theo thứ tự cho bài lịch sử, vòng đời "
        "hoặc quy trình; mỗi mốc có nhãn, tiêu đề và mô tả ngắn."
    ),
    props=(
        WidgetPropDefinition("title", "string", False, "Tiêu đề ngắn cho cả dòng thời gian.", template_value_kind="binding"),
        WidgetPropDefinition("items", "array", True, "Mảng 2–6 mốc theo thứ tự. Mỗi mốc bắt buộc có label, title và description.", template_value_kind="binding"),
    ),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(
        kind="timeline",
        content_label="DÒNG THỜI GIAN",
        text_source="props.title",
        collection_source="props.items",
        collection_anchor_prefix="milestone_",
        collection_layout="horizontal",
        collection_text_sources=(
            StageMapCollectionTextSource("NHÃN", "label"),
            StageMapCollectionTextSource("TIÊU ĐỀ", "title"),
            StageMapCollectionTextSource("MÔ TẢ", "description"),
        ),
    ),
)
