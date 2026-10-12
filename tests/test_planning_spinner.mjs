import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
const functions = source.slice(source.indexOf("function showPlanningSurface()"), source.indexOf("function patchSurfaceComponents("))
  + source.slice(source.indexOf("function handleMessage(event)"), source.indexOf("function pcm16k("));

// Minimal DOM/renderer doubles exercise production event handling without providers.
class Element {
  constructor() {
    this.children = [];
    this.hidden = false;
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.classList = { add() {}, remove() {} };
  }
  append(...children) {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parent = null;
    this.children = [];
    this.append(...children);
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener() {}
  removeEventListener() {}
  querySelectorAll() { return []; }
}

function browserState({ existingPanel = false } = {}) {
  const host = new Element();
  host.shadowRoot = new Element();
  const oldContent = existingPanel ? new Element() : null;
  if (oldContent) host.shadowRoot.append(oldContent);
  const weatherView = new Element();
  weatherView.append(host);
  const context = vm.createContext({
    console: { info() {} }, ArrayBuffer, document: { createElement: () => new Element() },
    contentPanel: new Element(), weatherView, welcome: new Element(), workspace: new Element(),
    contentTitle: new Element(), templateHost: host, voiceStatus: new Element(), avatar: new Element(),
    animationController: { clear() {} }, planningSurfaceElements: null, panelInteractionRoot: null,
    activePanelContent: oldContent, activePanelPayload: null, activePanelRevision: null, activePanelSurfaceId: null,
    socketReady: true, recording: false, socket: null,
    sendPanelInteraction() {}, whenExtensionCatalogReady: async () => {},
    patchSurfaceComponents: () => true, hiddenComponentIdsInCurrentSurface: () => new Set(),
    appendSurfaceDocumentCoreStyles: () => new Element(), appendWidgetExtensionStyles: () => [],
    appendEffectExtensionStyles() {}, renderSurfaceDocument: () => new Element(),
    reportVisualDiagnostic() {}, reportChoiceDomMetrics() {}, reportRuntimeDiagnostic() {},
    choicePayloadSummary: () => ({}),
    fitPresentationToHost() {}, checkRenderedLayout() {}, requestAnimationFrame: (callback) => callback(),
    waitForWidgetStyles: async () => true, confirmSurfaceRendered: async () => {}, stopCapture() {},
  });
  vm.runInContext(functions, context);
  const send = (payload) => context.handleMessage({ data: JSON.stringify(payload) });
  return { context, send, oldContent, host, weatherView };
}

test("planning shows an animated status without replacing the existing panel", () => {
  const { context, send, oldContent, host, weatherView } = browserState({ existingPanel: true });
  send({ type: "surface:planning", status: "started" });
  assert.equal(host.shadowRoot.children[0], oldContent);
  assert.equal(weatherView.children.length, 3);
  const { style, loading } = context.planningSurfaceElements;
  assert.match(style.textContent, /animation: lumi-planning-spin/);
  assert.equal(loading.attributes.role, "status");
  assert.equal(loading.children[0].className, "lumi-planning-spinner");
  assert.match(loading.children[1].textContent, /Đang chuẩn bị nội dung/);
  send({ type: "surface:planning", status: "stopped" });
  assert.equal(context.planningSurfaceElements, null);
  assert.equal(host.shadowRoot.children[0], oldContent);
  assert.equal(context.contentPanel.hidden, false);
});

test("Draft replaces spinner and a later planned tool result leaves Draft intact", async () => {
  const { context, send, host } = browserState();
  send({ type: "surface:planning", status: "started" });
  send({ type: "panel", panel: {
    ui_type: "surface_document", surface: { surface_id: "draft_1", revision: 1, components: [] },
  } });
  await new Promise(setImmediate);
  assert.equal(context.planningSurfaceElements, null);
  const draftContent = context.activePanelContent;
  assert.ok(draftContent);
  assert.ok(host.shadowRoot.children.includes(draftContent));
  send({ type: "surface:planning", status: "stopped" });
  send({ type: "tool_result", name: "request_task_manager", response: { status: "planned" } });
  assert.equal(context.activePanelContent, draftContent);
  assert.equal(context.contentPanel.hidden, false);
  assert.equal(context.planningSurfaceElements, null);
});

test("a previous run panel update cannot remove the new planning spinner", async () => {
  const { context, send } = browserState({ existingPanel: true });
  send({ type: "surface:planning", status: "started" });
  const spinner = context.planningSurfaceElements;
  send({ type: "panel_update", panel: {
    ui_type: "surface_document", surface: { surface_id: "old_1", revision: 2, components: [] },
  } });
  await new Promise(setImmediate);
  assert.equal(context.planningSurfaceElements, spinner);
});

test("planning rejection before the first Draft restores the welcome view", () => {
  const { context, send, weatherView } = browserState();
  send({ type: "surface:planning", status: "started" });
  send({ type: "surface:planning", status: "stopped" });
  send({ type: "tool_result", name: "request_task_manager", response: { status: "rejected" } });
  assert.equal(context.planningSurfaceElements, null);
  assert.equal(weatherView.children.length, 1);
  assert.equal(context.contentPanel.hidden, true);
  assert.equal(context.welcome.hidden, false);
});

test("Live errors and timeouts clear a waiting spinner", () => {
  for (const type of ["live:error", "live:timeout"]) {
    const { context, send } = browserState({ existingPanel: true });
    send({ type: "surface:planning", status: "started" });
    send({ type, message: "test failure" });
    assert.equal(context.planningSurfaceElements, null);
    assert.equal(context.contentPanel.hidden, false);
  }
});
