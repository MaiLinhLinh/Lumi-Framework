"""Contract for the image extension."""

from lumi_openui.openui_schema import unbound_props

from lumi_openui.widgets import (
    StageMapPolicy, WidgetAnchor, WidgetAssetReferenceDefinition, WidgetDefinition,
    WidgetStateDefinition,
)


_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})

WIDGET_EXTENSION = WidgetDefinition(
    validate_props=unbound_props,
    anchor_policy=lambda _: (WidgetAnchor("image"),),
    purpose="Hiển thị một ảnh từ Asset Catalog hoặc kết quả search ảnh đã được backend xác minh.",
    props=(),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(kind="image", content_label="ẢNH", asset_source="props.asset_id", asset_text_source="asset.caption", anchor_key="image", text_rendered=False),
    asset_references=(WidgetAssetReferenceDefinition(path="props.asset_id", allowed_kinds=("image", "icon"), required=False),),
)
