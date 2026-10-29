/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineEffect } from "../../../frontend/src/presentation/effect";
export const effect = defineEffect({ component: ({ context: { overlay, rect } }) => {
  const box = overlay.getBoundingClientRect();
  const x = Math.max(0, rect.x - 8), y = Math.max(0, rect.y - 8);
  const width = Math.min(box.width - x, rect.width + 16), height = Math.min(box.height - y, rect.height + 16);
  return <path className="lumi-overlay-spotlight" fillRule="evenodd"
    d={`M 0 0 H ${box.width} V ${box.height} H 0 Z M ${x} ${y} H ${x + width} V ${y + height} H ${x} Z`}/>;
} });
