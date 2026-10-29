import { pcm16k, rmsLevel } from "./audio";

/** Capture belongs to the Live session, not a mounted button/component. */
export class MicrophoneCapture {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private mute: GainNode | null = null;
  private opening = false;
  private recording = false;
  private sendBusyAudioUntil = 0;

  constructor(private options: {
    ensureSocket(): Promise<void>;
    socketOpen(): boolean;
    send(data: ArrayBuffer | Record<string, unknown>): void;
    armAudio(): Promise<void>;
    liveState(): string;
    status(text: string): void;
    listening(value: boolean): void;
  }) {}

  get active() { return this.recording || !!this.stream; }

  async enable() {
    if (this.opening || !navigator.mediaDevices?.getUserMedia) return;
    this.opening = true;
    try {
      if (!this.options.socketOpen()) this.options.status("Đang kết nối Gemini Live…");
      await this.options.ensureSocket();
      if (this.active) return;
      await this.options.armAudio();
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      } });
      this.recording = true;
      this.options.listening(true);
      this.options.send({ type: "live:mic_enabled" });
      this.start();
    } catch (error) {
      this.options.status(`Không thể mở micro: ${error instanceof Error ? error.message : String(error)}`);
      this.stop();
    } finally { this.opening = false; }
  }

  start() {
    if (!this.recording || !this.stream || this.context) return;
    this.context = new AudioContext();
    this.source = this.context.createMediaStreamSource(this.stream);
    this.processor = this.context.createScriptProcessor(4096, 1, 1);
    this.mute = this.context.createGain(); this.mute.gain.value = 0;
    this.processor.addEventListener("audioprocess", event => {
      if (!this.recording || !this.options.socketOpen()) return;
      const samples = event.inputBuffer.getChannelData(0);
      const busy = ["waiting_for_tool", "speaking"].includes(this.options.liveState());
      if (busy) {
        const now = performance.now();
        if (rmsLevel(samples) >= 0.008) this.sendBusyAudioUntil = now + 300;
        if (now > this.sendBusyAudioUntil) return;
      }
      this.options.send(pcm16k(samples, event.inputBuffer.sampleRate));
    });
    this.source.connect(this.processor); this.processor.connect(this.mute);
    this.mute.connect(this.context.destination);
  }

  disable() {
    if (!this.active) return;
    this.recording = false;
    if (this.options.socketOpen()) this.options.send({ type: "live:mic_disabled" });
    this.stop(); this.options.status("Đã tắt micro");
  }

  stop() {
    this.recording = false; this.sendBusyAudioUntil = 0;
    this.processor?.disconnect(); this.source?.disconnect(); this.mute?.disconnect();
    void this.context?.close();
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null; this.context = null; this.source = null; this.processor = null; this.mute = null;
    this.options.listening(false);
  }
}
