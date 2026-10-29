/** @jsxRuntime classic */
/** @jsx React.createElement */
/** @jsxFrag React.Fragment */
import { React, defineComponent, z } from "../../../frontend/src/runtime/openui";
import { text, mediaProps } from "../../../frontend/src/runtime/schemas";
import { Hidden, useWidgetRuntime, useWidgetRoot } from "../../../frontend/src/runtime/widget";
export const interactionActions = [];
export const widget = defineComponent({
  name: "ObjectGroup", description: "Hiển thị cùng một ảnh asset hoặc media đúng count lần. count là số lần hiển thị ảnh, không phải số vật bên trong ảnh.",
  props: mediaProps({ count: z.number().int().min(1).describe("Số lần hiển thị toàn bộ ảnh. Số vật mỗi ảnh × count = tổng số vật; bài đếm ưu tiên ảnh đúng một vật và count bằng tổng cần có."), label: text().nullish() }),
  component: () => {
    const { component, anchorsByKey } = useWidgetRuntime(), p = component.props;
    const hidden = component.state?.visibility === "hidden", count = Number(p.count || 0);
    const imageUrl = p.source?.url || p.asset_url || "";
    const columns = count <= 4 ? Math.max(1, count) : Math.ceil(Math.sqrt(count));
    const rows = Math.max(1, Math.ceil(count / columns));
    return <section {...useWidgetRoot("group", `lumi-widget lumi-widget-object-group${hidden ? " lumi-widget-hidden-content" : ""}`)}>
      {hidden ? <Hidden paragraph /> : <>
        {p.label && <p className="lumi-widget-object-group-label">{p.label}</p>}
        <div className="lumi-widget-object-group-items"
          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>
          {Array.from({ length: count }, (_, index) => <img key={index} src={imageUrl} alt={p.label || p.source?.caption || ""} draggable={false} data-anchor-id={anchorsByKey[`item_${index + 1}`]?.anchor_id}/>)}
        </div>
      </>}
    </section>;
  },
});
