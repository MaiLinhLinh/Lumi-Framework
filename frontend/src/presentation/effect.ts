import React, { useLayoutEffect, type ComponentType } from "react";
import type { AudioMarker } from "../services/audio";
export { React, useLayoutEffect };

/** Internal React extension API. Command fields remain the existing Lumi cue. */
export interface VisualCommand extends AudioMarker {
  anchor_id: string;
  effect: string;
  panel_revision: number;
  animation_delay_ms?: number;
  panel_id?: string;
}
export interface EffectContext {
  target: Element;
  overlay: SVGSVGElement;
  rect: { x: number; y: number; width: number; height: number };
}
export interface EffectProps { context: EffectContext; command: VisualCommand }
export interface EffectDefinition { component: ComponentType<EffectProps> }
export function defineEffect(definition: EffectDefinition): EffectDefinition {
  if (typeof definition.component !== "function") throw new TypeError("Effect requires a React component");
  return definition;
}
