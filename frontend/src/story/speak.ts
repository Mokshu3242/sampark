/** Voice narration and river ambience. No audio files. Everything fails quietly. */

let voices: SpeechSynthesisVoice[] = [];
let riverCtx: AudioContext | null = null;
let riverGain: GainNode | null = null;
let riverSrc: AudioBufferSourceNode | null = null;

/** Chrome fills the voice list later, so ask early. */
export function primeVoices(): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  voices = window.speechSynthesis.getVoices();
  window.speechSynthesis.onvoiceschanged = () => { voices = window.speechSynthesis.getVoices(); };
}

function pickVoice(): SpeechSynthesisVoice | undefined {
  const en = voices.filter((v) => /^en/i.test(v.lang));
  return en.find((v) => /natural|neural|online/i.test(v.name))
    ?? en.find((v) => /google|samantha|zira|aria|jenny/i.test(v.name))
    ?? en.find((v) => /en[-_](US|GB)/i.test(v.lang))
    ?? en[0];
}

export function speak(text: string): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US";
    u.rate = 0.98;
    const v = pickVoice();
    if (v) u.voice = v;
    window.speechSynthesis.speak(u);
  } catch { /* no voice */ }
}

export function stopSpeaking(): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  try { window.speechSynthesis.cancel(); } catch { /* ignore */ }
}

/** Soft river sound: looping pink noise through a low-pass filter. Call from a click. */
export function startRiver(): void {
  if (typeof window === "undefined") return;
  try {
    if (!riverCtx) {
      const Ctx = window.AudioContext || (window as any).webkitAudioContext;
      riverCtx = new Ctx();
      const len = riverCtx.sampleRate * 2;
      const buf = riverCtx.createBuffer(1, len, riverCtx.sampleRate);
      const data = buf.getChannelData(0);
      let b0 = 0, b1 = 0, b2 = 0;
      for (let i = 0; i < len; i++) {
        const w = Math.random() * 2 - 1;
        b0 = 0.99765 * b0 + w * 0.099046;
        b1 = 0.963 * b1 + w * 0.2965164;
        b2 = 0.57 * b2 + w * 1.0526913;
        data[i] = (b0 + b1 + b2 + w * 0.1848) * 0.06;
      }
      const src = riverCtx.createBufferSource();
      src.buffer = buf;
      src.loop = true;
      const lp = riverCtx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = 520;
      lp.Q.value = 0.6;
      const gain = riverCtx.createGain();
      gain.gain.value = 0;
      src.connect(lp).connect(gain).connect(riverCtx.destination);
      src.start();
      riverSrc = src;
      riverGain = gain;
    }
    if (riverCtx.state === "suspended") riverCtx.resume();
    const t = riverCtx.currentTime;
    riverGain!.gain.cancelScheduledValues(t);
    riverGain!.gain.linearRampToValueAtTime(0.045, t + 1.5);
  } catch { /* no audio */ }
}

export function stopRiver(): void {
  if (!riverCtx || !riverGain) return;
  const ctx = riverCtx, gain = riverGain, src = riverSrc;
  riverCtx = null; riverGain = null; riverSrc = null;
  try {
    const t = ctx.currentTime;
    gain.gain.cancelScheduledValues(t);
    gain.gain.linearRampToValueAtTime(0, t + 0.8);
    setTimeout(() => {
      try { src?.stop(); } catch { /* ignore */ }
      try { ctx.close(); } catch { /* ignore */ }
    }, 900);
  } catch { /* ignore */ }
}