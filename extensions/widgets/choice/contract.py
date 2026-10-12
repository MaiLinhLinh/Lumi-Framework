"""Contract for the selectable choice extension."""

from typing import Any, Mapping
from final_framework.widgets import StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetInteractionDefinition, WidgetPropsError, WidgetStateDefinition

def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    if props: raise WidgetPropsError("choice.props must be empty; the compiler assigns its interaction anchor.")
    return {}

_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})
_SELECTED = WidgetStateDefinition(name="selected", value_type="boolean", default_value=False)
WIDGET_EXTENSION = WidgetDefinition(
    validate_props=_validate_props, anchor_policy=lambda _: (WidgetAnchor("choice"),),
    purpose="Tạo một lựa chọn có thể chạm/chọn; hiển thị ảnh hoặc nhóm ở trên và nhãn/chữ ở dưới.", props=(), state_fields=(_VISIBILITY, _SELECTED),
    allowed_child_widget_ids=("image", "text", "number_display", "object_group"),
    interactions=(WidgetInteractionDefinition("select", "Trẻ chạm hoặc chọn toàn bộ thẻ lựa chọn."),),
    stage_map_policy=StageMapPolicy(kind="container", anchor_key="choice", children_layout="vertical"),
)
