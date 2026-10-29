import { createContext, useContext, type HTMLAttributes, type ReactNode } from "react";

export interface RuntimeComponent {
  id?: string;
  type: string;
  props: Record<string, any>;
  state?: Record<string, any>;
}

export interface SlotRuntimeBinding {
  component_id: string;
  anchors: Record<string, string>;
  state: Record<string, any>;
}

export interface SurfaceResources {
  assets: Record<string, string>;
  media: Record<string, { url: string; caption?: string; source_url?: string; mime_type?: string }>;
}

export interface SurfaceRuntimeValue {
  surfaceId: string;
  revision: number;
  slots: Record<string, SlotRuntimeBinding>;
  resources: SurfaceResources;
  emitInteraction(event: { anchor_id: string; action: string }): void;
}

interface SlotRuntimeValue {
  statementId: string;
  grid: { col: number; row: number; col_span: number; row_span: number };
  description?: string | null;
  initialState: Record<string, any>;
}

export interface WidgetRuntime {
  component: RuntimeComponent;
  anchorsByKey: Record<string, { anchor_id: string }>;
  rootProps: HTMLAttributes<HTMLElement> & {
    "data-component-id"?: string;
    "data-visibility"?: string;
  };
  emitInteraction(event: { anchor_id: string; action: string }): void;
}

const SurfaceRuntimeContext = createContext<SurfaceRuntimeValue | null>(null);
const SlotRuntimeContext = createContext<SlotRuntimeValue | null>(null);
const WidgetDepthContext = createContext(0);
export const WidgetContext = createContext<WidgetRuntime | null>(null);

export function SurfaceRuntimeProvider({ value, children }: { value: SurfaceRuntimeValue; children: ReactNode }) {
  return <SurfaceRuntimeContext.Provider value={value}>{children}</SurfaceRuntimeContext.Provider>;
}

export function SlotRuntimeProvider({ statementId, grid, description, initialState, children }: SlotRuntimeValue & { children: ReactNode }) {
  return <SlotRuntimeContext.Provider value={{ statementId, grid, description, initialState }}>
    <WidgetDepthContext.Provider value={0}>{children}</WidgetDepthContext.Provider>
  </SlotRuntimeContext.Provider>;
}

function materialize(value: any, resources: SurfaceResources): any {
  if (Array.isArray(value)) return value.map(item => materialize(item, resources));
  if (!value || typeof value !== "object") return value;
  const copy = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, materialize(item, resources)]));
  if (typeof copy.asset_id === "string") copy.asset_url = resources.assets[copy.asset_id] || "";
  if (typeof copy.media_id === "string" && resources.media[copy.media_id]) copy.source = resources.media[copy.media_id];
  return copy;
}

export function WidgetRuntimeBridge({ type, props, children }: {
  type: string;
  props: Record<string, any>;
  children: ReactNode;
}) {
  const surface = useContext(SurfaceRuntimeContext);
  const slot = useContext(SlotRuntimeContext);
  const depth = useContext(WidgetDepthContext);
  if (!surface || !slot) throw new Error("Lumi OpenUI widget requires surface and Slot runtime context");
  const root = depth === 0;
  const binding = root ? surface.slots[slot.statementId] : undefined;
  const componentId = binding?.component_id || (root ? slot.statementId : undefined);
  const state = root ? (binding?.state || slot.initialState) : { visibility: "visible" };
  const anchorsByKey = Object.fromEntries(Object.entries(binding?.anchors || {}).map(([key, anchor_id]) => [key, { anchor_id }]));
  const runtime: WidgetRuntime = {
    component: { id: componentId, type, props: materialize(props, surface.resources), state },
    anchorsByKey,
    rootProps: root ? {
      "data-component-id": componentId,
      "data-visibility": state.visibility || "visible",
      style: {
        gridColumn: `${slot.grid.col} / span ${slot.grid.col_span}`,
        gridRow: `${slot.grid.row} / span ${slot.grid.row_span}`,
      },
    } : {},
    emitInteraction: event => {
      if (root && binding && Object.values(binding.anchors).includes(event.anchor_id)) surface.emitInteraction(event);
    },
  };
  return <WidgetContext.Provider value={runtime}>
    <WidgetDepthContext.Provider value={depth + 1}>{children}</WidgetDepthContext.Provider>
  </WidgetContext.Provider>;
}

export function PendingRuntimeView({ widgetId }: { widgetId: string }) {
  const surface = useContext(SurfaceRuntimeContext);
  const slot = useContext(SlotRuntimeContext);
  if (!surface || !slot) return null;
  const binding = surface.slots[slot.statementId];
  const firstAnchor = Object.values(binding?.anchors || {})[0];
  return <section
    className="lumi-draft-placeholder"
    data-component-id={binding?.component_id || slot.statementId}
    data-visibility={binding?.state?.visibility || slot.initialState.visibility || "visible"}
    data-anchor-id={firstAnchor}
    style={{
      gridColumn: `${slot.grid.col} / span ${slot.grid.col_span}`,
      gridRow: `${slot.grid.row} / span ${slot.grid.row_span}`,
    }}
    role="status"
    aria-live="polite"
  >
    <strong>Đang tìm dữ liệu</strong>
    <span>{slot.description || widgetId}</span>
  </section>;
}

export function useWidgetRuntime() {
  const value = useContext(WidgetContext);
  if (!value) throw new Error("Lumi widget requires runtime binding");
  return value;
}

export function useWidgetRoot(anchorKey?: string, className = "") {
  const runtime = useWidgetRuntime();
  return {
    ...runtime.rootProps,
    className: [className, runtime.rootProps.className].filter(Boolean).join(" "),
    "data-anchor-id": anchorKey ? runtime.anchorsByKey[anchorKey]?.anchor_id : undefined,
  };
}

export function Hidden({ paragraph = false }: { paragraph?: boolean }) {
  const Tag = paragraph ? "p" : "span";
  return <Tag className="lumi-widget-hidden-placeholder">Nội dung đang ẩn</Tag>;
}
