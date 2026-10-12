/** Dim the panel outside the Runtime-validated target. */
export function run({ overlay, rect }) {
  const box = overlay.getBoundingClientRect();
  const padding = 8;
  const x = Math.max(0, rect.x - padding);
  const y = Math.max(0, rect.y - padding);
  const width = Math.min(box.width - x, rect.width + padding * 2);
  const height = Math.min(box.height - y, rect.height + padding * 2);
  const mask = document.createElementNS("http://www.w3.org/2000/svg", "path");
  mask.setAttribute("class", "lumi-overlay-spotlight");
  mask.setAttribute("fill-rule", "evenodd");
  mask.setAttribute("d", `M 0 0 H ${box.width} V ${box.height} H 0 Z M ${x} ${y} H ${x + width} V ${y + height} H ${x} Z`);
  overlay.append(mask);
  return () => mask.remove();
}
