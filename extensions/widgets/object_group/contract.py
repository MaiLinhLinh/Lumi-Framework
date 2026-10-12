"""Contract for the object_group extension."""

from typing import Any, Mapping

from final_framework.widgets import (
    StageMapPolicy, WidgetAnchor, WidgetAssetReferenceDefinition, WidgetDefinition,
    WidgetPropDefinition, WidgetPropsError, WidgetStateDefinition,
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetPropsError(f"{field} must be a non-empty string.")
    return value.strip()

def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WidgetPropsError(f"{field} must be a positive integer.")
    return value

def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"asset_id", "media_id", "count", "label"}
    if unknown:
        raise WidgetPropsError(
            f"object_group.props has unsupported fields: {sorted(unknown)}."
        )
    asset_id, media_id = props.get("asset_id"), props.get("media_id")
    if (asset_id is None) == (media_id is None):
        raise WidgetPropsError(
            "object_group.props must contain exactly one of asset_id or media_id."
        )
    result: dict[str, Any] = {
        "count": _positive(props.get("count"), "object_group.count")
    }
    if asset_id is not None:
        result["asset_id"] = _text(asset_id, "object_group.asset_id")
    else:
        result["media_id"] = _text(media_id, "object_group.media_id")
    if props.get("label") is not None:
        result["label"] = _text(props["label"], "object_group.label")
    return result

_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})

WIDGET_EXTENSION = WidgetDefinition(
    validate_props=_validate_props,
    anchor_policy=lambda props: (
        (WidgetAnchor("group"),)
        if "media_id" in props
        else (
            WidgetAnchor("group"),
            *(
                WidgetAnchor(f"item_{index}")
                for index in range(1, props["count"] + 1)
            ),
        )
    ),
    # A generated composite image renders once, regardless of the semantic
    # object count. Reserve only that stable root target while media is pending.
    draft_anchor_policy=lambda _props: (WidgetAnchor("group"),),
    purpose=(
        "Hiển thị một nhóm bằng cách lặp asset có sẵn hoặc hiển thị nguyên một "
        "ảnh tổng hợp từ media đã được backend xác minh."
    ),
    props=(
        WidgetPropDefinition(
            "asset_id", "string", False,
            "ID của asset đơn được lặp theo count.",
            source="asset_catalog.id", template_value_kind="binding",
        ),
        WidgetPropDefinition(
            "media_id", "string", False,
            "ID của ảnh tổng hợp đã chứa cả nhóm; renderer hiển thị ảnh đúng một lần.",
            source="knowledge.media_id", template_value_kind="binding",
        ),
        WidgetPropDefinition(
            "count", "integer", True,
            "Số lượng đối tượng trong nhóm; chỉ điều khiển số bản sao khi dùng asset_id.",
            minimum=1, template_value_kind="binding",
        ),
        WidgetPropDefinition(
            "label", "string", False, "Nhãn ngắn cho cả nhóm.",
            template_value_kind="binding",
        ),
    ),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(kind="object_group", content_label="NHÓM", asset_source="props.asset_id", asset_text_source="asset.caption", count_source="props.count", anchor_key="group", item_anchor_prefix="item_", items_layout="wrap", text_rendered=False),
    asset_references=(
        WidgetAssetReferenceDefinition(path="props.asset_id", required=False),
    ),
)
