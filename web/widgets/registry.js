const WIDGET_RENDERERS = new Map();

export async function loadWidgetCatalog(entries) {
  if (!Array.isArray(entries)) throw new TypeError("widget catalog must be an array.");
  await Promise.all(entries.map(async (entry) => {
    const id = typeof entry?.id === "string" ? entry.id : "";
    const rendererUrl = typeof entry?.renderer === "string" ? entry.renderer : "";
    const expectedActions = entry?.interaction_actions;
    if (!id || !rendererUrl || !Array.isArray(expectedActions)) {
      throw new TypeError("widget catalog entry requires id, renderer, and interaction_actions.");
    }
    if (!expectedActions.every((action) => typeof action === "string" && action)) {
      throw new TypeError(`widget '${id}' interaction_actions must contain non-empty strings.`);
    }
    if (WIDGET_RENDERERS.has(id)) return;
    const module = await import(rendererUrl);
    if (typeof module.render !== "function") {
      throw new TypeError(`widget '${id}' renderer must export render().`);
    }
    if (!Array.isArray(module.interactionActions)
      || module.interactionActions.length !== expectedActions.length
      || module.interactionActions.some((action, index) => action !== expectedActions[index])) {
      throw new TypeError(`widget '${id}' interactionActions must match its validated catalog contract.`);
    }
    WIDGET_RENDERERS.set(id, module.render);
  }));
}

export function widgetRendererFor(widgetType) {
  return WIDGET_RENDERERS.get(widgetType) || null;
}
