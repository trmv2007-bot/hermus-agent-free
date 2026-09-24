// Where the core sits, and what it is doing.
//
// The orb is not decoration: its position answers "where is the work" and its
// motion answers "what is happening right now". Both are derived from real
// workspace state here, as pure functions, so the rules can be tested without a
// browser and the renderer stays a dumb painter.

import type { Surface, Viewport } from "./surfaces";

export type OrbState = "idle" | "working" | "verifying" | "attention" | "blocked";

export interface OrbPlacement {
  x: number;
  y: number;
  size: number;
  /** Why it is there — surfaced in the orb's title so its behaviour is legible. */
  anchor: "focused" | "crowded" | "empty";
}

export const ORB_SIZE = 132;
export const ORB_MIN = 56;
const MARGIN = 18;

const STATE_BY_EVENT: Array<[RegExp, OrbState]> = [
  [/emergency|red_line|blocked/i, "blocked"],
  [/disagreement|breach|repair_stopped|refus/i, "attention"],
  [/verification|verif/i, "verifying"],
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
 * Rules, in order: no visible surfaces means it holds the corner and stays full
 * size; a crowded room means it shrinks and finds the emptiest corner rather
 * than covering the work; otherwise it sits just outside the focused surface on
 * whichever side has room.
 */
export function placeOrb(visible: Surface[], viewport: Viewport): OrbPlacement {
  if (!visible.length) {
    return { x: viewport.w - ORB_SIZE - MARGIN, y: viewport.h - ORB_SIZE - MARGIN, size: ORB_SIZE, anchor: "empty" };
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
};
