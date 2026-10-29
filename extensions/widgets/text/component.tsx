/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineComponent, z } from "../../../frontend/src/runtime/openui";
import { text } from "../../../frontend/src/runtime/schemas";
import { useWidgetRuntime, useWidgetRoot } from "../../../frontend/src/runtime/widget";
export const interactionActions = [];
function fitText(element: HTMLElement) {
  const overflows = () => element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1;
  element.style.removeProperty("font-size");
  if (!overflows()) return;
  const size = Number.parseFloat(getComputedStyle(element).fontSize);
  if (!Number.isFinite(size) || size <= 0) return;
  const classes = element.classList;
  let low = classes.contains("lumi-widget-text-title") ? 14 : classes.contains("lumi-widget-text-subtitle") ? 12 : classes.contains("lumi-widget-text-label") ? 11 : classes.contains("lumi-widget-text-token") ? 14 : 12;
  let high = size, best = low;
  element.style.fontSize = `${low}px`;
  if (!overflows()) for (let step = 0; step < 9; step++) {
    const candidate = (low + high) / 2;
    element.style.fontSize = `${candidate}px`;
    if (overflows()) high = candidate; else { best = candidate; low = candidate; }
  }
  element.style.fontSize = `${best.toFixed(2)}px`;
}
export const widget = defineComponent({
  name: "Text", description: "Văn bản tự do, giữ vai trò và tự co chữ khi cần.",
  props: z.strictObject({ content: text(), role: z.enum(["title", "subtitle", "label", "body"]).default("body") }),
  component: ({ props }) => {
    const { component } = useWidgetRuntime();
    const ref = React.useRef<HTMLParagraphElement>(null);
    const content = component.state?.visibility === "hidden" ? "Nội dung đang ẩn" : props.content;
    React.useLayoutEffect(() => {
      const element = ref.current!;
      let frame = 0;
      const schedule = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; fitText(element); }); };
      const observer = new ResizeObserver(schedule); observer.observe(element); schedule();
      return () => { observer.disconnect(); cancelAnimationFrame(frame); };
    }, [content, props.role]);
    return <p ref={ref} {...useWidgetRoot("text", `lumi-widget lumi-widget-text lumi-widget-text-${props.role}${/^.{1,3}$/u.test(content.trim()) ? " lumi-widget-text-token" : ""}`)}>{content}</p>;
  },
});
