/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineEffect } from "../../../frontend/src/presentation/effect";
export const effect = defineEffect({ component: ({ context: { rect } }) => {
  const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
  return <path className="lumi-overlay-shape lumi-overlay-draw-arrow" pathLength="100"
    d={`M ${Math.max(8, x - 100)} ${Math.max(10, y - 68)} L ${x} ${y}`}/>;
} });
