/**
 * AudioWorklet: mono mic input at the context's sample rate -> 16 kHz PCM16 LE frames.
 * Posts an ArrayBuffer of `frameSamples` Int16 samples (default 640 = 40 ms) per message.
 * Resamples with linear interpolation when the AudioContext could not be created at 16 kHz.
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || 16000;
    this.frameSamples = opts.frameSamples || 640;
    this.ratio = sampleRate / this.targetRate; // input samples per output sample
    this.pos = 0; // fractional read position into `carry`
    this.carry = new Float32Array(0);
    this.out = new Int16Array(this.frameSamples);
    this.outLen = 0;
    this.muted = false;
    this.port.onmessage = (e) => {
      if (e.data && typeof e.data.muted === "boolean") this.muted = e.data.muted;
    };
  }

  push(sample) {
    const s = Math.max(-1, Math.min(1, sample));
    this.out[this.outLen++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.outLen === this.frameSamples) {
      const buf = this.out.buffer.slice(0);
      this.port.postMessage(buf, [buf]);
      this.outLen = 0;
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const channel = input[0];
    if (this.muted) return true;

    if (this.ratio === 1) {
      for (let i = 0; i < channel.length; i++) this.push(channel[i]);
      return true;
    }

    // Concatenate leftover samples with the new block, then interpolate.
    const data = new Float32Array(this.carry.length + channel.length);
    data.set(this.carry, 0);
    data.set(channel, this.carry.length);
    let pos = this.pos;
    while (pos + 1 < data.length) {
      const i = Math.floor(pos);
      const frac = pos - i;
      this.push(data[i] * (1 - frac) + data[i + 1] * frac);
      pos += this.ratio;
    }
    const keepFrom = Math.floor(pos);
    this.carry = data.slice(keepFrom);
    this.pos = pos - keepFrom;
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
