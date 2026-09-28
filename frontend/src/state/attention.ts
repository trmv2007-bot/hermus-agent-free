/**
 * Where the person is, right now.
 *
 * This is the single biggest thing separating an interface that feels awake
 * from one that feels like it is running a job. The system streams an answer
 * in a few seconds, and for most of that time nothing in the room acknowledges
 * the person at all. So they type, and the room is visibly busy with
 * something that is not them.
 *
 * The fix is not more animation. It is one cheap signal: while there is text in
 * the input, the core is drawn looking at it. That is it. It costs a
 * coordinate and an easing term, and it closes the loop between "I did
 * something" and "something noticed".
 *
 * The state is deliberately tiny and module-local rather than in the workspace
 * store, because it is per-observer, changes on every keystroke, and nothing
 * else in the app needs to know about it. Putting a keystroke-rate value into
 * shared state would make every surface re-render while someone types.
 *
 * Two rules keep it from becoming noise:
 *
 *   - It decays. Let go and the attention releases on its own, so the room
 *     does not stay locked on a field nobody is using.
 *   - It is a *look*, not a claim. The core turns slightly toward the input.
 *     It does not claim to be reading it, because it is not.
 */

export interface Attention {
  /** Where to look, as a fraction of the stage. Null when nobody is asking. */
  target: { x: number; y: number } | null;
  /** 0..1, how strongly it is being held. */
  hold: number;
  /** Monotonic ms of the last input event, for the decay. */
  lastAt: number;
}

const RELEASE_MS = 2600;
const RISE_TAU = 0.16;
const FALL_TAU = 0.9;

let state: Attention = { target: null, hold: 0, lastAt: 0 };
const listeners = new Set<(a: Attention) => void>();

export function attention(): Attention {
  return state;
}

export function subscribeAttention(fn: (a: Attention) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) fn(state);
}

/**
 * The person typed. `x`/`y` are viewport fractions, so this is resolution- and
 * layout-independent -- the same call works on the 1080p desktop and on a
 * resized window, and it does not need to be recomputed on resize.
 */
export function noteInput(x: number, y: number, at = nowMs()): void {
  state = { target: { x: clamp01(x), y: clamp01(y) }, hold: 1, lastAt: at };
  emit();
}

/** The answer landed, or the field was cleared. Let go. */
export function releaseAttention(): void {
  if (state.hold === 0 && state.target === null) return;
  state = { target: state.target, hold: 0, lastAt: state.lastAt };
  emit();
}

export function clearAttention(): void {
  state = { target: null, hold: 0, lastAt: 0 };
  emit();
}

/**
 * Advance the hold. Exposed as a pure function so the easing can be tested
 * without a clock, and called once per frame from the Orb.
 */
export function advanceAttention(a: Attention, deltaMs: number, at: number): Attention {
  if (a.hold === 0) {
    // Already released: let the target go so nothing keeps looking at a field
    // that has been abandoned for a while.
    const idle = at - a.lastAt;
    return idle > RELEASE_MS ? { ...a, target: null } : a;
  }
  const tau = RELEASE_MS;
  const decayed = Math.exp(-deltaMs / tau);
  const next = a.hold * decayed;
  return next < 0.01 ? { ...a, hold: 0 } : { ...a, hold: next };
}

/** Commit the advanced state. Kept separate so the pure step is testable. */
export function tickAttention(deltaMs: number, at = nowMs()): Attention {
  state = advanceAttention(state, deltaMs, at);
  return state;
}

export function nowMs(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function clamp01(n: number): number {
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0;
}

/** Easing constants exported so the shell can show the same numbers. */
export const TIMING = { RELEASE_MS, RISE_TAU, FALL_TAU };

export function resetAttentionForTest(): void {
  state = { target: null, hold: 0, lastAt: 0 };
  listeners.clear();
}
