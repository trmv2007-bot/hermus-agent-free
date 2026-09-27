// Panning does not belong in React.
//
// Every keypress used to write panX/panY into the workspace store. That made a
// new `stage` object, which re-rendered every subscriber: the stage, the
// backdrop, and EVERY open panel — thirty times a second while a key is held
// down. That is the jitter. The pan is a transform on one element; there is no
// reason for a tree to re-render to move a div.
//
// So pan lives here, in a plain object, and the stage writes it straight to the
// element's style. Zero renders per pan.
//
// Zoom DOES stay in the store: it is read by panel drag maths, and it changes
// on a wheel notch rather than on key repeat, so its render cost is nothing.

export interface PanState {
  x: number;
  y: number;
}

const pan: PanState = { x: 0, y: 0 };

type Listener = (pan: PanState) => void;
const listeners = new Set<Listener>();

/** Read without subscribing. Safe to call in a rAF or an event handler. */
export function getPan(): PanState {
  return pan;
}

export function setPan(x: number, y: number): void {
  if (pan.x === x && pan.y === y) return;
  pan.x = x;
  pan.y = y;
  for (const listener of listeners) listener(pan);
}

/** Nudge the pan. Returns the new position. */
export function movePan(dx: number, dy: number): PanState {
  setPan(pan.x + dx, pan.y + dy);
  return pan;
}

export function resetPan(): void {
  setPan(0, 0);
}

export function subscribePan(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Coalesce a burst of moves into one write per frame.
 *
 * Key repeat delivers around 30 events a second and a 60Hz display can only
 * show 16 of them, so two thirds of the work was being thrown away after
 * already being done. This runs the last value of the burst on the next frame
 * instead, which is both cheaper and what the eye expects: the room tracks the
 * key instead of stuttering toward it.
 */
export function coalescePan(dx: number, dy: number): void {
  pendingX += dx;
  pendingY += dy;
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    const x = pendingX;
    const y = pendingY;
    pendingX = 0;
    pendingY = 0;
    movePan(x, y);
  });
}

let pendingX = 0;
let pendingY = 0;
let frame = 0;
