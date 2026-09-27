// Where the launch fan opens.
//
// The pod can be dragged into any corner, so the arc has to bend away from the
// edges rather than spill off screen. That is geometry, not styling, and it is
// derived here so the rules hold at any viewport size instead of depending on
// where the window happens to be.

import type { SurfaceKind, Viewport } from "./surfaces";

export const POD_SIZE = 46;
export const SLOT_SIZE = 38;
export const EDGE = 12;
/**
 * The fan sweeps this much of a circle when it opens to one side.
 *
 * When the core is hero-sized and centred there is nothing to point away from,
 * so the fan opens all the way round instead — see `heroSpreadDeg`.
 */
export const ARC_SPAN_DEG = 100;
/** A centred, room-sized core has no "away" to face, so the fan rings it. */
export const HERO_SPAN_DEG = 360;
const SLOT_GAP = 8;
const MIN_RADIUS = 92;
/** Air between the core's edge and the nearest entry, so a slot never overlaps
 *  the thing it is supposed to point at. */
export const RING_CLEARANCE = 34;

/** Kinds the operator can call up by hand. Every one either renders a real panel
 * or says plainly that it is not built. */
export const LAUNCHER_KINDS: SurfaceKind[] = ["chat", "mission", "worker", "evidence", "model", "telemetry", "logs", "computer", "memory", "terminal", "settings", "diagnostics", "voice"];

export const KIND_GLYPH: Record<SurfaceKind, string> = {
  chat: "◉",
  mission: "◆",
  worker: "▣",
  evidence: "❐",
  model: "◈",
  memory: "◍",
  files: "▤",
  ide: "⌗",
  terminal: "▸",
  diff: "≈",
  logs: "≡",
  telemetry: "∿",
  computer: "▦",
  media: "◐",
  diagnostics: "⚙",
  voice: "◍",
  settings: "⚙",
};

/**
 * Radius wide enough that `count` slots of `size` never touch each other along
 * the arc, with a floor so a single entry does not sit on top of the pod.
 *
 * A full ring is spaced by circumference instead of by arc length: eleven slots
 * around 360 degrees at the same radius as a 100-degree fan would overlap badly.
 */
export function fanRadius(
  count: number,
  size = SLOT_SIZE,
  spanDeg = ARC_SPAN_DEG,
  innerRadius = 0,
): number {
  // Whatever else the maths says, the ring has to clear the core it is drawn
  // around. A 300px hero core with a 92px ring puts every entry inside the orb,
  // where the orb's own hit area swallows the clicks.
  const floor = Math.max(MIN_RADIUS, innerRadius + RING_CLEARANCE);
  if (count <= 1) return floor;
  if (spanDeg >= 360) {
    // A ring is spaced by circumference, not arc length.
    const needed = (count * (size + SLOT_GAP)) / (2 * Math.PI);
    return Math.max(floor, Math.round(needed));
  }
  const spanRad = (Math.min(spanDeg, 180) * Math.PI) / 180;
  const needed = ((count - 1) * (size + SLOT_GAP)) / spanRad;
  return Math.max(floor, Math.round(needed));
}

export interface FanSlot {
  x: number;
  y: number;
  kind: SurfaceKind;
  /** Degrees from the anchor, so the renderer can stagger entries along the arc. */
  angle: number;
}

/**
 * Continuous fan angle instead of a per-quadrant jump.
 *
 * The fan used to snap between four fixed diagonals — one per quadrant — so
 * dragging the pod across the midpoint of the screen made all eleven entries
 * teleport at once. This measures how far the anchor sits from the room's
 * centre and swings the arc with it, so the fan tracks the pointer continuously
 * and the entries slide rather than jump.
 */
export function fanCenterDeg(anchor: { x: number; y: number }, podSize: number, viewport: Viewport): number {
  const cx = anchor.x + podSize / 2;
  const cy = anchor.y + podSize / 2;
  // -1..1 on each axis: 0 dead centre, ±1 hard against an edge.
  const nx = viewport.w > 0 ? clampUnit((cx * 2 - viewport.w) / viewport.w) : 0;
  const ny = viewport.h > 0 ? clampUnit((cy * 2 - viewport.h) / viewport.h) : 0;
  // Point the fan away from centre, so a pod on the left opens leftward and one
  // on the right opens rightward. Screen y grows downward, so +y is downward.
  const awayX = -nx;
  const awayY = -ny;
  if (awayX === 0 && awayY === 0) return 45;
  return (Math.atan2(awayY, awayX) * 180) / Math.PI;
}

function clampUnit(value: number): number {
  return Math.min(1, Math.max(-1, value));
}

/**
 * Lay `kinds` out on an arc around the pod, opening toward the roomier quadrant.
 *
 * The pod's centre anchors the fan; each slot is the centre point minus half its
 * own size, then clamped inside the viewport so a pod against an edge still
 * yields clickable entries.
 */
export function fanSlots(
  anchor: { x: number; y: number },
  viewport: Viewport,
  kinds: SurfaceKind[] = LAUNCHER_KINDS,
  podSize = POD_SIZE,
  slotSize = SLOT_SIZE,
  spanDeg: number = ARC_SPAN_DEG,
  innerRadius = 0,
): FanSlot[] {
  const cx = anchor.x + podSize / 2;
  const cy = anchor.y + podSize / 2;
  const ring = spanDeg >= 360;
  // A ring has no diagonal to point along, so it is anchored to the top (270deg
  // in screen terms, since y grows downward) and distributed from there.
  const diagonal = ring ? -90 : fanCenterDeg(anchor, podSize, viewport);
  const radius = fanRadius(kinds.length, slotSize, spanDeg, innerRadius);
  const spread = kinds.length > 1 ? spanDeg : 0;
  const max = Math.max(EDGE, viewport.w - slotSize - EDGE);
  const maxY = Math.max(EDGE, viewport.h - slotSize - EDGE);

  return kinds.map((kind, index) => {
    // On a ring the first entry sits at the top and the rest divide the
    // remaining arc evenly, so no slot lands under another.
    const t = kinds.length > 1 ? (ring ? (index + 0.5) / kinds.length : index / (kinds.length - 1)) : 0.5;
    const degrees = diagonal - spread / 2 + spread * t;
    const radians = degrees * (Math.PI / 180);
    const x = Math.min(Math.max(cx + radius * Math.cos(radians) - slotSize / 2, EDGE), max);
    const y = Math.min(Math.max(cy + radius * Math.sin(radians) - slotSize / 2, EDGE), maxY);
    return { kind, x: Math.round(x), y: Math.round(y), angle: Math.round(degrees) };
  });
}
