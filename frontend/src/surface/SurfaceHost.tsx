import { useCallback, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Renderer, type OpenUIError, type ParseResult } from "@openuidev/react-lang";
import { SurfaceRuntimeProvider } from "../runtime/widget";
import { surfaceStore, type OpenUIDocument } from "./store";
import { liveClient } from "../services/live";
import { reportDiagnostic } from "../services/diagnostics";
import coreCss from "./surface.css?inline";
import { EffectStyles } from "../presentation/EffectStyles";
import { effectRegistry } from "../presentation/registry";

async function waitImages(root: HTMLElement, document: OpenUIDocument, signal: AbortSignal) {
  return (await Promise.all([...root.querySelectorAll("img")].map(image => new Promise<boolean>(resolve => {
    let retry = false, done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      image.removeEventListener("load", loaded);
      image.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
      image.dispatchEvent(new CustomEvent("lumi:image-final", { detail: { status: ok ? "loaded" : "failed" } }));
      if (!ok && !signal.aborted) surfaceStore.diagnose(document, "image_load_failed", { status: "error", retry_attempts: 1 });
      resolve(ok);
    };
    const loaded = () => finish(true), aborted = () => finish(false);
    const failed = () => {
      if (retry || !image.getAttribute("src")) return finish(false);
      retry = true;
      const src = image.src;
      image.removeAttribute("src");
      queueMicrotask(() => { if (image.isConnected) image.src = src; });
    };
    const timer = setTimeout(() => finish(false), 15000);
    image.addEventListener("load", loaded);
    image.addEventListener("error", failed);
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted(); else if (image.complete) image.naturalWidth > 0 ? loaded() : failed();
  })))).every(Boolean);
}

function NativeSurface({ document, effectsReady }: { document: OpenUIDocument; effectsReady: boolean }) {
  const state = surfaceStore.getSnapshot();
  const root = useRef<HTMLDivElement>(null);
  const [parseReady, setParseReady] = useState(false);
  const onParseResult = useCallback((result: ParseResult | null) => {
    setParseReady(Boolean(result?.root && !result.meta.incomplete && !result.meta.unresolved.length));
  }, []);
  const onError = useCallback((errors: OpenUIError[]) => {
    const material = errors.filter(error => error.severity !== "warning");
    if (material.length && !document.is_streaming) surfaceStore.diagnose(document, "widget_render_failed", {
      errors: material.map(error => ({ source: error.source, message: error.message, statement_id: error.statementId })),
    });
  }, [document]);

  useLayoutEffect(() => {
    if (document.is_streaming || !parseReady || !effectsReady) return;
    const container = root.current!;
    const abort = new AbortController();
    const frame = requestAnimationFrame(() => {
      void waitImages(container, document, abort.signal).then(imagesLoaded => {
        if (!imagesLoaded || abort.signal.aborted || !surfaceStore.isCurrent(document)) return;
        const expected = Object.keys(document.slots).length;
        if (container.querySelectorAll("[data-component-id]").length !== expected) return;
        for (const node of container.querySelectorAll<HTMLElement>("[data-component-id]")) {
          if (node.scrollWidth > node.clientWidth + 1 || node.scrollHeight > node.clientHeight + 1) {
            reportDiagnostic("layout_overflow", JSON.stringify({ component_id: node.dataset.componentId,
              scroll_width: node.scrollWidth, client_width: node.clientWidth,
              scroll_height: node.scrollHeight, client_height: node.clientHeight }));
          }
        }
        surfaceStore.markRendered(document);
        liveClient.send({ type: "surface:rendered", surface_id: document.surface_id, revision: document.revision });
      });
    });
    return () => { cancelAnimationFrame(frame); abort.abort(); };
  }, [document, effectsReady, parseReady]);

  if (!state.library) return null;
  return <div ref={root} style={{ width: "100%", height: "100%" }}>
    <SurfaceRuntimeProvider value={{
      surfaceId: document.surface_id,
      revision: document.revision,
      slots: document.slots,
      resources: document.resources,
      emitInteraction: detail => surfaceStore.interaction(detail),
    }}>
      <Renderer
        library={state.library}
        response={document.source}
        isStreaming={document.is_streaming}
        onParseResult={onParseResult}
        onError={onError}
      >
        {/* Keep a definite height through OpenUI's content slot to the 16×10 grid. */}
        <Renderer.Content style={{ width: "100%", height: "100%", minHeight: 0 }}/>
      </Renderer>
    </SurfaceRuntimeProvider>
  </div>;
}

function Styles({ urls, onReady }: { urls: string[]; onReady(ready: boolean): void }) {
  const loaded = useRef(new Set<string>());
  useLayoutEffect(() => { onReady(urls.every(url => loaded.current.has(url))); }, [urls, onReady]);
  return <>{urls.map(url => <link key={url} rel="stylesheet" href={url}
    onLoad={() => { loaded.current.add(url); onReady(urls.every(item => loaded.current.has(item))); }}
    onError={() => { onReady(false); reportDiagnostic("widget_styles_failed", url); }}/>)}</>;
}

export function SurfaceHost() {
  const state = useSyncExternalStore(surfaceStore.subscribe, surfaceStore.getSnapshot);
  useSyncExternalStore(effectRegistry.subscribe, effectRegistry.getSnapshot);
  const host = useRef<HTMLDivElement>(null);
  const [shadow, setShadow] = useState<ShadowRoot | null>(null), [ready, setReady] = useState(false);
  useLayoutEffect(() => { setShadow(host.current!.shadowRoot || host.current!.attachShadow({ mode: "open" })); }, []);
  return <div id="weatherTemplateHost" ref={host} className="weather-template-host" aria-label="Kết quả trực quan">
    {shadow && createPortal(<>
      <style>{coreCss + "\n[data-anchor-id]{transition:outline .18s,box-shadow .18s,transform .18s}"}</style>
      <Styles urls={state.styles} onReady={setReady}/>
      <EffectStyles scope="targetReady"/>
      {ready && state.document && <NativeSurface document={state.document} effectsReady={effectRegistry.ready()}/>} 
    </>, shadow)}
  </div>;
}
