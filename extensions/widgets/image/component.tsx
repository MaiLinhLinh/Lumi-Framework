/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineComponent } from "../../../frontend/src/runtime/openui";
import { text, mediaProps } from "../../../frontend/src/runtime/schemas";
import { Hidden, useWidgetRuntime, useWidgetRoot } from "../../../frontend/src/runtime/widget";
export const interactionActions = [];
export const widget = defineComponent({
  name: "Image", description: "Hiển thị ảnh từ asset_id hoặc media_id đã được backend xác minh.",
  props: mediaProps({ label: text().nullish() }),
  component: () => {
    const { component } = useWidgetRuntime(), p = component.props;
    const hidden = component.state?.visibility === "hidden";
    return <figure {...useWidgetRoot("image", `lumi-widget lumi-widget-image${hidden ? " lumi-widget-hidden-content" : ""}`)}>
      {hidden ? <Hidden /> : <img src={p.source?.url || p.asset_url || ""} alt={p.label || p.source?.caption || ""} draggable={false} />}
    </figure>;
  },
});
