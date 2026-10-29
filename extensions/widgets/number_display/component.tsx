/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineComponent, z } from "../../../frontend/src/runtime/openui";
import { text } from "../../../frontend/src/runtime/schemas";
import { useWidgetRuntime, useWidgetRoot } from "../../../frontend/src/runtime/widget";
export const interactionActions = [];
export const widget = defineComponent({
  name: "NumberDisplay", description: "Hiển thị số.",
  props: z.strictObject({ value: text().max(20) }),
  component: ({ props }) => {
    const { component } = useWidgetRuntime();
    return <output {...useWidgetRoot("number", "lumi-widget lumi-widget-number-display")}>
      <span className="lumi-widget-number-display-value">{component.state?.visibility === "hidden" ? "?" : (component.state?.value ?? props.value)}</span>
    </output>;
  },
});
