"""Contract for the image extension."""

from typing import Any, Mapping

from final_framework.widgets import (
    StageMapPolicy, WidgetAnchor, WidgetAssetReferenceDefinition, WidgetDefinition,
    WidgetPropDefinition, WidgetPropsError, WidgetStateDefinition,
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetPropsError(f"{field} must be a non-empty string.")
    return value.strip()


def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"asset_id", "media_id", "label"}
    if unknown:
        raise WidgetPropsError(f"image.props has unsupported fields: {sorted(unknown)}.")
    asset_id, media_id = props.get("asset_id"), props.get("media_id")
    if (asset_id is None) == (media_id is None):
        raise WidgetPropsError("image.props must contain exactly one of asset_id or media_id.")
    result: dict[str, Any] = {"asset_id": _text(asset_id, "image.asset_id")} if asset_id is not None else {"media_id": _text(media_id, "image.media_id")}
    if props.get("label") is not None: result["label"] = _text(props["label"], "image.label")
    return result


_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})

WIDGET_EXTENSION = WidgetDefinition(
    validate_props=_validate_props,
    anchor_policy=lambda _: (WidgetAnchor("image"),),
    purpose="Hiển thị một ảnh từ Asset Catalog hoặc kết quả search ảnh đã được backend xác minh.",
    props=(
        WidgetPropDefinition("asset_id", "string", False, "ID của asset ảnh sẽ hiển thị.", source="asset_catalog.id", template_value_kind="binding"),
        WidgetPropDefinition("media_id", "string", False, "ID media từ Media Worker đã được xác minh trong Live session hiện tại.", source="knowledge.media_id", template_value_kind="binding"),
        WidgetPropDefinition("label", "string", False, "Nhãn ngắn cho ảnh.", template_value_kind="binding"),
    ),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(kind="image", content_label="ẢNH", asset_source="props.asset_id", asset_text_source="asset.caption", anchor_key="image", text_rendered=False),
    asset_references=(WidgetAssetReferenceDefinition(path="props.asset_id", allowed_kinds=("image", "icon"), required=False),),
)
