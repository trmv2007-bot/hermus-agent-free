// Screen pixels and world coordinates are not the same number, and the gap
// between them is the whole reason a dragged object either lags the cursor,
// races ahead of it, or "holds further away every time you grab it".
//
// The conversion has to undo three things at once:
//
//   1. the stage's own offset on the page — clientX is viewport-relative and
//      the stage sits below a topbar, so it is not stage-relative
//   2. the pan, which is already in screen pixels
//   3. the zoom, which scales the world layer
//
// Getting (1) wrong is the subtle one. Dividing by the zoom alone leaves the
// stage origin sitting in the grab offset, so the object jumps the instant you
// press it and the error is baked into the offset — so the next grab starts
// from a position that is already wrong, and it gets further away each time.
//
// It lives here as a pure function so it can be tested directly. The same maths
// is the inverse of what `zoomAt` does in the store, which is why the two are
// written as the same pair of equations rather than independently.

/** Where the stage sits on the page, and where you are looking within it. */
export interface ScreenFrame {
  /** The stage's left edge in viewport coordinates. */
  originX: number;
  originY: number;
  panX: number;
  panY: number;
  zoom: number;
}

/** Viewport coordinates -> world coordinates. */
export function screenToWorld(frame: ScreenFrame, clientX: number, clientY: number): { x: number; y: number } {
  const zoom = frame.zoom || 1;
  return {
    x: (clientX - frame.originX - frame.panX) / zoom,
    y: (clientY - frame.originY - frame.panY) / zoom,
  };
}

/**
 * The offset between where you pressed and the object's world origin.
 *
 * Held for the life of the drag and subtracted on every move, which is what
 * makes the object stay under the cursor instead of snapping its centre to it.
 */
export function grabOffset(
  frame: ScreenFrame,
  clientX: number,
  clientY: number,
  originX: number,
  originY: number,
): { dx: number; dy: number } {
  const world = screenToWorld(frame, clientX, clientY);
  return { dx: world.x - originX, dy: world.y - originY };
}

/** Inverse of `screenToWorld` — used by tests and by hit-testing. */
export function worldToScreen(frame: ScreenFrame, worldX: number, worldY: number): { x: number; y: number } {
  const zoom = frame.zoom || 1;
  return {
    x: worldX * zoom + frame.panX + frame.originX,
    y: worldY * zoom + frame.panY + frame.originY,
  };
}

/**
 * A pointer MOVEMENT, in world units.
 *
 * Deliberately separate from `screenToWorld`, because a move only needs the
 * zoom — the stage origin and the pan cancel out when you subtract two screen
 * positions. Routing a move through `screenToWorld` instead is a real bug that
 * looks correct at 1:1 and drifts at every other zoom: the object's stored
 * origin gets divided by the zoom on every frame, so it slides away from where
 * it was as you drag.
 */
export function screenDeltaToWorld(frame: Pick<ScreenFrame, "zoom">, dx: number, dy: number): { x: number; y: number } {
  const zoom = frame.zoom || 1;
  return { x: dx / zoom, y: dy / zoom };
}
