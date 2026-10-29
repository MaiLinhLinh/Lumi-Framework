"""Contract for a compact timeline used by history, lifecycle and process activities."""

from __future__ import annotations

from lumi_openui.openui_schema import unbound_props
from typing import Any, Mapping

from lumi_openui.widgets import (
    StageMapCollectionTextSource, StageMapPolicy, WidgetAnchor, WidgetDefinition, 
    WidgetPropsError, WidgetStateDefinition,
)


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
    validate_props=unbound_props,
    anchor_policy=lambda props: tuple(
        WidgetAnchor(f"milestone_{index}")
        for index in range(1, len(props["items"]) + 1)
    ),
    draft_anchor_policy=_draft_anchor_policy,
    purpose=(
        "Trực quan hoá từ 2 đến 6 mốc theo thứ tự cho bài lịch sử, vòng đời "
        "hoặc quy trình; mỗi mốc có nhãn, tiêu đề và mô tả ngắn."
    ),
    props=(),
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
