import type { EffectContext, EffectDefinition, VisualCommand } from "./effect";
import type { OpenUIDocument } from "../surface/store";
import { flushSync } from "react-dom";

export interface ActiveEffect { definition: EffectDefinition; context: EffectContext; command: VisualCommand; serial: number }
interface ControllerOptions {
  templateHost(): HTMLElement | null;
  viewport(): HTMLElement | null;
  overlay(): SVGSVGElement | null;
  surface(): OpenUIDocument | null;
  ready(surface: OpenUIDocument): boolean;
  effect(id: string): EffectDefinition | undefined;
  speaking(): void;
  diagnostic(phase: string, detail: Record<string, unknown>): void;
}

/** Same queue/audio clock/timer ownership as the original AnimationController. */
export class AnimationController {
  private pendingScene: VisualCommand | null = null;
  private scheduledTimers = new Map<ReturnType<typeof setTimeout>, string | null>();
  private active: ActiveEffect | null = null;
  private serial = 0;
  private cancelled = new Set<string>();
  private listeners = new Set<() => void>();
  constructor(private options: ControllerOptions) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = () => this.active;
  private publish(value: ActiveEffect | null) { this.active = value; this.listeners.forEach(listener => listener()); }
  clearActiveEffect(turnId: string | null = null) {
    if (turnId && this.active?.command.turn_id !== turnId) return;
    if (this.active) this.publish(null);
  }
  cancelScheduledEffects(turnId: string | null = null) {
    for (const [timer, timerTurnId] of this.scheduledTimers) {
      if (turnId && turnId !== timerTurnId) continue;
      clearTimeout(timer); this.scheduledTimers.delete(timer);
    }
  }
  cancelTurn(turnId: string) {
    if (!turnId) return;
    this.cancelled.add(turnId);
    if (this.cancelled.size > 128) this.cancelled.delete(this.cancelled.values().next().value!);
    if (this.pendingScene?.turn_id === turnId) this.pendingScene = null;
    this.cancelScheduledEffects(turnId); this.clearActiveEffect(turnId);
  }
  clear() { this.cancelScheduledEffects(); this.pendingScene = null; this.clearActiveEffect(); }
  private current(command: VisualCommand) {
    const surface = this.options.surface();
    return Boolean(surface && command && typeof command.anchor_id === "string" && typeof command.effect === "string"
      && Number.isInteger(command.panel_revision) && surface.revision === command.panel_revision
      && (!command.panel_id || command.panel_id === surface.surface_id)
      && (!command.turn_id || !this.cancelled.has(command.turn_id)));
  }
  queue(command: VisualCommand) { if (this.current(command)) this.pendingScene = command; }
  schedule(command: VisualCommand) {
    this.clear();
    if (!this.current(command)) return;
    this.arm(command, Math.max(0, Number(command.animation_delay_ms) || 0));
  }
  armAtAudioStart(audioStartAt: number, audioContext: Pick<AudioContext, "currentTime">) {
    if (!this.pendingScene || !audioContext) return;
    const scene = this.pendingScene; this.pendingScene = null;
    if (!this.current(scene) || !Number.isFinite(audioStartAt)) return;
    const delay = Math.max(0, (audioStartAt - audioContext.currentTime) * 1000)
      + Math.max(0, Number(scene.animation_delay_ms) || 0);
    this.arm(scene, delay);
    this.options.diagnostic("armed_at_audio_start", { anchor_id: scene.anchor_id, effect: scene.effect, delay_ms: Math.round(delay) });
  }
  private arm(command: VisualCommand, delay: number) {
    const timer = setTimeout(() => {
      this.scheduledTimers.delete(timer);
      if (this.current(command)) this.play(command);
    }, delay);
    this.scheduledTimers.set(timer, command.turn_id || null);
  }
  play(command: VisualCommand) {
    if (!this.current(command)) return;
    // React must run the previous target hook's cleanup before measuring the
    // next target, just as the original synchronous cleanupEffect() did.
    if (this.active) flushSync(() => this.clearActiveEffect());
    const surface = this.options.surface()!;
    const root = this.options.templateHost()?.shadowRoot;
    const target = root?.querySelector(`[data-anchor-id="${CSS.escape(command.anchor_id)}"]`);
    const knownAnchor = Object.values(surface.slots).some(slot => Object.values(slot.anchors).includes(command.anchor_id));
    if (!target?.isConnected || !knownAnchor) {
      this.options.diagnostic("target_missing", { anchor_id: command.anchor_id, effect: command.effect }); return;
    }
    const definition = this.options.effect(command.effect);
    if (!definition) {
      this.options.diagnostic("effect_unimplemented", { anchor_id: command.anchor_id, effect: command.effect }); return;
    }
    if (!this.options.ready(surface) || target.closest('[data-visibility="hidden"]')) {
      this.options.diagnostic("target_not_ready", { anchor_id: command.anchor_id, effect: command.effect }); return;
    }
    const overlay = this.options.overlay(), viewport = this.options.viewport();
    if (!overlay || !viewport) return;
    const box = target.getBoundingClientRect(), bounds = viewport.getBoundingClientRect();
    const rect = { x: box.left - bounds.left, y: box.top - bounds.top, width: box.width, height: box.height };
    this.options.speaking();
    this.options.diagnostic("effect_started", { anchor_id: command.anchor_id, effect: command.effect, rect });
    this.publish({ definition, context: { target, overlay, rect }, command, serial: ++this.serial });
  }
  validateTarget() {
    if (this.active && (!this.active.context.target.isConnected || !this.current(this.active.command))) this.clear();
  }
}
