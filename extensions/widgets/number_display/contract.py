"""Contract for the number_display extension."""

from lumi_openui.openui_schema import unbound_props
from lumi_openui.widgets import StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetStateDefinition


_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})
_VALUE = WidgetStateDefinition(name="value", value_type="string", default_value="", initial_prop="value", max_length=20)
WIDGET_EXTENSION = WidgetDefinition(
    validate_props=unbound_props, anchor_policy=lambda _: (WidgetAnchor("number"),),
    purpose="Hiển thị số.",
    props=(),
    state_fields=(_VISIBILITY, _VALUE), stage_map_policy=StageMapPolicy(kind="number_display", content_label="SỐ", text_source="props.value", anchor_key="number"),
)
