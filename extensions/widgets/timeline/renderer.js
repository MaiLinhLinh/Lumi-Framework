export const interactionActions = [];

export function render(component, { anchorsByKey = {} } = {}) {
  const timeline = document.createElement("section");
  timeline.className = "lumi-widget lumi-widget-timeline";
  if (component.state?.visibility === "hidden") {
    timeline.classList.add("lumi-widget-hidden-content");
    timeline.textContent = "Nội dung đang ẩn";
    return timeline;
  }
  const title = String(component.props?.title || "").trim();
  if (title) {
    const heading = document.createElement("h3");
    heading.className = "lumi-widget-timeline-title";
    heading.textContent = title;
    timeline.append(heading);
  }
  const milestones = document.createElement("ol");
  milestones.className = "lumi-widget-timeline-items";
  for (const [index, item] of (Array.isArray(component.props?.items) ? component.props.items : []).entries()) {
    const milestone = document.createElement("li");
    milestone.className = "lumi-widget-timeline-item";
    const anchor = anchorsByKey[`milestone_${index + 1}`];
    if (anchor?.anchor_id) milestone.dataset.anchorId = anchor.anchor_id;
    for (const [className, tag, value] of [
      ["lumi-widget-timeline-label", "p", item?.label],
      ["lumi-widget-timeline-item-title", "h4", item?.title],
      ["lumi-widget-timeline-description", "p", item?.description],
    ]) {
      const element = document.createElement(tag);
      element.className = className;
      element.textContent = String(value || "");
      milestone.append(element);
    }
    milestones.append(milestone);
  }
  timeline.append(milestones);
  return timeline;
}
