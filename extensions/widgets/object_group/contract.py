"""Contract for the object_group extension."""

from lumi_openui.openui_schema import unbound_props

from lumi_openui.widgets import (
    StageMapPolicy, WidgetAnchor, WidgetAssetReferenceDefinition, WidgetDefinition,
    WidgetStateDefinition, WidgetPropsError,
)


_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})


def _anchors(props):
    count = props.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise WidgetPropsError("object_group.props.count must declare a positive integer image repetition count.")
    return (WidgetAnchor("group"), *(WidgetAnchor(f"item_{index}") for index in range(1, count + 1)))


WIDGET_EXTENSION = WidgetDefinition(
    validate_props=unbound_props,
    anchor_policy=_anchors,
    # Reserve every image target before async media arrives.
    draft_anchor_policy=_anchors,
    purpose=(
        "Lặp cùng một ảnh asset hoặc media đúng count lần. count là số lần hiển thị ảnh, "
        "không phải số vật trong ảnh. Số vật mỗi ảnh × count = tổng số vật mong muốn. "
        "Bài đếm ưu tiên vẽ đúng một vật rồi lặp count bằng số lượng cần có."
    ),
    props=(),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(kind="object_group", content_label="NHÓM", asset_source="props.asset_id", asset_text_source="asset.caption", count_source="props.count", anchor_key="group", item_anchor_prefix="item_", items_layout="wrap", text_rendered=False),
    asset_references=(
        WidgetAssetReferenceDefinition(path="props.asset_id", required=False),
    ),
)
