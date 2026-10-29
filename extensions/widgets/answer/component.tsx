/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineComponent, z } from "../../../frontend/src/runtime/openui";
import { text } from "../../../frontend/src/runtime/schemas";
import { useWidgetRuntime, useWidgetRoot } from "../../../frontend/src/runtime/widget";
export const interactionActions = [];
export const widget = defineComponent({
  name: "Answer", description: "Hiển thị đáp án ngắn; hidden hiển thị dấu ?.",
  props: z.strictObject({ value: text().max(80) }),
  component: ({ props }) => {
    const { component } = useWidgetRuntime();
    return <output {...useWidgetRoot("answer", "lumi-widget lumi-widget-answer")}>
      {component.state?.visibility === "hidden" ? "?" : props.value}
    </output>;
  },
});
