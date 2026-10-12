/** Draw a bounded emphasis frame around the Runtime-validated target. */
export function run({ overlay, rect }) {
  const padding = 5;
  const frame = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  frame.setAttribute("class", "lumi-overlay-highlight");
  frame.setAttribute("x", String(Math.max(0, rect.x - padding)));
  frame.setAttribute("y", String(Math.max(0, rect.y - padding)));
  frame.setAttribute("width", String(rect.width + padding * 2));
  frame.setAttribute("height", String(rect.height + padding * 2));
  frame.setAttribute("rx", "12");
  frame.setAttribute("ry", "12");
  overlay.append(frame);
  return () => frame.remove();
}
