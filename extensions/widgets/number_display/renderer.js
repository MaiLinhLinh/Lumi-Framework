export const interactionActions = [];

export function render(component, { anchorsByKey = {} } = {}) {
  const element = document.createElement("output"); element.className = "lumi-widget lumi-widget-number-display";
  if (anchorsByKey.number?.anchor_id) element.dataset.anchorId = anchorsByKey.number.anchor_id;
  const value = document.createElement("span");
  value.className = "lumi-widget-number-display-value";
  value.textContent = component.state?.visibility === "hidden" ? "?" : (component.props?.value || "");
  element.append(value);
  return element;
}
