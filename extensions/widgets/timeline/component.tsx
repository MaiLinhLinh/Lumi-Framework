/** @jsxRuntime classic */
/** @jsx React.createElement */
/** @jsxFrag React.Fragment */
import { React, defineComponent, z } from "../../../frontend/src/runtime/openui";
import { text } from "../../../frontend/src/runtime/schemas";
import { useWidgetRuntime, useWidgetRoot } from "../../../frontend/src/runtime/widget";
export const interactionActions = [];
export const widget = defineComponent({
  name: "Timeline", description: "Dòng thời gian từ 2 đến 6 mốc, mỗi mốc có anchor riêng.",
  props: z.strictObject({ title: text().nullish(), items: z.array(z.strictObject({ label: text(), title: text(), description: text() })).min(2).max(6) }),
  component: ({ props }) => {
    const { component, anchorsByKey } = useWidgetRuntime(), hidden = component.state?.visibility === "hidden";
    return <section {...useWidgetRoot(undefined, `lumi-widget lumi-widget-timeline${hidden ? " lumi-widget-hidden-content" : ""}`)}>
      {hidden ? "Nội dung đang ẩn" : <>
        {props.title && <h3 className="lumi-widget-timeline-title">{props.title}</h3>}
        <ol className="lumi-widget-timeline-items">{props.items.map((item, index) =>
          <li key={index} className="lumi-widget-timeline-item" data-anchor-id={anchorsByKey[`milestone_${index + 1}`]?.anchor_id}>
            <p className="lumi-widget-timeline-label">{item.label}</p><h4 className="lumi-widget-timeline-item-title">{item.title}</h4><p className="lumi-widget-timeline-description">{item.description}</p>
          </li>)}</ol>
      </>}
    </section>;
  },
});
