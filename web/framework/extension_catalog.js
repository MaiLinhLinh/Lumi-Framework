import { loadEffectCatalog } from "/assets/presentation/effects/registry.js";
import { loadWidgetCatalog } from "/assets/widgets/registry.js";

let catalogReady = Promise.reject(new Error("Extension Catalog has not arrived."));
catalogReady.catch(() => {});
let widgetStyleUrls = [];
let effectStyleUrls = [];

export function receiveExtensionCatalog(catalog) {
  const widgets = Array.isArray(catalog?.widgets) ? catalog.widgets : null;
  const effects = Array.isArray(catalog?.effects) ? catalog.effects : null;
  if (!widgets || !effects) throw new TypeError("Extension Catalog requires widgets and effects arrays.");
  widgetStyleUrls = [...new Set(widgets.map((entry) => entry?.styles).filter((url) => typeof url === "string"))];
  effectStyleUrls = [...new Set(effects.map((entry) => entry?.styles).filter((url) => typeof url === "string"))];
  catalogReady = Promise.all([loadWidgetCatalog(widgets), loadEffectCatalog(effects)]).then(() => undefined);
  return catalogReady;
}

export function whenExtensionCatalogReady() {
  return catalogReady;
}

export function appendWidgetExtensionStyles(root) {
  const styles = [];
  for (const href of widgetStyleUrls) {
    const existing = root.querySelector(`link[data-lumi-extension-style="${CSS.escape(href)}"]`);
    if (existing) {
      styles.push(existing);
      continue;
    }
    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = href;
    style.dataset.lumiExtensionStyle = href;
    root.append(style);
    styles.push(style);
  }
  return styles;
}

export function appendEffectExtensionStyles(root = document) {
  const documentRoot = root instanceof Document ? root : root.ownerDocument;
  const container = root instanceof Document ? root.head : root;
  const styles = [];
  for (const href of effectStyleUrls) {
    const existing = container.querySelector(`link[data-lumi-effect-style="${CSS.escape(href)}"]`);
    if (existing) {
      styles.push(existing);
      continue;
    }
    const style = documentRoot.createElement("link");
    style.rel = "stylesheet";
    style.href = href;
    style.dataset.lumiEffectStyle = href;
    container.append(style);
    styles.push(style);
  }
  return styles;
}
