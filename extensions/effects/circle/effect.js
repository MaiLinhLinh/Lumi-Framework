/** Draw an overlay circle around the Runtime-validated presentation target. */
export function run({ overlay, rect }) {
  const circle = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
  circle.setAttribute("class", "lumi-overlay-shape lumi-overlay-draw-circle");
  circle.setAttribute("pathLength", "100");
  circle.setAttribute("cx", String(rect.x + rect.width / 2));
  circle.setAttribute("cy", String(rect.y + rect.height / 2));
  circle.setAttribute("rx", String(Math.max(14, rect.width / 2 + 8)));
  circle.setAttribute("ry", String(Math.max(14, rect.height / 2 + 8)));
  overlay.append(circle);
  return () => circle.remove();
}
