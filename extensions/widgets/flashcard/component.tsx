/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineComponent, z } from "../../../frontend/src/runtime/openui";
import { text, mediaProps } from "../../../frontend/src/runtime/schemas";
import { Hidden, useWidgetRuntime, useWidgetRoot } from "../../../frontend/src/runtime/widget";
export const interactionActions = ["flip"];
export const widget = defineComponent({
  name: "Flashcard", description: "Thẻ từ vựng hai mặt, backend quản lý trạng thái lật.",
  props: z.strictObject({
    front: mediaProps({ text: text().max(80) }),
    back: z.strictObject({ word: text().max(80), phonetic: text().max(80), meaning: text().max(80) }),
  }),
  component: ({ props }) => {
    const { component, anchorsByKey, emitInteraction } = useWidgetRuntime();
    const hidden = component.state?.visibility === "hidden", flipped = component.state?.flipped === true;
    const front = component.props.front || {};
    const flip = () => {
      const anchorId = anchorsByKey.card?.anchor_id;
      if (anchorId) emitInteraction({ anchor_id: anchorId, action: "flip" });
    };
    return <div {...useWidgetRoot("card", `lumi-widget lumi-widget-flashcard${hidden ? " lumi-widget-hidden-content" : flipped ? " is-flipped" : ""}`)}
      role="button" tabIndex={0} aria-label="Lật thẻ từ vựng" aria-pressed={flipped}
      onClick={hidden ? undefined : flip} onKeyDown={hidden ? undefined : event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault(); flip();
      }}>
      {hidden ? <Hidden /> : <div className="lumi-widget-flashcard-inner">
        <section className="lumi-widget-flashcard-face lumi-widget-flashcard-front">
          <img src={front.source?.url || front.asset_url || ""} alt={front.source?.caption || ""} draggable={false}/>
          <p>{props.front.text}</p>
        </section>
        <section className="lumi-widget-flashcard-face lumi-widget-flashcard-back">
          <strong>{props.back.word}</strong><span>{props.back.phonetic}</span><p>{props.back.meaning}</p>
        </section>
      </div>}
    </div>;
  },
});
