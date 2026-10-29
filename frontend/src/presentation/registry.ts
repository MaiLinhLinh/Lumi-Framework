import { runtimeEvents } from "../services/live";
import { reportDiagnostic } from "../services/diagnostics";
import type { EffectDefinition } from "./effect";

interface CatalogSnapshot {
  key: string; modules: ReadonlyMap<string, EffectDefinition>; styles: string[];
  documentReady: boolean; targetReady: boolean;
}
class EffectRegistry {
  private value: CatalogSnapshot = { key: "", modules: new Map(), styles: [], documentReady: false, targetReady: false };
  private generation = 0;
  private listeners = new Set<() => void>();
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = () => this.value;
  private update(value: CatalogSnapshot) { this.value = value; this.listeners.forEach(listener => listener()); }
  get(id: string) { return this.value.modules.get(id); }
  ready() { return Boolean(this.value.key && this.value.documentReady && this.value.targetReady); }
  async load(entries: any[]) {
    if (!Array.isArray(entries)) return;
    const key = JSON.stringify(entries);
    if (key === this.value.key) return;
    const generation = ++this.generation;
    runtimeEvents.dispatchEvent(new Event("presentation:clear"));
    this.update({ key: "", modules: new Map(), styles: [], documentReady: false, targetReady: false });
    try {
      const modules = new Map<string, EffectDefinition>();
      await Promise.all(entries.map(async entry => {
        if (!/^[a-z]+(?:_[a-z]+)*$/.test(entry.id) || !entry.handler?.startsWith("/assets/") || (entry.styles && !entry.styles.startsWith("/assets/"))) throw Error("Invalid effect catalog");
        const module = await import(/* @vite-ignore */ entry.handler);
        if (typeof module.effect?.component !== "function") throw Error(`Invalid React effect ${entry.id}`);
        if (modules.has(entry.id)) throw Error(`Duplicate effect ${entry.id}`);
        modules.set(entry.id, module.effect);
      }));
      if (generation !== this.generation) return;
      const styles = [...new Set<string>(entries.map(entry => entry.styles).filter(Boolean))];
      this.update({ key, modules, styles, documentReady: !styles.length, targetReady: !styles.length });
    } catch (error) {
      if (generation === this.generation) reportDiagnostic("effect_catalog_unavailable", String(error));
    }
  }
  stylesReady(scope: "documentReady" | "targetReady", key: string, ready: boolean) {
    if (!key || key !== this.value.key || this.value[scope] === ready) return;
    this.update({ ...this.value, [scope]: ready });
  }
}
export const effectRegistry = new EffectRegistry();
runtimeEvents.addEventListener("backend:event", event => {
  const payload = (event as CustomEvent).detail;
  if (payload.type === "extensions:catalog") void effectRegistry.load(payload.catalog?.effects);
});
