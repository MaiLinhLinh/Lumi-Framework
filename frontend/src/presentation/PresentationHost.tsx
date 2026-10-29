import { Component, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from "react";
import { liveClient, runtimeEvents } from "../services/live";
import { reportDiagnostic } from "../services/diagnostics";
import { surfaceStore } from "../surface/store";
import { effectRegistry } from "./registry";
import { EffectStyles } from "./EffectStyles";
import { AnimationController } from "./controller";
import type { VisualCommand } from "./effect";

class EffectBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { reportDiagnostic("effect_render_failed", String(error)); }
  render() { return this.state.failed ? null : this.props.children; }
}
export function PresentationHost({ viewport }: { viewport: RefObject<HTMLDivElement | null> }) {
  const overlay = useRef<SVGSVGElement>(null);
  const surface = useSyncExternalStore(surfaceStore.subscribe, surfaceStore.getSnapshot);
  const [controller] = useState(() => new AnimationController({
    templateHost: () => viewport.current?.querySelector<HTMLElement>("#weatherTemplateHost") || null,
    viewport: () => viewport.current, overlay: () => overlay.current,
    surface: () => surfaceStore.getSnapshot().document || null,
    ready: value => effectRegistry.ready() && surfaceStore.isRendered(value),
    effect: id => effectRegistry.get(id), speaking: () => liveClient.presentationSpeaking(),
    diagnostic: (phase, detail) => reportDiagnostic(phase, JSON.stringify(detail)),
  }));
  const active = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  useLayoutEffect(() => {
    const handlers: Record<string, (event: Event) => void> = {
      "presentation:marker": event => controller.queue((event as CustomEvent<VisualCommand>).detail),
      "presentation:audio_start": event => { const {time, context} = (event as CustomEvent).detail; controller.armAtAudioStart(time, context); },
      "presentation:cancel_turn": event => controller.cancelTurn((event as CustomEvent<string>).detail),
      "presentation:clear": () => controller.clear(),
    };
    for (const [type, handler] of Object.entries(handlers)) runtimeEvents.addEventListener(type, handler);
    return () => { for (const [type, handler] of Object.entries(handlers)) runtimeEvents.removeEventListener(type, handler); controller.clear(); };
  }, [controller]);
  useLayoutEffect(() => {
    const root = viewport.current?.querySelector("#weatherTemplateHost")?.shadowRoot;
    if (!root) return;
    const observer = new MutationObserver(() => controller.validateTarget());
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [controller, viewport, surface.document]);
  const Effect = active?.definition.component;
  return <><EffectStyles scope="documentReady"/>
    <svg ref={overlay} id="presentationOverlay" className="presentation-overlay" aria-hidden="true">
      {active && Effect && <EffectBoundary key={active.serial}><Effect context={active.context} command={active.command}/></EffectBoundary>}
    </svg>
  </>;
}
