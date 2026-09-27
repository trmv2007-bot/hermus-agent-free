// Where the core sits, and what it is doing.
//
// The orb is not decoration: its position answers "where is the work" and its
// motion answers "what is happening right now". Both are derived from real
// workspace state here, as pure functions, so the rules can be tested without a
// browser and the renderer stays a dumb painter.

import type { Surface, Viewport } from "./surfaces";

export type OrbState =
  | "idle"
  | "working"
  | "verifying"
  | "attention"
  | "blocked"
  // Voice. These are driven by the microphone and the speaker rather than the
  // event tray, and they outrank it: a room that is mid-task and also being
  // spoken to should look like it is being spoken to.
  | "listening"
  | "thinking"
  | "speaking"
  // Asleep. The presence machine can hold it and the painter has a treatment
  // for it, so it belongs in this union too: leaving it out would make the
  // label and glow lookups unsound at exactly the point where they are read.
  | "asleep"
  // A wait with a reason attached. It is deliberately its own state and not an
  // alias for "verifying": context compaction is the one wait where nothing is
  // happening and everything looks like it is, so it gets a dim, slow, honest
  // treatment rather than a sweep that claims progress it cannot measure.
  | "compacting";

export interface OrbPlacement {
  x: number;
  y: number;
  size: number;
  /** Why it is there · surfaced in the orb's title so its behaviour is legible. */
  anchor: "focused" | "crowded" | "empty";
}

export const ORB_SIZE = 132;
export const ORB_MIN = 56;
const MARGIN = 18;

/**
 * An empty room is the one moment the core can be the room. This is the size it
 * takes when it has the whole stage to itself · deliberately larger than
 * ORB_SIZE, because "no surface open" means there is nothing to sit beside, and
 * a 132px dot in a 1600px room still reads as a corner ornament.
 */
export const ORB_HERO = 300;

const STATE_BY_EVENT: Array<[RegExp, OrbState]> = [
  [/emergency|red_line|blocked/i, "blocked"],
  [/disagreement|breach|repair_stopped|refus/i, "attention"],
  [/verification|verif/i, "verifying"],
  // Compaction is checked before the generic "something is happening" catch-all
  // below, because a wait and a task look identical in the tray and the whole
  // point of this state is that they must not. The runtime reports compaction
  // as an ordinary event kind, and every unrecognised kind lands in the tray
  // verbatim, so this matches a real signal rather than a guess.
  [/compact/i, "compacting"],
  [/mission_state|mission_opened|node|step|tool|agent\.|mission_repair\b/i, "working"],
  [/mission_finished|completed/i, "idle"],
];

/** The newest meaningful runtime signal decides what the core is showing. */
export function orbStateFor(entries: Array<{ label: string }>): OrbState {
  for (const entry of entries) {
    for (const [pattern, state] of STATE_BY_EVENT) {
      if (pattern.test(entry.label)) return state;
    }
  }
  return "idle";
}

function area(surface: Surface): number {
  return Math.max(0, surface.geometry.w) * Math.max(0, surface.geometry.h);
}

function clamp(value: number, min: number, max: number): number {
  if (max < min) return min;
  return Math.min(Math.max(value, min), max);
}

function coveredFraction(surfaces: Surface[], viewport: Viewport): number {
  const total = viewport.w * viewport.h;
  if (total <= 0) return 1;
  const covered = surfaces.reduce((sum, surface) => sum + area(surface), 0);
  return Math.min(1, covered / total);
}

/** The emptiest corner, used when the room is too full to sit beside anything. */
function freeCorner(surfaces: Surface[], viewport: Viewport, size: number): { x: number; y: number } {
  const bottom = Math.max(MARGIN, viewport.h - size - MARGIN);
  const candidates = [
    { x: MARGIN, y: MARGIN },
    { x: viewport.w - size - MARGIN, y: MARGIN },
    { x: MARGIN, y: bottom },
    { x: viewport.w - size - MARGIN, y: bottom },
  ];
  let best = candidates[candidates.length - 1];
  let bestOverlap = Number.POSITIVE_INFINITY;
  for (const spot of candidates) {
    let overlap = 0;
    for (const surface of surfaces) {
      const g = surface.geometry;
      const dx = Math.max(0, Math.min(spot.x + size, g.x + g.w) - Math.max(spot.x, g.x));
      const dy = Math.max(0, Math.min(spot.y + size, g.y + g.h) - Math.max(spot.y, g.y));
      overlap += dx * dy;
    }
    if (overlap < bestOverlap) {
      bestOverlap = overlap;
      best = spot;
    }
  }
  return best;
}

/**
 * Place the core next to the surface that matters.
 *
 * Rules, in order: no visible surfaces means it takes the middle of the room at
 * hero size, because with nothing to sit beside it should BE the room; a
 * crowded room means it shrinks to ORB_MIN and finds the emptiest corner rather
 * than covering the work; otherwise it sits just outside the focused surface on
 * whichever side has room.
 */
export function placeOrb(visible: Surface[], viewport: Viewport): OrbPlacement {
  if (!visible.length) {
    // Centre of the room, and never bigger than the room can hold.
    const size = Math.max(ORB_MIN, Math.min(ORB_HERO, Math.min(viewport.w, viewport.h) - MARGIN * 2));
    return {
      x: Math.round((viewport.w - size) / 2),
      y: Math.round((viewport.h - size) / 2),
      size: Math.round(size),
      anchor: "empty",
    };
  }

  const focused = visible.reduce((top, surface) => (surface.geometry.z >= top.geometry.z ? surface : top), visible[0]);

  if (coveredFraction(visible, viewport) > 0.55) {
    const size = ORB_MIN;
    const spot = freeCorner(visible, viewport, size);
    return { ...spot, size, anchor: "crowded" };
  }

  const g = focused.geometry;
  const roomRight = viewport.w - (g.x + g.w) - MARGIN * 2;
  const roomLeft = g.x - MARGIN * 2;
  let x: number;
  if (roomRight >= ORB_SIZE) x = g.x + g.w + MARGIN;
  else if (roomLeft >= ORB_SIZE) x = g.x - ORB_SIZE - MARGIN;
  else {
    const spot = freeCorner(visible, viewport, ORB_SIZE);
    return { ...spot, size: ORB_SIZE, anchor: "crowded" };
  }
  const y = clamp(g.y + Math.round(g.h / 2) - ORB_SIZE / 2, MARGIN, Math.max(MARGIN, viewport.h - ORB_SIZE - MARGIN));
  return { x: Math.round(x), y: Math.round(y), size: ORB_SIZE, anchor: "focused" };
}

export const STATE_LABEL: Record<OrbState, string> = {
  idle: "idle",
  working: "working",
  verifying: "verifying",
  attention: "needs attention",
  blocked: "blocked",
  asleep: "asleep",
  listening: "listening",
  thinking: "thinking",
  speaking: "speaking",
  compacting: "compacting context",
};

/**
 * How loud a state is allowed to be, 0..1.
 *
 * This is the honest part of the palette. A state that needs the user to do
 * something is bright; a wait with nothing to report is dim; a dead stop is
 * nearly out. Colour is the only channel that survives when motion is
 * correctly switched off, so it has to carry the whole message on its own in
 * "asleep" and "blocked".
 */
export const STATE_GLOW: Record<OrbState, number> = {
  idle: 0.5,
  working: 0.95,
  verifying: 0.8,
  attention: 1,
  blocked: 0.22,
  // Asleep is the floor. An orb that keeps advertising itself while nothing is
  // happening is a notification that never stops.
  asleep: 0.12,
  // Listening is open and even. It is waiting on you, not working.
  listening: 0.7,
  thinking: 0.75,
  speaking: 0.9,
  // Dim on purpose. It is a wait, and a wait that looks busy reads as a hang.
  compacting: 0.35,
};

/**
 * Count the real signals the room has reported.
 *
 * The orb's ring is allowed to turn by this number and by nothing else, so
 * this function is the seam between "the runtime said something" and "the orb
 * moved". It counts tray entries, which are the runtime's own readouts, and
 * that count is monotonic: a tray that is trimmed or cleared does not make the
 * ring spin backwards, because the count only ever goes up.
 */
export function eventCountFor(entries: ReadonlyArray<{ at: number }>): number {
  // Newest first, so the newest timestamp is the room's current clock reading.
  // Distinct timestamps are what make two entries two events rather than one
  // entry pushed twice.
  const seen = new Set<number>();
  for (const entry of entries) seen.add(entry.at);
  return seen.size;
}

// --- audio level bus -------------------------------------------------------
//
// A live microphone produces a level value many times a second, and the only
// owner of that value is whichever component opened the mic. So the level has
// to travel through a module-level bus rather than through props, exactly like
// pan does, for exactly the same reason: routing it through the render tree
// re-renders the orb, the backdrop and every open panel several times a second
// to move a ring.
//
// The important design decision is what "no source" means. It is `null`, and
// it is NOT the same as 0. A room with nobody talking in it measures 0. A
// machine with no microphone tap attached measures nothing at all. Painting a
// waveform for the second case is how an orb ends up looking like it is
// hearing a room that does not exist, so the null is carried all the way to
// the painter and shown in words.

export type LevelSourceKind = "mic" | "playback";

/** Reads a real 0..1 amplitude, or null when there is no real data. */
export type LevelProbe = () => number | null;

let micProbe: LevelProbe | null = null;
let playbackProbe: LevelProbe | null = null;

/**
 * Register a real level source.
 *
 * The caller passes a getter rather than a value on purpose: a getter is read
 * once per frame from the orb's rAF loop, so nothing here can trigger a React
 * render no matter how fast the level moves.
 */
export function setLevelSource(kind: LevelSourceKind, probe: LevelProbe | null): void {
  if (kind === "mic") micProbe = probe;
  else playbackProbe = probe;
}

/** Forget every level source. Used by tests and by teardown. */
export function clearLevelSources(): void {
  micProbe = null;
  playbackProbe = null;
}

/** True when something is really attached and reporting. */
export function hasLevelSource(kind: LevelSourceKind): boolean {
  return (kind === "mic" ? micProbe : playbackProbe) !== null;
}

/**
 * Read a level, defensively.
 *
 * A probe is a function someone else owns, and a function that throws must not
 * take the orb's frame loop down with it. Anything non-finite becomes null,
 * which is the honest "no usable number" answer.
 */
export function readLevel(kind: LevelSourceKind): number | null {
  const probe = kind === "mic" ? micProbe : playbackProbe;
  if (!probe) return null;
  try {
    const value = probe();
    if (value === null || value === undefined || !Number.isFinite(value)) return null;
    return value < 0 ? 0 : value > 1 ? 1 : value;
  } catch {
    return null;
  }
}
