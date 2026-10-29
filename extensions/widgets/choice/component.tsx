/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineComponent, z } from "../../../frontend/src/runtime/openui";
import { useWidgetRuntime, useWidgetRoot } from "../../../frontend/src/runtime/widget";
import { widget as Image } from "../image/component";
import { widget as Text } from "../text/component";
import { widget as NumberDisplay } from "../number_display/component";
import { widget as ObjectGroup } from "../object_group/component";
export const interactionActions = ["select"];
// Children are OpenUI expressions; Python still owns allowed-child/runtime policy.
export const widget = defineComponent({
  name: "Choice", description: "Thẻ lựa chọn, phát action select với anchor của toàn thẻ.",
  props: z.strictObject({ children: z.array(z.union([Image.ref, Text.ref, NumberDisplay.ref, ObjectGroup.ref])).optional() }),
  component: ({ props, renderNode }) => {
    const { anchorsByKey, emitInteraction } = useWidgetRuntime();
    const select = () => {
      const anchorId = anchorsByKey.choice?.anchor_id;
      if (anchorId) emitInteraction({ anchor_id: anchorId, action: "select" });
    };
    return <div {...useWidgetRoot("choice", "lumi-widget lumi-widget-choice")} role="button" tabIndex={0} aria-label="Lựa chọn"
      onClick={select} onKeyDown={event => { if (event.key !== "Enter" && event.key !== " ") return; event.preventDefault(); select(); }}>
      <div className="lumi-widget-choice-content">{renderNode(props.children)}</div>
    </div>;
  },
});
