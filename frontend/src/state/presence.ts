// The Orb is a presence, not a state light.
//
// This is the single largest difference between this and something that looks
// alive. A state light says "idle". A presence does something in every state —
// it looks around, it breathes, it blinks, it holds your gaze — so the room
// reads as inhabited between your visits. The research this is ported from put
// it plainly: what separates a convincing assistant from a themed chat box is
// state-driven MOTION, not the paint job.
//
// Every function here is pure and deterministic given a seed, which is what
// makes presence testable at all. The idea under test is not "does a canvas
// move" — it is "is a spinner distinguishable from something alive", and the
// answer turns on statistics: does idle behaviour repeat on a cycle, does it
// avoid repeating, and does a state change actually change the distribution.
//
// No model, no audio analysis. Just weighted random, a breath, and a blink.

/**
 * What the Orb is doing.
 *
 * Two families live here. The room states are what the event bus reports
 * (`blocked`, `attention`, …). The voice states are what a speech pipeline
 * will report later. They are one enum because there is one thing on screen
 * and two vocabularies for it guarantees the mapping is written down.
 */
export type PresenceState =
  // room
  | "idle"
  | "working"
  | "verifying"
  | "attention"
  | "blocked"
  // voice, once the pipeline lands
  | "asleep"
  | "listening"
  | "thinking"
  | "speaking"
  // a wait that has a reason attached
  | "compacting";

/**
 * Idle behaviour, weighted.
 *
 * A pure spinner is a single loop and the eye locks onto it. A weighted pool
 * with seven entries, none of them long, reads as a person idling: the eye
 * keeps finding something new. The weights are lopsided on purpose — looking
 * around is the commonest thing someone waiting does.
 */
export interface IdleMove {
  kind: "glance" | "hover" | "tilt" | "gaze" | "stretch" | "wink" | "yawn";
  /** Lower bound in ms. */
  minMs: number;
  /** Upper bound in ms. */
  maxMs: number;
  weight: number;
}

export const IDLE_POOL: readonly IdleMove[] = [
  { kind: "glance", minMs: 3000, maxMs: 8000, weight: 33 },
  { kind: "hover", minMs: 4000, maxMs: 10000, weight: 24 },
  { kind: "tilt", minMs: 3000, maxMs: 7000, weight: 19 },
  { kind: "gaze", minMs: 5000, maxMs: 12000, weight: 10 },
  { kind: "stretch", minMs: 2000, maxMs: 4000, weight: 7 },
  { kind: "wink", minMs: 1000, maxMs: 1700, weight: 4 },
  { kind: "yawn", minMs: 1000, maxMs: 5000, weight: 3 },
];

/**
 * mulberry32 — a small seeded PRNG.
 *
 * A module-level Math.random would make presence untestable and would make the
 * room impossible to reproduce from a bug report. Seeding costs nothing and
 * turns "the orb felt wrong" into an assertion.
 */
export function makeRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Pick a weighted move. Returns null only for a zero-weight pool. */
export function pickIdleMove(rand: () => number, pool: readonly IdleMove[] = IDLE_POOL): IdleMove {
  const total = pool.reduce((sum, move) => sum + move.weight, 0);
  let roll = rand() * total;
  for (const move of pool) {
    roll -= move.weight;
    if (roll <= 0) return move;
  }
  return pool[pool.length - 1];
}

/** How long the chosen move lasts. */
export function moveDuration(move: IdleMove, rand: () => number): number {
  return move.minMs + rand() * (move.maxMs - move.minMs);
}

/**
 * Blinks arrive on their own irregular schedule — 2 to 5 seconds, never
 * metronomic. A fixed interval is the single most common tell that something is
 * animated rather than alive.
 */
export function nextBlink(rand: () => number): number {
  return 2000 + rand() * 3000;
}

/**
 * The breath. One sine, one frequency, scaled by how awake it is, so the whole
 * orb swells and settles together rather than parts of it pulsing out of step.
 */
export function breath(timeMs: number, activation: number, periodMs = 4200): number {
  // Return a hard 0 when asleep rather than a sin() times zero. A signed zero
  // compares unequal to 0 under Object.is, which is a small trap to leave in a
  // function whose whole job is to be continuous.
  if (activation === 0) return 0;
  return Math.sin((timeMs / periodMs) * Math.PI * 2) * 0.015 * activation;
}

/**
 * Presence, as a value.
 *
 * `activation` is the interesting one: a single 0..1 float that lerps toward 1
 * while anything is happening and decays toward 0 when nothing is. Everything
 * visual multiplies by it. Waking and sleeping are therefore a continuous fade
 * rather than a state flip, which is what stops the room from appearing to
 * break when the agent goes quiet.
 */
export interface Presence {
  state: PresenceState;
  /** Monotonic ms. The clock is PART of the value, not an argument.
   *
   *  It started as an argument and that was a bug: the caller passed a
   *  Presence where a timestamp was wanted, `now - moveStartedAt` compared an
   *  object to a number, and the comparison silently never fired. So idle
   *  behaviour never advanced past its first pick — which looked, in a
   *  screenshot, exactly like a working feature. */
  time: number;
  /** 0..1, eased. Drives global brightness, breath depth and ring opacity. */
  activation: number;
  /** Current idle move, or null when the state is not an idling one. */
  move: IdleMove | null;
  /** When the current move began, on the same clock. */
  moveStartedAt: number;
  /** When the current move is due to end, decided when it was chosen.
   *
   *  Resampled per frame, a duration drawn from a random source would change
   *  every frame and the move could never end. Once, at pick time, is stable. */
  moveEndsAt: number;
  /** When the next blink is due. */
  blinkAt: number;
  /** True while a blink is rendering. */
  blinking: boolean;
  /** Rises to 1 on a state change and falls back, so transitions are visible. */
  transitionEnergy: number;
}

const ACTIVATION_ON = 0.06;
const ACTIVATION_OFF = 0.025;
const ENERGY_DECAY = 0.03;
const BLINK_MS = 140;

/** States where the Orb is busy enough to stay lit and keep working. */
const BUSY: ReadonlySet<PresenceState> = new Set<PresenceState>([
  "working",
  "verifying",
  "attention",
  "blocked",
  "listening",
  "thinking",
  "speaking",
  "compacting",
]);

/** States where the Orb is idling — and so may blink and fidget. */
const IDLING: ReadonlySet<PresenceState> = new Set<PresenceState>(["idle", "asleep"]);

/**
 * Advance the presence by `dt` ms.
 *
 * Pure: same inputs, same output. `rand` is threaded in rather than drawn from
 * a module global so a test can replay an exact timeline.
 */
export function advance(now: Presence, dt: number, target: PresenceState, rand: () => number): Presence {
  const time = now.time + dt;
  const steps = dt / 16.667;

  let state = now.state;
  let transitionEnergy = now.transitionEnergy;
  // A state change is a visible event. Injecting energy and letting it decay
  // means the change reads as a change even when the two states look similar.
  if (target !== state) {
    state = target;
    transitionEnergy = 1;
  }
  transitionEnergy = Math.max(0, transitionEnergy - ENERGY_DECAY * steps);

  const wantsAwake = BUSY.has(state);
  const activation = clamp01(now.activation + (wantsAwake ? ACTIVATION_ON : -ACTIVATION_OFF) * steps);

  // Blinks: scheduled, and only in states that have an "eye" to close.
  let blinkAt = now.blinkAt;
  let blinking = now.blinking;
  if (!IDLING.has(state)) {
    blinking = false;
    blinkAt = time + nextBlink(rand);
  } else if (time >= blinkAt) {
    blinking = true;
    if (time >= blinkAt + BLINK_MS) {
      blinking = false;
      blinkAt = time + nextBlink(rand);
    }
  }

  // Idle moves only while idling. Working states have their own motion.
  let move = now.move;
  let moveStartedAt = now.moveStartedAt;
  let moveEndsAt = now.moveEndsAt;
  const idling = IDLING.has(state);
  if (!idling) {
    move = null;
  } else if (move === null || time >= moveEndsAt) {
    move = pickIdleMove(rand);
    moveStartedAt = time;
    moveEndsAt = time + moveDuration(move, rand);
  }

  return { state, time, activation, move, moveStartedAt, moveEndsAt, blinkAt, blinking, transitionEnergy };
}

/** The presence a freshly-mounted Orb starts from. */
export function initialPresence(seed = 1): Presence {
  const rand = makeRandom(seed);
  const move = pickIdleMove(rand);
  return {
    state: "idle",
    time: 0,
    activation: 0.6,
    move,
    moveStartedAt: 0,
    moveEndsAt: moveDuration(move, rand),
    blinkAt: nextBlink(rand),
    blinking: false,
    transitionEnergy: 0,
  };
}

function clamp01(n: number): number {
  return n < 0 ? 0 : n > 1 ? 1 : n;
}
