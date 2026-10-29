export interface AudioMarker {
  anchor_id?: string;
  effect?: string;
  panel_revision?: number;
  turn_id?: string | null;
  [key: string]: unknown;
}

export interface AudioPresentation {
  marker(marker: AudioMarker): void;
  armAtAudioStart(time: number, context: AudioContext): void;
  cancelTurn(turnId: string): void;
  clear(): void;
}

type TurnSource = AudioBufferSourceNode & { lumiTurnId: string };
const PCM_CROSSFADE_SECONDS = 0.0015;

/** One output clock per browser session, independent of React renders. */
export class AudioPlayback {
  private context: AudioContext | null = null;
  private sampleRate: number | null = null;
  private nextAudioAt = 0;
  private pendingMarker: AudioMarker | null = null;
  private pendingTurnId: string | null = null;
  private activeTurnId: string | null = null;
  private sources = new Set<TurnSource>();

  constructor(private presentation: AudioPresentation, private panelRevision: () => number | null) {}

  async arm() {
    if (!this.context) {
      const ctor = window.AudioContext || (window as typeof window & { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.context = new ctor();
    }
    await this.context.resume();
  }
  setFormat(sampleRate: number) { this.sampleRate = sampleRate; }
  setTurn(turnId: string | null) { this.pendingTurnId = turnId; }
  setMarker(marker: AudioMarker) { this.pendingMarker = marker; }

  interrupt(turnId: string | null) {
    for (const source of [...this.sources]) {
      if (turnId && source.lumiTurnId !== turnId) continue;
      try { source.stop(); } catch { /* Already ended. */ }
      this.sources.delete(source);
    }
    if (!turnId || this.activeTurnId === turnId) {
      this.activeTurnId = null;
      this.nextAudioAt = this.context?.currentTime || 0;
    }
    if (!turnId || this.pendingTurnId === turnId) this.pendingTurnId = null;
    if (!turnId || this.pendingMarker?.turn_id === turnId) this.pendingMarker = null;
    if (turnId) this.presentation.cancelTurn(turnId);
    else this.presentation.clear();
  }

  play(bytes: ArrayBuffer) {
    if (!this.context || !this.sampleRate || !bytes.byteLength) return;
    const turnId = this.pendingTurnId;
    this.pendingTurnId = null;
    if (!turnId) return;
    this.activeTurnId = turnId;
    const input = new Int16Array(bytes);
    const buffer = this.context.createBuffer(1, input.length, this.sampleRate);
    const output = buffer.getChannelData(0);
    for (let i = 0; i < input.length; i++) output[i] = input[i] / 32768;
    const source = this.context.createBufferSource() as TurnSource;
    const gain = this.context.createGain();
    source.buffer = buffer;
    source.connect(gain); gain.connect(this.context.destination);
    source.lumiTurnId = turnId;
    const fade = Math.min(PCM_CROSSFADE_SECONDS, buffer.duration / 4);
    const start = Math.max(this.context.currentTime + 0.020, this.nextAudioAt);
    const end = start + buffer.duration;
    gain.gain.setValueAtTime(0, start);
    gain.gain.linearRampToValueAtTime(1, start + fade);
    gain.gain.setValueAtTime(1, Math.max(start + fade, end - fade));
    gain.gain.linearRampToValueAtTime(0, end);
    source.start(start); this.nextAudioAt = end - fade;
    if (this.pendingMarker) {
      if (this.pendingMarker.turn_id === turnId && Number.isInteger(this.pendingMarker.panel_revision)
          && this.pendingMarker.panel_revision === this.panelRevision()) {
        this.presentation.marker(this.pendingMarker);
      }
      this.pendingMarker = null;
    }
    this.presentation.armAtAudioStart(start, this.context);
    this.sources.add(source);
    source.addEventListener("ended", () => this.sources.delete(source), { once: true });
  }
}

/** Same averaging/resampling and clipping as the original browser client. */
export function pcm16k(input: Float32Array, sourceRate: number): ArrayBuffer {
  const frames = Math.max(1, Math.round(input.length * 16000 / sourceRate));
  const data = new Int16Array(frames), ratio = input.length / frames;
  for (let i = 0; i < frames; i++) {
    const begin = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.max(begin + 1, Math.floor((i + 1) * ratio)));
    let sum = 0;
    for (let j = begin; j < end; j++) sum += input[j];
    const value = Math.max(-1, Math.min(1, sum / (end - begin)));
    data[i] = value < 0 ? value * 0x8000 : value * 0x7fff;
  }
  return data.buffer;
}

export function rmsLevel(samples: Float32Array) {
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return Math.sqrt(sum / Math.max(samples.length, 1));
}
