import { effectHandlerFor } from "/assets/presentation/effects/registry.js";

/** Shared visual-cue lifecycle. It is intentionally independent of every domain. */
export class AnimationController {
  constructor({ templateHost, viewport, overlay, avatar, onDiagnostic = null }) {
    this.templateHost = templateHost;
    this.viewport = viewport;
    this.overlay = overlay;
    this.avatar = avatar;
    this.onDiagnostic = typeof onDiagnostic === "function" ? onDiagnostic : () => {};
    this.pendingScene = null;
    this.scheduledTimers = new Map();
    this.cleanupEffect = null;
    this.activeEffectTurnId = null;
  }

  clearActiveEffect(turnId = null) {
    if (turnId && this.activeEffectTurnId !== turnId) return;
    this.cleanupEffect?.();
    this.cleanupEffect = null;
    this.activeEffectTurnId = null;
    this.overlay.replaceChildren();
  }

  cancelScheduledEffects(turnId = null) {
    for (const [timer, timerTurnId] of this.scheduledTimers) {
      if (turnId && timerTurnId !== turnId) continue;
      window.clearTimeout(timer);
      this.scheduledTimers.delete(timer);
    }
  }

  cancelTurn(turnId) {
    if (!turnId) return;
    if (this.pendingScene?.turn_id === turnId) this.pendingScene = null;
    this.cancelScheduledEffects(turnId);
    this.clearActiveEffect(turnId);
  }

  clear() {
    this.cancelScheduledEffects();
    this.pendingScene = null;
    this.clearActiveEffect();
  }

  queue(command) { this.pendingScene = command; }

  schedule(command) {
    this.clear();
    const delay = Math.max(0, Number(command.animation_delay_ms) || 0);
    const timer = window.setTimeout(() => {
      this.scheduledTimers.delete(timer);
      this.avatar.dataset.avatarState = "speaking";
      this.play(command);
    }, delay);
    this.scheduledTimers.set(timer, command.turn_id || null);
    console.info("[GEMINI_LIVE:VISUAL_CUE_SCHEDULED]", {
      anchor_id: command.anchor_id,
      effect: command.effect,
      delay_ms: delay,
    });
  }

  armAtAudioStart(audioStartAt, audioContext) {
    if (!this.pendingScene || !audioContext) return;
    const scene = this.pendingScene;
    this.pendingScene = null;
    const delay = Math.max(0, (audioStartAt - audioContext.currentTime) * 1000)
      + Math.max(0, Number(scene.animation_delay_ms) || 0);
    const timer = window.setTimeout(() => {
      this.scheduledTimers.delete(timer);
      this.avatar.dataset.avatarState = "speaking";
      this.play(scene);
    }, delay);
    this.scheduledTimers.set(timer, scene.turn_id || null);
    console.info("[GEMINI_LIVE:SCENE_ARMED]", { scene, delay_ms: Math.round(delay) });
    this.onDiagnostic("armed_at_audio_start", {
      anchor_id: scene.anchor_id,
      effect: scene.effect,
      anchor_id: scene.anchor_id,
      delay_ms: Math.round(delay),
    });
  }

  play(command) {
    this.clearActiveEffect();
    const root = this.templateHost.shadowRoot;
    const target = root?.querySelector(`[data-anchor-id="${CSS.escape(command.anchor_id)}"]`);
    if (!target) {
      console.warn("[GEMINI_LIVE:UI_TARGET_MISSING]", command);
      this.onDiagnostic("target_missing", {
        anchor_id: command.anchor_id,
        effect: command.effect,
        anchor_id: command.anchor_id,
      });
      return;
    }
    const context = { target, overlay: this.overlay, rect: this.rectFor(target) };
    const handler = effectHandlerFor(command.effect);
    if (!handler) {
      console.warn("[GEMINI_LIVE:UI_EFFECT_UNIMPLEMENTED]", command);
      this.onDiagnostic("effect_unimplemented", {
        anchor_id: command.anchor_id,
        effect: command.effect,
        anchor_id: command.anchor_id,
      });
      return;
    }
    this.onDiagnostic("effect_started", {
      anchor_id: command.anchor_id,
      effect: command.effect,
      anchor_id: command.anchor_id,
      rect: context.rect,
    });
    const cleanup = handler(context, command);
    if (typeof cleanup === "function") {
      this.cleanupEffect = cleanup;
      this.activeEffectTurnId = command.turn_id || null;
    }
  }

  rectFor(target) {
    const root = this.viewport.getBoundingClientRect();
    const box = target.getBoundingClientRect();
    return { x: box.left - root.left, y: box.top - root.top, width: box.width, height: box.height };
  }
}
