"""Backend contract for the extension-owned two-sided flashcard widget."""

from __future__ import annotations

from lumi_openui.openui_schema import unbound_props

from lumi_openui.widgets import (
    StageMapPolicy,
    StageMapTextSource,
    StageMapView,
    WidgetAnchor,
    WidgetAssetReferenceDefinition,
    WidgetDefinition,
    WidgetInteractionDefinition,
    WidgetStateDefinition,
)


_VISIBILITY = WidgetStateDefinition(
    name="visibility",
    value_type="string",
    default_value="visible",
    allowed_values=("visible", "hidden"),
    transitions={"visible": ("hidden",), "hidden": ("visible",)},
)
_FLIPPED = WidgetStateDefinition(name="flipped", value_type="boolean", default_value=False)


WIDGET_EXTENSION = WidgetDefinition(
    validate_props=unbound_props,
    anchor_policy=lambda _: (WidgetAnchor("card"),),
    purpose="Hiển thị thẻ từ vựng có thể lật giữa mặt ảnh và mặt kiến thức.",
    props=(),
    state_fields=(_VISIBILITY, _FLIPPED),
    interactions=(WidgetInteractionDefinition("flip", "Chạm hoặc dùng bàn phím để lật giữa hai mặt thẻ.", state_rule={"flipped": {"op": "toggle"}}),),
    stage_map_policy=StageMapPolicy(
        kind="flashcard", anchor_key="card", views=(
            StageMapView("flipped", False, StageMapPolicy(kind="flashcard_front", content_label="ẢNH", asset_source="props.front.asset_id", asset_text_source="asset.caption", text_rendered=True, text_sources=(StageMapTextSource("CHỮ", "props.front.text", True),))),
            StageMapView("flipped", True, StageMapPolicy(kind="flashcard_back", text_rendered=True, text_sources=(StageMapTextSource("TỪ", "props.back.word", True), StageMapTextSource("PHIÊN ÂM", "props.back.phonetic", True), StageMapTextSource("NGHĨA", "props.back.meaning", True)))),
        ),
    ),
    asset_references=(WidgetAssetReferenceDefinition(path="props.front.asset_id", required=False),),
)
