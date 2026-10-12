export const interactionActions = ["select"];

export function render(component, { anchorsByKey = {}, renderChild = null, emitInteraction = () => {} } = {}) {
  const choice = document.createElement("div"); choice.className = "lumi-widget lumi-widget-choice"; choice.setAttribute("role", "button"); choice.tabIndex = 0; choice.setAttribute("aria-label", "Lựa chọn");
  const anchorId = String(anchorsByKey.choice?.anchor_id || ""); if (anchorId) choice.dataset.anchorId = anchorId;
  const select = () => { if (anchorId) emitInteraction({ anchor_id: anchorId, action: "select" }); };
  choice.addEventListener("click", select);
  choice.addEventListener("keydown", (event) => { if (event.key !== "Enter" && event.key !== " ") return; event.preventDefault(); select(); });
  const content = document.createElement("div"); content.className = "lumi-widget-choice-content";
  for (const child of (Array.isArray(component.children) ? component.children : [])) { const node = renderChild?.(child); if (!node) continue; node.classList.add("lumi-widget-choice-child"); content.append(node); }
  choice.append(content); return choice;
}
