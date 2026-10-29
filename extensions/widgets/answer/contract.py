"""Contract for the answer extension."""

from lumi_openui.openui_schema import unbound_props
from lumi_openui.widgets import StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetStateDefinition


_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})
WIDGET_EXTENSION = WidgetDefinition(
    validate_props=unbound_props, anchor_policy=lambda _: (WidgetAnchor("answer"),),
    purpose="Hiển thị một đáp án số hoặc chữ ngắn; khi hidden hiển thị dấu ?, khi visible hiển thị value.",
    props=(),
    state_fields=(_VISIBILITY,), stage_map_policy=StageMapPolicy(kind="answer", content_label="KẾT QUẢ", text_source="props.value", anchor_key="answer"),
)
