export const interactionActions = [];

export function render(component, { anchorsByKey = {} } = {}) {
  const figure = document.createElement("figure");
  figure.className = "lumi-widget lumi-widget-image";
  if (anchorsByKey.image?.anchor_id) figure.dataset.anchorId = anchorsByKey.image.anchor_id;
  if (component.state?.visibility === "hidden") {
    figure.classList.add("lumi-widget-hidden-content");
    const placeholder = document.createElement("span");
    placeholder.className = "lumi-widget-hidden-placeholder";
    placeholder.textContent = "Nội dung đang ẩn";
    figure.append(placeholder);
    return figure;
  }
  const image = document.createElement("img");
  image.src = component.props?.source?.url || component.props?.asset_url || "";
  image.alt = component.props?.label || component.props?.source?.caption || "";
  image.draggable = false;
  figure.append(image);
  return figure;
}
