export const interactionActions = [];

export function render(component, { anchorsByKey = {} } = {}) {
  const container = document.createElement("section");
  container.className = "lumi-widget lumi-widget-object-group";

  if (anchorsByKey.group?.anchor_id) {
    container.dataset.anchorId = anchorsByKey.group.anchor_id;
  }

  if (component.state?.visibility === "hidden") {
    container.classList.add("lumi-widget-hidden-content");

    const placeholder = document.createElement("p");
    placeholder.className = "lumi-widget-hidden-placeholder";
    placeholder.textContent = "Nội dung đang ẩn";

    container.append(placeholder);
    return container;
  }

  if (component.props?.label) {
    const label = document.createElement("p");
    label.className = "lumi-widget-object-group-label";
    label.textContent = component.props.label;
    container.append(label);
  }

  const items = document.createElement("div");
  items.className = "lumi-widget-object-group-items";

  const count = Number(component.props?.count || 0);
  const mediaUrl = component.props?.source?.url;

  // media_id is an already-composed image of the complete group. The count
  // remains semantic data and must not cause this image to be duplicated.
  if (typeof mediaUrl === "string" && mediaUrl) {
    items.classList.add("lumi-widget-object-group-items-composite");

    const image = document.createElement("img");
    image.src = mediaUrl;
    image.alt = component.props?.label || component.props?.source?.caption || "";
    image.draggable = false;

    items.append(image);
    container.append(items);
    return container;
  }

  // 1–4 vật: xếp một hàng để ảnh dọc không bị quá bé.
  // Từ 5 vật: dùng lưới gần vuông.
  const columns = count <= 4 ? Math.max(1, count) : Math.ceil(Math.sqrt(count));
  const rows = Math.max(1, Math.ceil(count / columns));

  items.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
  items.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;

  for (let index = 0; index < count; index += 1) {
    const image = document.createElement("img");
    image.src = component.props?.asset_url || "";
    image.alt = component.props?.label || "";
    image.draggable = false;

    const anchor = anchorsByKey[`item_${index + 1}`];
    if (anchor?.anchor_id) {
      image.dataset.anchorId = anchor.anchor_id;
    }

    items.append(image);
  }

  container.append(items);
  return container;
}
