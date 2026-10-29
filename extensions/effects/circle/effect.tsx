/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineEffect } from "../../../frontend/src/presentation/effect";
export const effect = defineEffect({ component: ({ context: { rect } }) => <ellipse
  className="lumi-overlay-shape lumi-overlay-draw-circle" pathLength="100"
  cx={rect.x + rect.width / 2} cy={rect.y + rect.height / 2}
  rx={Math.max(14, rect.width / 2 + 8)} ry={Math.max(14, rect.height / 2 + 8)}/> });
