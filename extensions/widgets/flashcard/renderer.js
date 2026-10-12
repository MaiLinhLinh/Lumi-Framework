export const interactionActions = ["flip"];

export function render(component, { anchorsByKey = {}, emitInteraction = () => {} } = {}) {
  const card = document.createElement("div");
  card.className = "lumi-widget lumi-widget-flashcard";
  card.setAttribute("role", "button");
  card.tabIndex = 0;
  card.setAttribute("aria-label", "Lật thẻ từ vựng");
  card.setAttribute("aria-pressed", component.state?.flipped === true ? "true" : "false");
  const anchorId = String(anchorsByKey.card?.anchor_id || "");
  if (anchorId) card.dataset.anchorId = anchorId;

  if (component.state?.visibility === "hidden") {
    card.classList.add("lumi-widget-hidden-content");
    const placeholder = document.createElement("span");
    placeholder.className = "lumi-widget-hidden-placeholder";
    placeholder.textContent = "Nội dung đang ẩn";
    card.append(placeholder);
    return card;
  }

  if (component.state?.flipped === true) card.classList.add("is-flipped");
  const flip = () => {
    if (anchorId) emitInteraction({ anchor_id: anchorId, action: "flip" });
  };
  card.addEventListener("click", flip);
  card.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    flip();
  });

  const inner = document.createElement("div");
  inner.className = "lumi-widget-flashcard-inner";
  inner.append(renderFront(component.props?.front || {}), renderBack(component.props?.back || {}));
  card.append(inner);
  return card;
}

function renderFront(front) {
  const face = document.createElement("section");
  face.className = "lumi-widget-flashcard-face lumi-widget-flashcard-front";
  const image = document.createElement("img");
  image.src = front.source?.url || (typeof front.asset_url === "string" ? front.asset_url : "");
  image.alt = front.source?.caption || "";
  image.draggable = false;
  const text = document.createElement("p");
  text.textContent = typeof front.text === "string" ? front.text : "";
  face.append(image, text);
  return face;
}

function renderBack(back) {
  const face = document.createElement("section");
  face.className = "lumi-widget-flashcard-face lumi-widget-flashcard-back";
  const word = document.createElement("strong");
  word.textContent = typeof back.word === "string" ? back.word : "";
  const phonetic = document.createElement("span");
  phonetic.textContent = typeof back.phonetic === "string" ? back.phonetic : "";
  const meaning = document.createElement("p");
  meaning.textContent = typeof back.meaning === "string" ? back.meaning : "";
  face.append(word, phonetic, meaning);
  return face;
}
