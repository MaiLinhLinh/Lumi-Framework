import { AudioPlayback, type AudioMarker } from "./audio";
import { MicrophoneCapture } from "./microphone";
import { reportDiagnostic } from "./diagnostics";

type Payload = Record<string, any>;
export interface Message {
  id: number;
  role: "user" | "trace";
  text: string;
  final?: boolean;
}
export interface LiveSnapshot {
  connection: string;
  voice: string;
  listening: boolean;
  avatar: "idle" | "speaking";
  planning: boolean;
  messages: readonly Message[];
}

/** Shared event bus for OpenUI documents, runtime state and presentation cues. */
export const runtimeEvents = new EventTarget();
function emit(type: string, detail?: unknown) {
  runtimeEvents.dispatchEvent(new CustomEvent(type, { detail }));
}

export class LiveClient {
  private snapshot: LiveSnapshot = {
    connection: "Sẵn sàng", voice: "Nhấn micro để nói", listening: false,
    avatar: "idle", planning: false, messages: [],
  };
  private listeners = new Set<() => void>();
  private socket: WebSocket | null = null;
  private ready = false;
  private state = "idle";
  private sessionId = "";
  private started = false;
  private waiters: { resolve(): void; reject(error: Error): void }[] = [];
  private messageId = 0;
  private inputId: number | null = null;
  private traceId: number | null = null;
  private panelRevision: number | null = null;
  readonly audio = new AudioPlayback({
    marker: marker => emit("presentation:marker", marker),
    armAtAudioStart: (time, context) => emit("presentation:audio_start", { time, context }),
    cancelTurn: turnId => emit("presentation:cancel_turn", turnId),
    clear: () => emit("presentation:clear"),
  }, () => this.panelRevision);
  readonly microphone = new MicrophoneCapture({
    ensureSocket: () => this.ensureSocket(),
    socketOpen: () => this.socket?.readyState === WebSocket.OPEN,
    send: data => this.send(data),
    armAudio: () => this.audio.arm(),
    liveState: () => this.state,
    status: voice => this.update({ voice }),
    listening: listening => this.update({ listening }),
  });

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  getSnapshot = () => this.snapshot;
  private update(patch: Partial<LiveSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach(listener => listener());
  }

  start() {
    if (this.started) return;
    this.started = true;
    const key = "lumi.gemini-live.session";
    this.sessionId = localStorage.getItem(key) || crypto.randomUUID();
    localStorage.setItem(key, this.sessionId);
    window.addEventListener("pagehide", this.close);
    reportDiagnostic("app_bootstrap", "React app reached connectSocket");
    this.connectSocket();
  }

  close = () => {
    if (this.socket?.readyState === WebSocket.OPEN) {
      if (this.microphone.active) this.send({ type: "live:mic_disabled" });
      this.send({ type: "live:close" });
    }
    this.microphone.stop();
    this.audio.interrupt(null);
    this.socket?.close();
  };

  send(data: ArrayBuffer | Payload) {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(data instanceof ArrayBuffer ? data : JSON.stringify(data));
  }

  setActiveSurface(surface: { revision: number } | null) {
    this.panelRevision = surface?.revision ?? null;
  }
  presentationSpeaking() { this.update({ avatar: "speaking" }); }

  private connectSocket() {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) return;
    this.ready = false;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/ws/live`);
    this.socket = socket; socket.binaryType = "arraybuffer";
    socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "live:connect", session_id: this.sessionId })));
    socket.addEventListener("message", event => this.handleMessage(event));
    socket.addEventListener("error", () => {
      this.update({ planning: false, connection: "Lỗi kết nối", voice: "Không thể kết nối Gemini Live." });
      this.releaseWaiters(new Error("Gemini Live socket error"));
    });
    socket.addEventListener("close", () => {
      this.socket = null; this.ready = false; this.state = "idle";
      this.update({ planning: false, connection: "Sẵn sàng" });
      this.releaseWaiters(new Error("Gemini Live socket closed before becoming ready"));
      this.microphone.stop();
    });
  }
  private releaseWaiters(error?: Error) {
    const waiters = this.waiters; this.waiters = [];
    for (const waiter of waiters) error ? waiter.reject(error) : waiter.resolve();
  }
  private async ensureSocket() {
    this.connectSocket();
    if (this.ready) return;
    await new Promise<void>((resolve, reject) => this.waiters.push({ resolve, reject }));
  }

  async submitText(query: string) {
    query = query.trim();
    if (!query) return false;
    try {
      await this.ensureSocket(); await this.audio.arm(); this.audio.interrupt(null);
      this.inputId = this.traceId = null;
      this.addMessage("user", query);
      this.update({ voice: "Đang xử lý…" });
      this.send({ type: "live:text", query });
      return true;
    } catch (error) {
      this.update({ voice: `Không thể kết nối: ${error instanceof Error ? error.message : String(error)}` });
      return false;
    }
  }
  toggleMicrophone() {
    if (this.microphone.active) this.microphone.disable();
    else void this.microphone.enable();
  }
  private addMessage(role: Message["role"], text: string) {
    const id = ++this.messageId;
    this.update({ messages: [...this.snapshot.messages, { id, role, text }] });
    return id;
  }
  private modifyMessage(id: number, patch: Partial<Message>) {
    this.update({ messages: this.snapshot.messages.map(message => message.id === id ? { ...message, ...patch } : message) });
  }

  private handleMessage(event: MessageEvent) {
    if (event.data instanceof ArrayBuffer) {
      this.audio.play(event.data);
      this.update({ avatar: "speaking", voice: "Lumi đang nói…" });
      return;
    }
    const payload: Payload = JSON.parse(event.data);
    const { type } = payload;
    if (type === "live:session_ready") {
      this.ready = true;
      this.update({ connection: "Đã kết nối", voice: "Đang mở micro…" });
      this.releaseWaiters(); void this.microphone.enable();
    }
    if (type === "live:state") {
      this.state = String(payload.state || "idle");
      const labels: Record<string, string> = {
        idle: "Sẵn sàng", listening: this.microphone.active ? "Đang nghe…" : "Sẵn sàng nói",
        waiting_for_tool: "Đang xử lý…", speaking: "Lumi đang trình bày…", error: "Có lỗi",
      };
      const patch: Partial<LiveSnapshot> = { connection: labels[this.state] || this.state };
      if (this.state === "listening") patch.voice = this.microphone.active ? "Đang nghe…" : "Đã tắt micro";
      if (this.state === "waiting_for_tool") patch.voice = "Lumi đang xử lý…";
      if (this.state === "speaking") patch.voice = "Lumi đang nói…";
      if (["speaking", "waiting_for_tool"].includes(this.state)) patch.avatar = "speaking";
      if (this.state === "idle" || (this.state === "listening" && !this.microphone.active)) patch.avatar = "idle";
      this.update(patch);
    }
    if (type === "live:input_ready") { this.update({ voice: "Đang nghe…" }); this.microphone.start(); }
    if (type === "input_transcript" && payload.text) {
      if (this.inputId === null) this.inputId = this.addMessage("user", "");
      this.modifyMessage(this.inputId, { text: payload.text, final: Boolean(payload.final) });
    }
    if (type === "live:debug_trace") {
      const timestamp = String(payload.timestamp || "").trim();
      const eventType = String(payload.event || "").trim();
      const content = String(payload.content || "").trim();
      if (timestamp && eventType && content) {
        if (this.traceId === null) this.traceId = this.addMessage("trace", "");
        const previous = this.snapshot.messages.find(item => item.id === this.traceId)?.text || "";
        this.modifyMessage(this.traceId, { text: `${previous}${timestamp} | ${eventType}: ${content}\n` });
      }
    }
    if (type === "surface:planning") this.update({ planning: payload.status === "started" });
    if (type === "openui:stream_start" || type === "openui:document") {
      this.update({ planning: false });
    }
    if (type === "panel_clear") this.update({ planning: false });
    if (type === "audio_format") this.audio.setFormat(Number(payload.sample_rate_hz));
    if (type === "audio_chunk") this.audio.setTurn(typeof payload.turn_id === "string" && payload.turn_id ? payload.turn_id : null);
    if (type === "audio_marker") this.audio.setMarker({
      ...payload.cue, animation_delay_ms: Number(payload.animation_delay_ms) || 0,
      turn_id: typeof payload.turn_id === "string" ? payload.turn_id : null,
    } as AudioMarker);
    if (type === "live:turn_complete") this.update({ avatar: "idle", voice: this.microphone.active ? "Đang nghe…" : "Đã tắt micro" });
    if (type === "live:interrupted") {
      this.audio.interrupt(typeof payload.turn_id === "string" ? payload.turn_id : null);
      this.update({ avatar: "idle", voice: this.microphone.active ? "Đang nghe…" : "Đã tắt micro" });
    }
    if (type === "live:timeout") {
      this.microphone.stop();
      this.update({ planning: false, voice: payload.reason === "idle_timeout"
        ? "Phiên đã tạm đóng vì không hoạt động. Đang kết nối lại khi bạn nói tiếp."
        : "Lumi chưa nhận được phản hồi kịp thời. Bạn có thể nói lại." });
    }
    if (type === "live:reconnecting") this.update({ connection: "Đang kết nối lại…", voice: "Đang khôi phục phiên hội thoại…" });
    if (type === "live:reconnected") this.update({ connection: "Đã kết nối lại", voice: "Đã khôi phục ngữ cảnh. Bạn có thể nói tiếp." });
    if (type === "live:error") {
      if (!this.ready) this.releaseWaiters(new Error(payload.message || "Gemini Live startup failed"));
      this.microphone.stop(); this.update({ planning: false, voice: `Lỗi: ${payload.message}`, avatar: "idle" });
    }
    emit("backend:event", payload);
  }
}

// Module lifetime: renders and UI subscriptions cannot recreate the session.
export const liveClient = new LiveClient();
