"""Contract for the text extension."""

from lumi_openui.openui_schema import unbound_props

from lumi_openui.widgets import (
    StageMapPolicy, WidgetAnchor, WidgetDefinition, 
    WidgetStateDefinition,
)


_VISIBILITY = WidgetStateDefinition(
    name="visibility", value_type="string", default_value="visible",
    allowed_values=("visible", "hidden"),
    transitions={"visible": ("hidden",), "hidden": ("visible",)},
)

WIDGET_EXTENSION = WidgetDefinition(
    validate_props=unbound_props,
    anchor_policy=lambda _: (WidgetAnchor("text"),),
    purpose="Hiển thị văn bản tự do như tiêu đề, nhãn hoặc nội dung ngắn.",
    props=(),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(kind="text", content_label="CHỮ", quote_text=True, text_source="props.content", anchor_key="text"),
)
