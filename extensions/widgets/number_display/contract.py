"""Contract for the number_display extension."""

from typing import Any, Mapping
from final_framework.widgets import StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetPropDefinition, WidgetPropsError, WidgetStateDefinition

def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"value"}
    if unknown: raise WidgetPropsError(f"number_display.props has unsupported fields: {sorted(unknown)}.")
    value = props.get("value")
    if not isinstance(value, str) or not value.strip(): raise WidgetPropsError("number_display.value must be a non-empty string.")
    if len(value.strip()) > 20: raise WidgetPropsError("number_display.value must not exceed 20 characters.")
    return {"value": value.strip()}

_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})
WIDGET_EXTENSION = WidgetDefinition(
    validate_props=_validate_props, anchor_policy=lambda _: (WidgetAnchor("number"),),
    purpose="Hiển thị một số hoặc giá trị ngắn thật lớn, rõ ràng và cân giữa trong vùng toán học.",
    props=(WidgetPropDefinition("value", "string", True, "Số hoặc giá trị ngắn cần hiển thị nổi bật.", template_value_kind="binding"),),
    state_fields=(_VISIBILITY,), stage_map_policy=StageMapPolicy(kind="number_display", content_label="SỐ", text_source="props.value", anchor_key="number"),
)
