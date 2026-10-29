import { liveClient, runtimeEvents } from "../services/live";
import { reportDiagnostic } from "../services/diagnostics";
import { createLumiLibrary, loadWidgetCatalog, type WidgetModules } from "./library";
import type { SlotRuntimeBinding, SurfaceResources } from "../runtime/widget";

export interface OpenUIDocument {
  ui_type: "openui_lang";
  surface_id: string;
  revision: number;
  source: string;
  is_streaming: boolean;
  stream_id?: string;
  sequence?: number;
  slots: Record<string, SlotRuntimeBinding>;
  resources: SurfaceResources;
}

interface SurfaceSnapshot {
  document: OpenUIDocument | null;
  modules: WidgetModules;
  styles: string[];
  library: ReturnType<typeof createLumiLibrary> | null;
}

class SurfaceStore {
  private value: SurfaceSnapshot = { document: null, modules: new Map(), styles: [], library: null };
  private listeners = new Set<() => void>();
  private catalogKey = "";
  private catalogGeneration = 0;
  private pendingDocument: OpenUIDocument | null = null;
  private revisions = new Map<string, number>();
  private renderedDocument: OpenUIDocument | null = null;
  private committedDocument: OpenUIDocument | null = null;

  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = () => this.value;
  private update(patch: Partial<SurfaceSnapshot>) { this.value = { ...this.value, ...patch }; this.listeners.forEach(listener => listener()); }

  async receive(payload: Record<string, any>) {
    if (payload.type === "extensions:catalog") {
      const entries = payload.catalog?.widgets;
      if (!Array.isArray(entries)) return;
      const key = JSON.stringify(entries);
      if (key === this.catalogKey) return;
      const generation = ++this.catalogGeneration;
      try {
        const modules = await loadWidgetCatalog(entries);
        const library = createLumiLibrary(modules);
        if (generation !== this.catalogGeneration) return;
        this.catalogKey = key;
        this.update({ modules, library, styles: entries.map(entry => entry.styles).filter(Boolean) });
        if (this.pendingDocument) { const document = this.pendingDocument; this.pendingDocument = null; this.accept(document); }
      } catch (error) { reportDiagnostic("extension_catalog_unavailable", String(error)); }
    }
    if (payload.type === "openui:stream_start") this.startStream(payload);
    if (payload.type === "openui:stream_delta") this.appendDelta(payload);
    if (payload.type === "openui:stream_abort") this.abortStream(payload);
    if (payload.type === "openui:document") this.accept(payload.document);
    if (payload.type === "panel_clear") this.clear(payload);
  }

  private startStream(payload: Record<string, any>) {
    if (typeof payload.stream_id !== "string" || !payload.stream_id || payload.mode !== "create") return;
    const preview: OpenUIDocument = {
      ui_type: "openui_lang",
      surface_id: `preview:${payload.stream_id}`,
      revision: 0,
      source: "",
      is_streaming: true,
      stream_id: payload.stream_id,
      sequence: 0,
      slots: {},
      resources: payload.resources && typeof payload.resources === "object"
        ? payload.resources as SurfaceResources
        : { assets: {}, media: {} },
    };
    this.renderedDocument = null;
    this.update({ document: preview });
    runtimeEvents.dispatchEvent(new CustomEvent("presentation:clear"));
  }

  private appendDelta(payload: Record<string, any>) {
    const current = this.value.document;
    if (!current?.is_streaming || current.stream_id !== payload.stream_id || typeof payload.delta !== "string") return;
    const sequence = Number(payload.sequence);
    if (!Number.isInteger(sequence) || sequence !== (current.sequence || 0) + 1) {
      reportDiagnostic("openui_stream_sequence", JSON.stringify({ expected: (current.sequence || 0) + 1, received: payload.sequence }));
      return;
    }
    this.update({ document: { ...current, source: current.source + payload.delta, sequence } });
  }

  private abortStream(payload: Record<string, any>) {
    const current = this.value.document;
    if (!current?.is_streaming || current.stream_id !== payload.stream_id) return;
    this.update({ document: this.committedDocument });
  }

  private accept(document: OpenUIDocument) {
    if (document?.ui_type !== "openui_lang" || !document.surface_id || !Number.isInteger(document.revision)
      || document.revision < 1 || typeof document.source !== "string" || !document.source.trim()
      || document.is_streaming || !document.slots || !document.resources) return;
    if (!this.value.library) { this.pendingDocument = document; return; }
    const activeRevision = this.revisions.get(document.surface_id) || 0;
    if (document.revision < activeRevision) return;
    if (document.revision === activeRevision && this.committedDocument?.source === document.source
      && JSON.stringify(this.committedDocument.slots) === JSON.stringify(document.slots)) return;
    this.revisions.set(document.surface_id, document.revision);
    this.renderedDocument = null;
    this.committedDocument = document;
    liveClient.setActiveSurface(document);
    this.update({ document });
    runtimeEvents.dispatchEvent(new CustomEvent("presentation:clear"));
  }

  private clear(payload: Record<string, any>) {
    if (!Number.isInteger(payload.revision) || payload.revision < 1) return;
    const activeId = this.committedDocument?.surface_id || this.pendingDocument?.surface_id;
    if (payload.surface_id && activeId && payload.surface_id !== activeId) return;
    const id = payload.surface_id || activeId;
    if (id && payload.revision < (this.revisions.get(id) || 0)) return;
    if (id) this.revisions.set(id, payload.revision);
    this.pendingDocument = null;
    this.committedDocument = null;
    this.renderedDocument = null;
    liveClient.setActiveSurface(null);
    this.update({ document: null });
    runtimeEvents.dispatchEvent(new CustomEvent("presentation:clear"));
  }

  isCurrent(document: OpenUIDocument) { return this.value.document === document; }
  isRendered(document: OpenUIDocument) { return this.isCurrent(document) && this.renderedDocument === document; }
  markRendered(document: OpenUIDocument) { if (this.isCurrent(document)) this.renderedDocument = document; }
  interaction(detail: { anchor_id: string; action: string }) {
    const document = this.committedDocument;
    if (!document || !detail.action || !Object.values(document.slots).some(slot => Object.values(slot.anchors).includes(detail.anchor_id))) return;
    liveClient.send({ type: "panel:interaction", surface_id: document.surface_id, ...detail, revision: document.revision });
  }
  diagnose(document: OpenUIDocument, error_type: string, observed: Record<string, unknown>) {
    if (!this.isCurrent(document) || document.is_streaming) return;
    liveClient.send({ type: "runtime:diagnostic", surface_id: document.surface_id, revision: document.revision,
      error_type, observed, repair_scope: "surface_plan" });
  }
}

export const surfaceStore = new SurfaceStore();
runtimeEvents.addEventListener("backend:event", event => { void surfaceStore.receive((event as CustomEvent).detail); });
