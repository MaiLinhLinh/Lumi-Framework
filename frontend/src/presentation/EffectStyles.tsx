import { useLayoutEffect, useRef, useSyncExternalStore } from "react";
import { effectRegistry } from "./registry";
import { reportDiagnostic } from "../services/diagnostics";

/** Overlay CSS lives in the app; target classes also need CSS inside Shadow DOM. */
export function EffectStyles({ scope }: { scope: "documentReady" | "targetReady" }) {
  const catalog = useSyncExternalStore(effectRegistry.subscribe, effectRegistry.getSnapshot);
  const links = useRef(new Map<string, HTMLLinkElement>());
  const publish = () => effectRegistry.stylesReady(scope, catalog.key, catalog.styles.every(url => Boolean(links.current.get(url)?.sheet)));
  useLayoutEffect(publish, [catalog.key, scope]);
  return <>{catalog.styles.map(url => <link key={url} ref={node => { if (node) links.current.set(url, node); else links.current.delete(url); }} rel="stylesheet" href={url} onLoad={publish} onError={() => {
    effectRegistry.stylesReady(scope, catalog.key, false);
    reportDiagnostic("effect_styles_failed", JSON.stringify({ url, scope }));
  }}/>)}</>;
}
