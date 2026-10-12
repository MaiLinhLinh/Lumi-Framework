export const interactionActions = [];

export function render(component, { anchorsByKey = {} } = {}) {
  const element = document.createElement("output"); element.className = "lumi-widget lumi-widget-answer";
  if (anchorsByKey.answer?.anchor_id) element.dataset.anchorId = anchorsByKey.answer.anchor_id;
  element.textContent = component.state?.visibility === "hidden" ? "?" : (component.props?.value || "");
  return element;
}
