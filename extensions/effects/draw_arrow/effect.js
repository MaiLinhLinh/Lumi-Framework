/** Draw an overlay arrow that points to one Runtime-validated target. */
export function run({ overlay, rect }) {
  const x = rect.x + rect.width / 2;
  const y = rect.y + rect.height / 2;
  const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
  arrow.setAttribute("class", "lumi-overlay-shape lumi-overlay-draw-arrow");
  arrow.setAttribute("pathLength", "100");
  arrow.setAttribute("d", `M ${Math.max(8, x - 100)} ${Math.max(10, y - 68)} L ${x} ${y}`);
  overlay.append(arrow);
  return () => arrow.remove();
}
