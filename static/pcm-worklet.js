/**
 * pcm16-worklet.js — AudioWorklet that turns microphone audio into the exact
 * format Gemini Live expects: 16 kHz, mono, 16-bit little-endian PCM.
 *
 * The AudioContext sample rate is usually 44.1 or 48 kHz, so we resample with
 * linear interpolation down to 16 kHz, then emit fixed 100 ms frames
 * (1600 samples = 3200 bytes) as transferable ArrayBuffers.
 */

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 1600; // 100 ms at 16 kHz

class PCM16Worklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE; // worklet global `sampleRate`
    this.pos = 0;
    this.carry = new Float32Array(0);
    this.pending = [];
    this.pendingLen = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || input[0].length === 0) {
      return true;
    }

    const frames = input[0].length;

    // Down-mix all channels to mono.
    const mono = new Float32Array(frames);
    for (let c = 0; c < input.length; c++) {
      const data = input[c];
      for (let i = 0; i < frames; i++) mono[i] += data[i];
    }
    if (input.length > 1) {
      for (let i = 0; i < frames; i++) mono[i] /= input.length;
    }

    // Prepend any leftover samples from the previous block.
    const buf = new Float32Array(this.carry.length + frames);
    buf.set(this.carry);
    buf.set(mono, this.carry.length);

    // Resample to 16 kHz.
    const out = [];
    while (this.pos + 1 < buf.length) {
      const i0 = this.pos | 0;
      const frac = this.pos - i0;
      out.push(buf[i0] + (buf[i0 + 1] - buf[i0]) * frac);
      this.pos += this.ratio;
    }
    const consumed = this.pos | 0;
    this.carry = buf.slice(consumed);
    this.pos -= consumed;

    // Convert to signed 16-bit PCM.
    const pcm = new Int16Array(out.length);
    for (let i = 0; i < out.length; i++) {
      const s = Math.max(-1, Math.min(1, out[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }

    this.pending.push(pcm);
    this.pendingLen += pcm.length;

    // Emit whole 100 ms frames.
    while (this.pendingLen >= FRAME_SAMPLES) {
      const frame = new Int16Array(FRAME_SAMPLES);
      let off = 0;
      while (off < FRAME_SAMPLES) {
        const head = this.pending[0];
        const need = FRAME_SAMPLES - off;
        if (head.length <= need) {
          frame.set(head, off);
          off += head.length;
          this.pending.shift();
        } else {
          frame.set(head.subarray(0, need), off);
          this.pending[0] = head.subarray(need);
          off += need;
        }
      }
      this.pendingLen -= FRAME_SAMPLES;
      this.port.postMessage(frame.buffer, [frame.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm16-worklet", PCM16Worklet);
