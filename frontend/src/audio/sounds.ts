/**
 * Sound, synthesised. Nothing to download, nothing to ship.
 *
 * Every sound here is generated with oscillators and filtered noise at the
 * moment it plays. That is a deliberate choice over a pack of audio files: it
 * costs zero bytes of repo, zero bandwidth, and no licensing question, and it
 * means a sound can be tuned by changing a number rather than re-recording a
 * sample.
 *
 * The hard part of interface sound is not making it, it is making it stop.
 * Sound that plays on everything trains people to mute the whole system within
 * a day, and then it is worse than no sound at all. So:
 *
 *  - Nothing plays before the first real user gesture, because browsers block
 *    it and because audio before anyone has touched anything is a startup
 *    noise, not feedback.
 *  - There is a single master level, deliberately low.
 *  - Mute is remembered, and is a real off switch rather than a "quiet" preset.
 *  - `prefers-reduced-motion` is read as a signal for restraint generally: it
 *    does not strictly mean "less sound", but someone who asked the OS for
 *    less stimulation should not be surprised by a chime.
 *
 * Timbre carries the meaning, so the events are distinguishable without being
 * loud. Click, open and close share a family because they are the same class
 * of thing; think, listen and speak do not, because they are states rather
 * than actions and need to be told apart at a glance.
 */

export type SoundName =
  | "click"
  | "open"
  | "close"
  | "hover"
  | "toggle"
  | "error"
  | "ready"
  | "think"
  | "listen"
  | "speak"
  | "error-soft";

const STORAGE_KEY = "hermus.sound.muted";
const LEVEL_KEY = "hermus.sound.level";

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let enabled = false;

/**
 * Preferences are read on first use rather than at import.
 *
 * Reading them at module scope means the first thing that happens is a storage
 * read that cannot fail gracefully -- there is no chance for a test, a server
 * render, or a browser that denies storage to install a working localStorage
 * first. A getter costs nothing and makes the module safe to import anywhere.
 */
let muted: boolean | null = null;
let level: number | null = null;

function isMuted(): boolean {
  return (muted ??= readBool(STORAGE_KEY, false));
}

function getLevel(): number {
  return (level ??= readNumber(LEVEL_KEY, 0.22));
}

function readBool(key: string, dflt: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v == null ? dflt : v === "1";
  } catch {
    return dflt;
  }
}

function readNumber(key: string, dflt: number): number {
  try {
    const v = localStorage.getItem(key);
    const n = v == null ? NaN : Number(v);
    return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : dflt;
  } catch {
    return dflt;
  }
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, isMuted() ? "1" : "0");
    localStorage.setItem(LEVEL_KEY, String(getLevel()));
  } catch {
    /* private mode; the session still works, it just will not be remembered */
  }
}

export function setMuted(next: boolean): boolean {
  muted = next;
  persist();
  return muted;
}

export function setLevel(next: number): number {
  level = Math.min(1, Math.max(0, next));
  // Apply live, so dragging a volume control does something. Persisting for
  // the next boot without applying it now is the half-done version of this.
  if (master && ctx) {
    try { master.gain.setValueAtTime(level, ctx.currentTime); } catch { /* ignore */ }
  }
  persist();
  return level;
}

/** Browsers only allow audio after a gesture. Call this from one. */
export function unlock(): void {
  if (!enabled) return;
  try {
    if (!ctx) {
      ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      // Every voice hangs off one master gain. Without this the module has a
      // context but nowhere to send sound, so play() and loop() both bail on
      // `!master` and the whole feature is silent while looking correctly
      // wired -- which is the exact shape of bug this repo has collected.
      master = ctx.createGain();
      master.gain.setValueAtTime(getLevel(), ctx.currentTime);
      master.connect(ctx.destination);
    }
    if (ctx.state === "suspended") void ctx.resume();
  } catch {
    ctx = null;
    master = null;
  }
}

export function init(): void {
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  // Reduced-motion is not a request for silence, but it is a clear request for
  // less. Default off rather than default on in that case.
  enabled = !reduced;
}

function now(): number {
  return ctx?.currentTime ?? 0;
}

function env(gain: GainNode, t: number, peak: number, attack: number, decay: number): void {
  gain.gain.cancelScheduledValues(t);
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), t + attack);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + attack + decay);
}

function tone(
  freq: number,
  opts: { type?: OscillatorType; at?: number; gain?: number; attack?: number; decay?: number; glide?: number } = {},
): void {
  if (!ctx || !master || isMuted()) return;
  const t = now() + (opts.at ?? 0);
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = opts.type ?? "sine";
  osc.frequency.setValueAtTime(freq, t);
  if (opts.glide) osc.frequency.exponentialRampToValueAtTime(Math.max(20, opts.glide), t + (opts.decay ?? 0.15));
  env(g, t, opts.gain ?? 0.5, opts.attack ?? 0.005, opts.decay ?? 0.12);
  osc.connect(g).connect(master);
  osc.start(t);
  osc.stop(t + (opts.attack ?? 0.005) + (opts.decay ?? 0.12) + 0.05);
}

/** A filtered noise transient. The difference between "a beep" and "a tap". */
function noise(opts: { at?: number; gain?: number; decay?: number; freq?: number; q?: number; type?: BiquadFilterType } = {}): void {
  if (!ctx || !master || isMuted()) return;
  const t = now() + (opts.at ?? 0);
  const decay = opts.decay ?? 0.05;
  const frames = Math.max(1, Math.ceil(ctx.sampleRate * decay));
  const buf = ctx.createBuffer(1, frames, ctx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < frames; i++) {
    // Fade the noise out across the buffer, so the transient does not click
    // itself at the end.
    data[i] = (Math.random() * 2 - 1) * (1 - i / frames);
  }
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const filt = ctx.createBiquadFilter();
  filt.type = opts.type ?? "bandpass";
  filt.frequency.value = opts.freq ?? 2400;
  filt.Q.value = opts.q ?? 1.2;
  const g = ctx.createGain();
  env(g, t, opts.gain ?? 0.3, 0.002, decay);
  src.connect(filt).connect(g).connect(master);
  src.start(t);
}

/**
 * A voice keeps one oscillator running and moves its gain, so a looping state
 * such as "thinking" does not sound like a machine gun of one-shot blips.
 */
const loops = new Map<string, { osc: OscillatorNode; gain: GainNode }>();

export function loop(name: "think" | "listen", on: boolean): void {
  if (!ctx || !master || isMuted()) return;
  const existing = loops.get(name);
  if (on && !existing) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    const filt = ctx.createBiquadFilter();
    filt.type = "lowpass";
    filt.frequency.value = name === "think" ? 420 : 700;
    osc.type = "triangle";
    // Two close partials beat against each other very slightly. Pure
    // single-frequency hum reads as a device fault; a slow beat reads as
    // something working.
    osc.frequency.value = name === "think" ? 88 : 196;
    const lfo = ctx.createOscillator();
    const lfoGain = ctx.createGain();
    lfo.frequency.value = name === "think" ? 0.6 : 1.1;
    lfoGain.gain.value = name === "think" ? 2.5 : 4;
    lfo.connect(lfoGain).connect(osc.frequency);

    g.gain.setValueAtTime(0.0001, now());
    g.gain.exponentialRampToValueAtTime(0.05, now() + 0.35);
    osc.connect(filt).connect(g).connect(master);
    osc.start();
    lfo.start();
    loops.set(name, { osc, gain: g });
  } else if (!on && existing) {
    const t = now();
    existing.gain.gain.cancelScheduledValues(t);
    existing.gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    existing.osc.stop(t + 0.25);
    loops.delete(name);
  }
}

export function stopAllLoops(): void {
  loop("think", false);
  loop("listen", false);
}

export function play(name: SoundName): void {
  if (!enabled || isMuted() || !ctx) return;
  switch (name) {
    case "click":
      // Dry, short, slightly bright. This is the most-repeated sound in the
      // system, so it is the one that has to be almost inaudible.
      noise({ gain: 0.20, decay: 0.035, freq: 3200, q: 0.9 });
      tone(1180, { type: "triangle", gain: 0.10, decay: 0.035 });
      break;
    case "hover":
      noise({ gain: 0.045, decay: 0.028, freq: 4600, q: 1.6 });
      break;
    case "toggle":
      tone(660, { type: "sine", gain: 0.16, decay: 0.07 });
      tone(990, { type: "sine", at: 0.045, gain: 0.13, decay: 0.08 });
      break;
    case "open":
      // Rising, because the surface is arriving.
      tone(420, { type: "sine", gain: 0.16, decay: 0.13, glide: 720 });
      tone(840, { type: "sine", at: 0.035, gain: 0.07, decay: 0.12, glide: 1280 });
      noise({ at: 0.005, gain: 0.07, decay: 0.05, freq: 2600 });
      break;
    case "close":
      tone(700, { type: "sine", gain: 0.13, decay: 0.11, glide: 380 });
      break;
    case "error":
      // Low and slightly sour. Not alarming -- alarming gets muted.
      tone(196, { type: "sawtooth", gain: 0.16, decay: 0.26 });
      tone(207, { type: "sawtooth", gain: 0.10, decay: 0.26, at: 0.01 });
      break;
    case "error-soft":
      tone(262, { type: "triangle", gain: 0.09, decay: 0.16 });
      break;
    case "ready":
      tone(587, { type: "sine", gain: 0.14, decay: 0.16 });
      tone(880, { type: "sine", at: 0.07, gain: 0.12, decay: 0.22 });
      break;
    case "speak":
      tone(520, { type: "sine", gain: 0.09, decay: 0.09, glide: 640 });
      break;
    default:
      break;
  }
}

/** Test seam: lets a test assert that init/unlock/mute compose correctly. */
export function _state() {
  return { enabled, muted: isMuted(), level: getLevel(), hasContext: ctx != null, loops: [...loops.keys()] };
}

export function _setEnabled(v: boolean): void {
  enabled = v;
}

export { isMuted, getLevel };

const sound = {
  play, loop, stopAllLoops, unlock, init, isMuted, setMuted, getLevel, setLevel,
};

export default sound;
