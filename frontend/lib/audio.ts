/** Gapless playback of streamed PCM16 LE mono audio with instant flush for barge-in. */
export class PcmPlayer {
  readonly ctx: AudioContext;
  readonly analyser: AnalyserNode;
  private readonly gain: GainNode;
  private sources = new Set<AudioBufferSourceNode>();
  private playhead = 0;
  private onIdle: (() => void) | null = null;

  constructor(sampleRate = 24_000) {
    this.ctx = new AudioContext({ sampleRate });
    this.gain = this.ctx.createGain();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.gain.connect(this.analyser);
    this.analyser.connect(this.ctx.destination);
  }

  /** Called whenever the playback queue drains. */
  setIdleListener(fn: (() => void) | null): void {
    this.onIdle = fn;
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  async resume(): Promise<void> {
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  enqueue(pcm: ArrayBuffer): void {
    const samples = new Int16Array(pcm);
    if (samples.length === 0) return;
    const buffer = this.ctx.createBuffer(1, samples.length, this.ctx.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 0x8000;

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gain);
    // Small lead avoids clicks when the queue was empty; otherwise play back-to-back.
    const startAt = Math.max(this.ctx.currentTime + 0.03, this.playhead);
    source.start(startAt);
    this.playhead = startAt + buffer.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      if (this.sources.size === 0) this.onIdle?.();
    };
  }

  /** Barge-in: drop everything queued and stop the current chunk immediately. */
  flush(): void {
    for (const s of this.sources) {
      s.onended = null;
      try {
        s.stop();
      } catch {
        // already stopped
      }
      s.disconnect();
    }
    this.sources.clear();
    this.playhead = 0;
    this.onIdle?.();
  }

  async close(): Promise<void> {
    this.flush();
    this.onIdle = null;
    if (this.ctx.state !== "closed") await this.ctx.close();
  }
}

export interface MicMeter {
  ctx: AudioContext;
  analyser: AnalyserNode;
  /** Current RMS level of the mic (0..1). */
  level: () => number;
  close: () => Promise<void>;
}

/** Mic level meter only (no streaming) - used by the pipeline engine for waveform + barge-in. */
export async function startMicMeter(): Promise<MicMeter> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  if (ctx.state === "suspended") await ctx.resume();
  const buf = new Float32Array(analyser.fftSize);
  return {
    ctx,
    analyser,
    level: () => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      return Math.sqrt(sum / buf.length);
    },
    close: async () => {
      source.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      if (ctx.state !== "closed") await ctx.close();
    },
  };
}

/**
 * speechSynthesis output can't be tapped by Web Audio, so the pipeline engine visualises the agent's
 * voice with a synthetic, speech-like signal that is only "on" while an utterance is playing.
 * The signal never reaches the speakers (gain 0 before the destination).
 */
export class SyntheticVoiceLevel {
  readonly ctx: AudioContext;
  readonly analyser: AnalyserNode;
  private readonly env: GainNode;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    this.ctx = new AudioContext();
    const a = this.ctx.createOscillator();
    const b = this.ctx.createOscillator();
    a.frequency.value = 170;
    b.type = "triangle";
    b.frequency.value = 263;
    this.env = this.ctx.createGain();
    this.env.gain.value = 0;
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    a.connect(this.env);
    b.connect(this.env);
    this.env.connect(this.analyser);
    this.analyser.connect(mute);
    mute.connect(this.ctx.destination);
    a.start();
    b.start();
  }

  async resume(): Promise<void> {
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  setActive(active: boolean): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    const now = this.ctx.currentTime;
    if (!active) {
      this.env.gain.setTargetAtTime(0, now, 0.05);
      return;
    }
    this.timer = setInterval(() => {
      // Syllable-like amplitude envelope.
      this.env.gain.setTargetAtTime(0.15 + Math.random() * 0.45, this.ctx.currentTime, 0.04);
    }, 110);
  }

  async close(): Promise<void> {
    this.setActive(false);
    if (this.ctx.state !== "closed") await this.ctx.close();
  }
}

export interface MicCapture {
  ctx: AudioContext;
  analyser: AnalyserNode;
  stream: MediaStream;
  node: AudioWorkletNode;
  setMuted: (muted: boolean) => void;
  close: () => Promise<void>;
}

/** Open the microphone and stream 16 kHz PCM16 frames (~40 ms) to `onFrame`. */
export async function startMicCapture(onFrame: (frame: ArrayBuffer) => void): Promise<MicCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });

  let ctx: AudioContext;
  try {
    ctx = new AudioContext({ sampleRate: 16_000 });
  } catch {
    ctx = new AudioContext(); // worklet resamples to 16 kHz
  }
  try {
    await ctx.audioWorklet.addModule("/pcm-capture-worklet.js");
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    const node = new AudioWorkletNode(ctx, "pcm-capture", {
      processorOptions: { targetRate: 16_000, frameSamples: 640 },
    });
    node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => onFrame(e.data);
    // Keep the worklet pulled by the graph without making the mic audible.
    const sink = ctx.createGain();
    sink.gain.value = 0;
    source.connect(analyser);
    source.connect(node);
    node.connect(sink);
    sink.connect(ctx.destination);
    if (ctx.state === "suspended") await ctx.resume();

    return {
      ctx,
      analyser,
      stream,
      node,
      setMuted: (muted) => node.port.postMessage({ muted }),
      close: async () => {
        node.port.onmessage = null;
        source.disconnect();
        node.disconnect();
        sink.disconnect();
        stream.getTracks().forEach((t) => t.stop());
        if (ctx.state !== "closed") await ctx.close();
      },
    };
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop());
    if (ctx.state !== "closed") await ctx.close();
    throw err;
  }
}
