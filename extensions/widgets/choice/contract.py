"""Contract for the selectable choice extension."""

from lumi_openui.openui_schema import unbound_props
from lumi_openui.widgets import StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetInteractionDefinition, WidgetStateDefinition


_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})
_SELECTED = WidgetStateDefinition(name="selected", value_type="boolean", default_value=False)
WIDGET_EXTENSION = WidgetDefinition(
    validate_props=unbound_props, anchor_policy=lambda _: (WidgetAnchor("choice"),),
    purpose="Tạo một lựa chọn có thể chạm/chọn; hiển thị ảnh hoặc nhóm ở trên và nhãn/chữ ở dưới.", props=(), state_fields=(_VISIBILITY, _SELECTED),
    allowed_child_widget_ids=("image", "text", "number_display", "object_group"),
    interactions=(WidgetInteractionDefinition("select", "Trẻ chạm hoặc chọn toàn bộ thẻ lựa chọn."),),
    stage_map_policy=StageMapPolicy(kind="container", anchor_key="choice", children_layout="vertical"),
)
