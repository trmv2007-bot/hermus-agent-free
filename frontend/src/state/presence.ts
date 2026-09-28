// The Orb is a presence, not a state light.
//
// This is the single largest difference between this and something that looks
// alive. A state light says "idle". A presence does something in every state ·
// it looks around, it breathes, it blinks, it holds your gaze · so the room
// reads as inhabited between your visits. The research this is ported from put
// it plainly: what separates a convincing assistant from a themed chat box is
// state-driven MOTION, not the paint job.
//
// Every function here is pure and deterministic given a seed, which is what
// makes presence testable at all. The idea under test is not "does a canvas
// move" · it is "is a spinner distinguishable from something alive", and the
// answer turns on statistics: does idle behaviour repeat on a cycle, does it
// avoid repeating, and does a state change actually change the distribution.
//
// Two rules do the real work here, and both came out of watching the earlier
// version rather than out of theory:
//
//   1. Nothing moves unless something real moved it. Every animated channel
//      declares a `backing`, and the licence that feeds the painter comes from
//      a real event count, a real measured level, or a real state change. A
//      channel with no backing evaluates to zero. This is why the ring does
//      not free-run: it turns one segment per real signal that arrived and
//      stands still between them, which is the difference between a readout
//      and a fidget.
//
//   2. Idle is not an event. An orb that animates on a timer is asking for
//      attention on a schedule nobody chose, and a ring the eye can count is a
//      loop, not a life. Idle gets exactly three things: a shallow
//      activation-scaled breath, a blink on an irregular schedule, and the
//      seeded idle pool. Asleep gets none of them.
//
// No model and no invented waveform. Weighted random, a breath, a blink, and
// whatever the microphone and the speaker actually report.

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
 * keeps finding something new. The weights are lopsided on purpose · looking
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
 * mulberry32 · a small seeded PRNG.
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
 * Blinks arrive on their own irregular schedule · 2 to 5 seconds, never
 * metronomic. A fixed interval is the single most common tell that something is
 * animated rather than alive.
 */
export function nextBlink(rand: () => number): number {
  return 2000 + rand() * 3000;
}

/** How long a blink stays shut, in ms. Long enough to read, short enough not to linger. */
const BLINK_MS = 140;

/**
 * The breath. One sine, one frequency, scaled by how awake it is, so the whole
 * orb swells and settles together rather than parts of it pulsing out of step.
 */
export function breath(timeMs: number, activation: number, periodMs = 4200): number {
  // Return a hard 0 when asleep rather than a sin() times zero. A signed zero
  // compares unequal to 0 under Object.is, which is a small trap to leave in a
  // function whose whole job is to be continuous.
  if (activation === 0) return 0;
  const t = timeMs / 1000;
  // Three terms at periods that do not divide into one another.
  //
  // A single sine is the reason the room can feel dead even while everything
  // in it is working. It has a period, and a period is something a person
  // notices within about fifteen seconds -- it reads as a fan, a test pattern,
  // or a screensaver, because real breathing is not exactly periodic. Nobody
  // breathes on a metronome.
  //
  // The ratios matter more than the amplitudes: at 1 : 1.31 : 1.79 the cycle
  // takes roughly 30 seconds to visibly repeat instead of 4.2, and no two
  // peaks are ever quite the same height. The amplitudes are small enough that
  // the sum stays inside the 0.016 bound the tests already assert.
  const slow = Math.sin(t * (Math.PI * 2) / (periodMs / 1000));
  const mid = Math.sin(t * (Math.PI * 2) / ((periodMs / 1000) * 1.31) + 1.1);
  const fast = Math.sin(t * (Math.PI * 2) / ((periodMs / 1000) * 1.79) + 2.7);
  // Amplitudes sum to just under the 0.016 bound the tests assert, and the
  // fast term is deliberately tiny: it should break the pattern, not be
  // visible as a separate rhythm.
  return (slow * 0.0105 + mid * 0.0038 + fast * 0.0014) * activation;
}

/**
 * One easing primitive, used by every value that has to travel.
 *
 * Exponential approach, not a per-frame constant. Two things fall out of that
 * choice, and both were bugs before:
 *
 *   - It is frame-rate independent. `x += (target - x) * 0.1` per frame moves
 *     twice as far on a 120Hz display as on a 60Hz one, which means the orb is
 *     not the same orb on two machines.
 *   - It is symmetric. Given one time constant, going from 0.2 to 0.6 takes
 *     exactly as long as going from 0.6 back to 0.2, so a state that opens and
 *     closes on the same tau cannot snap on and then slump off. That is the
 *     whole of "smooth and symmetric" and it costs one line.
 *
 * A tau of 0 or less means "no easing, be there now", which is what peak hold
 * and event flare want.
 */
export function approach(current: number, target: number, dt: number, tauMs: number): number {
  if (tauMs <= 0) return target;
  if (current === target) return target;
  return current + (target - current) * (1 - Math.exp(-dt / tauMs));
}

// --- audio -----------------------------------------------------------------
//
// Two channels arrive here, and the difference between "no data" and "measured
// silence" is the difference between an honest orb and a lying one. A missing
// level source is `null`. A real room with nobody talking in it is `0`.
// Conflating them produces a ring that either never moves or invents a waveform
// out of nothing, and you cannot tell which of the two you are looking at.

/** One frame of real audio, or an honest absence of it. */
export interface AudioReading {
  /** Measured RMS of the microphone, 0..1, or null when no source is attached. */
  mic: number | null;
  /** Measured RMS of the audio being played, 0..1, or null when unmeasured. */
  playback: number | null;
  /**
   * True while audio is genuinely being played. This comes from the media
   * transport and not from a timer, which is the only reason the speaking
   * envelope can claim to know when speech started and stopped.
   */
  playing: boolean;
}

/** Which real input the envelope is currently following. */
export type LevelSource = "none" | "mic" | "playback";

/** The audio half of the presence, as a value. */
export interface AudioPresence {
  /** Monotonic ms, on the same clock as `Presence.time`. */
  time: number;
  /** Smoothed 0..1 amplitude for the painter. */
  level: number;
  /** Which input produced `level`. "none" means there is no data at all. */
  source: LevelSource;
  /**
   * True only when a real number fed the last frame. This is the flag that
   * stops the orb drawing a waveform it made up.
   */
  measured: boolean;
  /** Decaying peak hold, so a 40ms syllable is still on screen a moment later. */
  peak: number;
  /** True while the transport says audio is playing. */
  playing: boolean;
  /** When this playback segment really began, or null. */
  speechStartedAt: number | null;
  /** When playback really stopped, or null while it runs. */
  speechEndedAt: number | null;
}

/**
 * Ballistics, in the units a real meter uses.
 *
 * Attack is fast so an onset is never clipped; release is slow so the gaps
 * between words read as speech rather than as strobing. These two are
 * deliberately NOT the same, and the symmetry rule above does not apply here: a
 * meter that fell as fast as it rose would flicker every time a speaker closed
 * their mouth. Symmetry belongs to state transitions, not to a needle.
 */
const ATTACK_TAU = 45;
const RELEASE_TAU = 190;
const PEAK_RELEASE_TAU = 900;

/**
 * The floor below which a level counts as a silent room.
 *
 * The same 0.02 the microphone already uses to zero its own RMS, quoted rather
 * than invented, so the ring and the voice panel's meter cannot end up
 * disagreeing about whether the room is quiet.
 */
export const LEVEL_FLOOR = 0.02;

/**
 * The envelope held open while real audio is genuinely playing and no
 * amplitude tap exists.
 *
 * Not a sine and not a guess at a waveform: a steady open shape bounded by the
 * real transport. It says "audio is coming out of this machine" and nothing
 * more, and `measured` stays false the whole time so the painter can say so in
 * words instead of only in pixels.
 */
const UNMEASURED_SPEECH_TARGET = 1;

export function initialAudio(): AudioPresence {
  return {
    time: 0,
    level: 0,
    source: "none",
    measured: false,
    peak: 0,
    playing: false,
    speechStartedAt: null,
    speechEndedAt: null,
  };
}

/** Advance the audio envelope. Pure: same inputs, same output. */
export function advanceAudio(now: AudioPresence, dt: number, time: number, reading: AudioReading): AudioPresence {
  // Playback outranks the mic. A machine talking into an open microphone should
  // show what it is emitting, not what it hears back off its own speakers.
  const source: LevelSource = reading.playing ? "playback" : reading.mic === null ? "none" : "mic";
  const raw = reading.playing ? (reading.playback ?? UNMEASURED_SPEECH_TARGET) : (reading.mic ?? 0);
  const measured = reading.playing ? reading.playback !== null : reading.mic !== null;
  const target = clamp01(Number.isFinite(raw) ? raw : 0);

  const level = approach(now.level, target, dt, target > now.level ? ATTACK_TAU : RELEASE_TAU);
  // Peak hold: instant on the way up, slow on the way down, so a short loud
  // syllable does not vanish inside a single frame.
  const peak = approach(now.peak, level, dt, level > now.peak ? 0 : PEAK_RELEASE_TAU);

  // The playback window is read off the transport on the frame it changes, not
  // predicted from a duration. A duration estimate is a guess, and a guess is
  // exactly what makes an assistant sound like a recording.
  let speechStartedAt = now.speechStartedAt;
  let speechEndedAt = now.speechEndedAt;
  if (reading.playing && !now.playing) {
    speechStartedAt = time;
    speechEndedAt = null;
  } else if (!reading.playing && now.playing) {
    speechEndedAt = time;
  }

  return { time, level, source, measured, peak, playing: reading.playing, speechStartedAt, speechEndedAt };
}

// --- events ----------------------------------------------------------------
//
// The ring turns by event, not by clock. Every tick on it is a real signal
// that arrived, which is the whole reason it can be trusted: a quiet room draws
// an unlit ring, and a room working hard draws a trail of ticks that empties as
// those events age out. A ring that turns forever is a spinner wearing a
// status colour.

/** How far the ring turns for each real event. */
export const EVENT_ARC = Math.PI / 12;

/** How many recent event windows the ring remembers. */
export const EVENT_SLOTS = 12;

/** Width of one event window, in ms. */
export const EVENT_SLOT_MS = 900;

/** How long a tick stays legible after its event. */
const EVENT_FADE_MS = 2600;

export interface EventHistory {
  /** When an event landed in each of the last EVENT_SLOTS windows, -1 for none. */
  recent: number[];
  /** The window index the history is currently anchored to. */
  slot: number;
}

export function initialEvents(): EventHistory {
  return { recent: new Array<number>(EVENT_SLOTS).fill(-1), slot: 0 };
}

function shiftEvents(now: EventHistory, time: number, arrived: boolean): EventHistory {
  const slot = Math.floor(time / EVENT_SLOT_MS);
  if (slot === now.slot) {
    // Same window. Only an arrival changes anything, so a frame that neither
    // crosses a boundary nor reports an event returns the identical object and
    // the history cannot drift.
    if (!arrived) return now;
    const recent = now.recent.slice();
    recent[0] = time;
    return { recent, slot };
  }
  const shift = Math.min(EVENT_SLOTS, Math.max(0, slot - now.slot));
  const recent = [...new Array<number>(shift).fill(-1), ...now.recent].slice(0, EVENT_SLOTS);
  if (arrived) recent[0] = time;
  return { recent, slot };
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
   *  behaviour never advanced past its first pick · which looked, in a
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
  /** Real events seen. The only thing the ring is allowed to turn by. */
  events: number;
  /** Flare straight after a real event or a real state change, 0..1. */
  glint: number;
  /** Per-window event times backing the ring's ticks. */
  history: EventHistory;
}

/**
 * Time constants, in ms.
 *
 * ACTIVATION_TAU and ENERGY_TAU are the same in both directions on purpose, so
 * a state opens and closes along one curve. The earlier pair woke at one rate
 * and slept at a slower one, which is why the core appeared to snap alive and
 * then slump away again.
 */
const ACTIVATION_TAU = 1800;
const ENERGY_TAU = 170;
const GLINT_TAU = 260;

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

/** States where the Orb is idling · and so may blink and fidget. */
const IDLING: ReadonlySet<PresenceState> = new Set<PresenceState>(["idle", "asleep"]);

/**
 * States where the Orb is completely still.
 *
 * Not "slow". Still. Asleep must not drift, because a thing that keeps moving
 * while it sleeps is a sleep indicator pretending to be a sleeper. Blocked must
 * not move either, because a thing that is stuck should not look busy.
 */
const STILL: ReadonlySet<PresenceState> = new Set<PresenceState>(["asleep", "blocked"]);

/** Is this state permitted to move at all? */
export function isStill(state: PresenceState): boolean {
  return STILL.has(state);
}

/**
 * Advance the presence by `dt` ms.
 *
 * Pure: same inputs, same output. `rand` is threaded in rather than drawn from
 * a module global so a test can replay an exact timeline. `events` is the count
 * of real signals the room has reported, and it defaults to the count already
 * held, so a caller that never reports anything gets a ring that stands still
 * rather than one that free-runs.
 */
export function advance(
  now: Presence,
  dt: number,
  target: PresenceState,
  rand: () => number,
  events: number = now.events,
): Presence {
  const time = now.time + dt;

  let state = now.state;
  let energyTarget = 0;
  // A state change is a visible event. Easing toward it on the same tau it
  // decays with is what makes the change read as a change even when the two
  // states differ only in colour.
  if (target !== state) {
    state = target;
    energyTarget = 1;
  }
  const transitionEnergy = clamp01(approach(now.transitionEnergy, energyTarget, dt, ENERGY_TAU));

  const wantsAwake = BUSY.has(state);
  // One tau for both directions, so activation is symmetric.
  const activation = clamp01(approach(now.activation, wantsAwake ? 1 : 0, dt, ACTIVATION_TAU));

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
  if (!IDLING.has(state)) {
    move = null;
  } else if (move === null || time >= moveEndsAt) {
    move = pickIdleMove(rand);
    moveStartedAt = time;
    moveEndsAt = time + moveDuration(move, rand);
  }

  // A still state is a hard stop rather than a slow one, blink included.
  if (isStill(state)) {
    move = null;
    blinking = false;
  }

  // Real events. The count only ever goes up, and the ring angle is a pure
  // function of it, so a remount cannot rewind the trail.
  const eventsSeen = Math.max(now.events, Math.floor(events));
  const arrived = eventsSeen > now.events || target !== now.state;
  const glint = clamp01(approach(now.glint, arrived ? 1 : 0, dt, arrived ? 0 : GLINT_TAU));

  return {
    state,
    time,
    activation,
    move,
    moveStartedAt,
    moveEndsAt,
    blinkAt,
    blinking,
    transitionEnergy,
    events: eventsSeen,
    glint,
    history: shiftEvents(now.history, time, arrived),
  };
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
    events: 0,
    glint: 0,
    history: initialEvents(),
  };
}

function clamp01(n: number): number {
  return n < 0 ? 0 : n > 1 ? 1 : n;
}

// --- motion ----------------------------------------------------------------
//
// One function turns the two values above into everything the painter needs,
// and every field it returns is a licence. A channel whose backing condition is
// not currently satisfied is exactly zero, so the painter is structurally
// unable to draw a moving thing that nothing is behind.

/** Which real condition is paying for a channel. */
export type Backing = "event" | "level" | "speech" | "transition" | "breath" | "none";

export interface Motion {
  /** Ring rotation in radians, advanced by real events and nothing else. */
  spin: number;
  /** Per-window tick brightness for the ring, newest first, 0..1. */
  ticks: number[];
  /** 0..1 ring opacity. */
  ring: number;
  /** Signed body swell as a fraction of radius. */
  swell: number;
  /** 0..1 amplitude for the listening ring, or null when there is nothing to draw. */
  audio: number | null;
  /** True when `audio` came from a real measurement rather than from the transport. */
  audioMeasured: boolean;
  /** 0..1 peak hold for the listening ring. */
  audioPeak: number;
  /** True while the transport says audio is playing. */
  speaking: boolean;
  /** Flare straight after a real event. */
  glint: number;
  /** Eye aperture, 0 shut and 1 open. */
  aperture: number;
  /** Where the eye looks, as a fraction of radius. */
  gaze: { x: number; y: number };
  /** Why each animated channel is currently allowed to move. */
  backing: Record<"spin" | "ring" | "swell" | "audio", Backing>;
}

/**
 * Where the idle move wants the body to sit, eased in and out.
 *
 * The ease is the same at both ends, so a glance leaves the way it arrived. A
 * move that eased in and snapped out read as a glitch rather than as a person.
 */
export function idleOffset(p: Presence, r: number): { x: number; y: number } {
  if (!p.move) return { x: 0, y: 0 };
  const span = p.moveEndsAt > p.moveStartedAt ? p.moveEndsAt - p.moveStartedAt : 1;
  const t = Math.min(1, Math.max(0, (p.time - p.moveStartedAt) / span));
  // Ease both ends so a move starts and stops rather than snapping.
  const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
  switch (p.move.kind) {
    // Frequent, small, horizontal: the commonest thing someone waiting does.
    case "glance":
      return { x: Math.sin(e * Math.PI * 2) * r * 0.05, y: 0 };
    case "hover":
      return { x: 0, y: -e * r * 0.07 };
    case "tilt":
      return { x: Math.sin(e * Math.PI) * r * 0.04, y: Math.cos(e * Math.PI) * r * 0.03 };
    // A long, focused stare. Barely moves, and the stillness is the point.
    case "gaze":
      return { x: 0, y: 0 };
    case "stretch":
      return { x: 0, y: -e * r * 0.12 };
    case "wink":
      return { x: Math.sin(e * Math.PI) * r * 0.03, y: 0 };
    case "yawn":
      return { x: 0, y: e * r * 0.05 };
    default:
      return { x: 0, y: 0 };
  }
}

/** Where the eye looks, which follows the body but overshoots slightly. */
export function gazeOffset(p: Presence, r: number): { x: number; y: number } {
  const body = idleOffset(p, r * 1.6);
  return { x: body.x * 1.25, y: body.y * 1.25 };
}

/**
 * Everything the painter draws, derived from the two presence values.
 *
 * There are no free-running clock terms. The only things that advance between
 * real events are the breath, which is scaled by activation, and the decay of a
 * flare that a real event caused. Take the events and the state changes away
 * and this returns a still orb, which is the property the tests assert.
 */
export function motionFor(p: Presence, audio: AudioPresence): Motion {
  // The ring turns by event. `events * EVENT_ARC` rather than an accumulator,
  // so the angle is a function of what has happened rather than of how long the
  // tab has been open.
  const spin = p.events * EVENT_ARC;

  const ticks: number[] = p.history.recent.map((at) => (at < 0 ? 0 : clamp01(1 - (p.time - at) / EVENT_FADE_MS)));
  const activity = Math.max(ticks[0] ?? 0, p.transitionEnergy);
  const act = p.activation;

  // Asleep closes its eye; blocked keeps it open, because blocked is waiting for
  // something and asleep is not.
  const aperture = p.state === "asleep" ? 0 : p.blinking ? 0.12 : 1;

  // A still state is a hard stop. Not the ring, not the swell, not the ticks
  // from events that landed before it, not the eye.
  if (isStill(p.state)) {
    return {
      spin: 0,
      ticks: ticks.map(() => 0),
      ring: 0,
      swell: 0,
      audio: null,
      audioMeasured: false,
      audioPeak: 0,
      speaking: false,
      glint: 0,
      aperture,
      gaze: { x: 0, y: 0 },
      backing: { spin: "none", ring: "none", swell: "none", audio: "none" },
    };
  }

  // The event trail. A tick is lit only while a real event sits inside its fade
  // window, so the ring visibly empties when the room goes quiet. That decay is
  // the honest part, and it is the thing the old free-running sweep could not
  // do at all.
  const ring = clamp01(0.18 * act + 0.82 * activity);

  // The body swells for exactly two real reasons: the breath, scaled by
  // activation, and a real event that just landed. There is no third.
  const swell = breath(p.time, act) * 3 + p.glint * 0.02;

  const speaking = p.state === "speaking" && audio.playing;
  // The audio ring is drawn only from a real measurement, except while the
  // transport is genuinely playing, where the shape is transport-derived and is
  // reported as unmeasured so the painter can label it honestly.
  const showAudio = (p.state === "listening" && audio.source === "mic" && audio.measured) || speaking;
  const audioValue = showAudio ? clamp01(audio.level) : null;

  return {
    spin,
    ticks,
    ring,
    swell,
    audio: audioValue,
    audioMeasured: showAudio && audio.measured,
    audioPeak: showAudio ? clamp01(audio.peak) : 0,
    speaking,
    glint: p.glint,
    aperture,
    gaze: gazeOffset(p, 0.2),
    backing: {
      spin: "event",
      ring: activity > 0 ? "event" : "breath",
      swell: p.glint > 0.01 ? "event" : "breath",
      audio: audioValue === null ? "none" : speaking && !audio.measured ? "speech" : "level",
    },
  };
}
