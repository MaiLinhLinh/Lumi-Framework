const EFFECT_HANDLERS = new Map();

export async function loadEffectCatalog(entries) {
  if (!Array.isArray(entries)) throw new TypeError("effect catalog must be an array.");
  await Promise.all(entries.map(async (entry) => {
    const id = typeof entry?.id === "string" ? entry.id : "";
    const handlerUrl = typeof entry?.handler === "string" ? entry.handler : "";
    if (!id || !handlerUrl) throw new TypeError("effect catalog entry requires id and handler.");
    if (EFFECT_HANDLERS.has(id)) return;
    const module = await import(handlerUrl);
    if (typeof module.run !== "function") {
      throw new TypeError(`effect '${id}' handler must export run().`);
    }
    EFFECT_HANDLERS.set(id, module.run);
  }));
}

export function effectHandlerFor(effect) {
  return EFFECT_HANDLERS.get(effect) || null;
}
