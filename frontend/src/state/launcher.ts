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
/** The fan sweeps this much of a circle, centred on the diagonal of the open quadrant. */
export const ARC_SPAN_DEG = 100;
const SLOT_GAP = 8;
const MIN_RADIUS = 92;

/** Kinds the operator can call up by hand. Every one either renders a real panel
 * or says plainly that it is not built. */
export const LAUNCHER_KINDS: SurfaceKind[] = ["chat", "mission", "worker", "evidence", "model", "telemetry", "logs", "computer", "memory", "terminal", "diagnostics"];

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
};

/**
 * Radius wide enough that `count` slots of `size` never touch each other along
 * the arc, with a floor so a single entry does not sit on top of the pod.
 */
export function fanRadius(count: number, size = SLOT_SIZE, spanDeg = ARC_SPAN_DEG): number {
  if (count <= 1) return MIN_RADIUS;
  const spanRad = (Math.min(spanDeg, 180) * Math.PI) / 180;
  const needed = ((count - 1) * (size + SLOT_GAP)) / spanRad;
  return Math.max(MIN_RADIUS, Math.round(needed));
}

export interface FanSlot {
  x: number;
  y: number;
  kind: SurfaceKind;
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
): FanSlot[] {
  const cx = anchor.x + podSize / 2;
  const cy = anchor.y + podSize / 2;
  const rightward = cx * 2 <= viewport.w;
  const downward = cy * 2 <= viewport.h;
  // Screen y grows downward, so the diagonal of the open quadrant is:
  const diagonal = Math.atan2(downward ? 1 : -1, rightward ? 1 : -1) * (180 / Math.PI);
  const radius = fanRadius(kinds.length, slotSize);
  const spread = kinds.length > 1 ? ARC_SPAN_DEG : 0;
  const max = Math.max(EDGE, viewport.w - slotSize - EDGE);
  const maxY = Math.max(EDGE, viewport.h - slotSize - EDGE);

  return kinds.map((kind, index) => {
    const t = kinds.length > 1 ? index / (kinds.length - 1) : 0.5;
    const degrees = diagonal - spread / 2 + spread * t;
    const radians = degrees * (Math.PI / 180);
    const x = Math.min(Math.max(cx + radius * Math.cos(radians) - slotSize / 2, EDGE), max);
    const y = Math.min(Math.max(cy + radius * Math.sin(radians) - slotSize / 2, EDGE), maxY);
    return { kind, x: Math.round(x), y: Math.round(y) };
  });
}
