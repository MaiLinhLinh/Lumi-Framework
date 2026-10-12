"""Contract for the text extension."""

from typing import Any, Mapping

from final_framework.widgets import (
    StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetPropDefinition,
    WidgetPropsError, WidgetStateDefinition,
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetPropsError(f"{field} must be a non-empty string.")
    return value.strip()


def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"content", "role"}
    if unknown:
        raise WidgetPropsError(f"text.props has unsupported fields: {sorted(unknown)}.")
    role = props.get("role", "body")
    if role not in {"title", "subtitle", "label", "body"}:
        raise WidgetPropsError("text.role must be title, subtitle, label or body.")
    return {"content": _text(props.get("content"), "text.content"), "role": role}


_VISIBILITY = WidgetStateDefinition(
    name="visibility", value_type="string", default_value="visible",
    allowed_values=("visible", "hidden"),
    transitions={"visible": ("hidden",), "hidden": ("visible",)},
)

WIDGET_EXTENSION = WidgetDefinition(
    validate_props=_validate_props,
    anchor_policy=lambda _: (WidgetAnchor("text"),),
    purpose="Hiển thị văn bản tự do như tiêu đề, nhãn hoặc nội dung ngắn.",
    props=(
        WidgetPropDefinition("content", "string", True, "Nội dung văn bản cần hiển thị.", template_value_kind="binding"),
        WidgetPropDefinition("role", "string", False, "Vai trò trình bày của văn bản.", allowed_values=("title", "subtitle", "label", "body")),
    ),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(kind="text", content_label="CHỮ", quote_text=True, text_source="props.content", anchor_key="text"),
)
