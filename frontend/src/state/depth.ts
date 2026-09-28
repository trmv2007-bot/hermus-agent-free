/**
 * How much of the core is currently spoken for.
 *
 * Flat compositing is the reason a layout with a glowing object in the middle
 * can read as a diagram rather than a room. A real camera has a focal plane,
 * and things behind other things go soft. Without that, the core is either
 * competing with every panel drawn over it or hidden behind them, and there
 * is no third option.
 *
 * This is that third option, and it is a number rather than a boolean: how much
 * of the core a panel is sitting on top of. One value drives dimming, blur and
 * a slight recession, so the core reads as further away when something is in
 * front of it and as close when the room is clear.
 *
 * It is coverage, not intersection, on purpose. A panel that clips a corner of
 * a 300px core is already visually in the way, and a test that only fires on
 * the centre would leave that case feeling flat.
 */

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Overlap area of two rects. Zero when they do not touch. */
export function overlapArea(a: Rect, b: Rect): number {
  const x = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const y = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  return x * y;
}

/** The area of a, for coverage. Guards a zero-sized rect from dividing by zero. */
export function area(r: Rect): number {
  return Math.max(1, r.w * r.h);
}

/**
 * 0 when nothing is in front of the core, 1 when it is fully covered.
 *
 * Softly saturating rather than linear, because a core that dims to nothing
 * after a panel moves one pixel is startling. The curve means the first bit of
 * overlap already reads, and full darkness still takes a lot of covering.
 */
export function coverage(core: Rect, surfaces: Rect[]): number {
  const total = area(core);
  let covered = 0;
  for (const s of surfaces) {
    covered += overlapArea(core, s);
  }
  const raw = Math.min(1, covered / total);
  return 1 - Math.pow(1 - raw, 1.6);
}

/** What the painter needs, derived once so the two channels cannot disagree. */
export interface Recession {
  /** 0..1, how covered. */
  cover: number;
  /** Multiplier on the glow. */
  glow: number;
  /** CSS blur in px. */
  blur: number;
  /** Multiplier on apparent size; a covered core recedes rather than shrinks. */
  scale: number;
}

export function recessionFor(cover: number): Recession {
  const c = Math.min(1, Math.max(0, cover));
  return {
    cover: c,
    glow: 1 - 0.55 * c,
    blur: 2.6 * c,
    // Slight recession, not shrinkage -- a smaller core reads as a smaller
    // core, whereas a dimmer and softer one reads as further away.
    scale: 1 - 0.06 * c,
  };
}
