/** @jsxRuntime classic */
/** @jsx React.createElement */
import { React, defineEffect } from "../../../frontend/src/presentation/effect";
export const effect = defineEffect({ component: ({ context: { rect } }) => <rect
  className="lumi-overlay-highlight" x={Math.max(0, rect.x - 5)} y={Math.max(0, rect.y - 5)}
  width={rect.width + 10} height={rect.height + 10} rx="12" ry="12"/> });
