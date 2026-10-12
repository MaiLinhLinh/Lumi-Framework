export const interactionActions = [];

export function render(component, { anchorsByKey = {} } = {}) {
  const element = document.createElement("p");
  const role = component.props?.role || "body";
  const content = component.state?.visibility === "hidden" ? "Nội dung đang ẩn" : String(component.props?.content || "");
  element.className = `lumi-widget lumi-widget-text lumi-widget-text-${role}`;
  if (/^.{1,3}$/u.test(content.trim())) element.classList.add("lumi-widget-text-token");
  if (anchorsByKey.text?.anchor_id) element.dataset.anchorId = anchorsByKey.text.anchor_id;
  element.textContent = content;
  installTextFit(element);
  return element;
}

function installTextFit(element) {
  let scheduled = false;
  const scheduleFit = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; fitTextToBounds(element); });
  };
  new ResizeObserver(scheduleFit).observe(element);
  scheduleFit();
}

function fitTextToBounds(element) {
  element.style.removeProperty("font-size");
  if (!overflows(element)) return;
  const naturalSize = Number.parseFloat(getComputedStyle(element).fontSize);
  if (!Number.isFinite(naturalSize) || naturalSize <= 0) return;
  let low = minimumFontSize(element), high = naturalSize, best = low;
  element.style.fontSize = `${low}px`;
  if (!overflows(element)) {
    for (let step = 0; step < 9; step += 1) {
      const candidate = (low + high) / 2;
      element.style.fontSize = `${candidate}px`;
      if (overflows(element)) high = candidate;
      else { best = candidate; low = candidate; }
    }
  }
  element.style.fontSize = `${best.toFixed(2)}px`;
}

function overflows(element) { return element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1; }
function minimumFontSize(element) {
  if (element.classList.contains("lumi-widget-text-title")) return 14;
  if (element.classList.contains("lumi-widget-text-subtitle")) return 12;
  if (element.classList.contains("lumi-widget-text-label")) return 11;
  if (element.classList.contains("lumi-widget-text-token")) return 14;
  return 12;
}
