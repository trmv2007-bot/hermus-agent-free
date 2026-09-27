// The room as a canvas you can move through, in the manner of ComfyUI.
//
// Three things it has to get right, and each one has a way to get it wrong:
//
//   1. Dragging the background pans. Not the panels — those are draggable by
//      their own headers, and the two gestures must not fight over the same
//      press. A drag only counts as a pan if it started on the stage itself, and
//      only if it travelled far enough to be a drag and not a click on a surface
//      that happened to arrive first.
//   2. The wheel zooms about the cursor. Multiplying the scale while leaving the
//      pan alone makes whatever sits under the pointer slide out from under it,
//      which is the single most disorienting thing a zoom can do.
//   3. The grid is drawn in SCREEN space, not world space, and the cell size is
//      derived from the zoom. A grid parented to the world layer would scale its
//      own line weight and go soft or vanish at the ends, and it would stop
//      looking like a floor.

import { useCallback, useEffect, useRef } from "react";
import { useWorkspace } from "../state/workspace-store";
import { clampZoom } from "../state/surfaces";

/** Pixels the pointer must travel before a press counts as a drag, not a click. */
const DRAG_SLOP = 4;

export function StageCanvas({ children }: { children: React.ReactNode }) {
  const stageRef = useRef<HTMLElement | null>(null);
  const pan = useWorkspace((s) => s.panBy);
  const zoomAt = useWorkspace((s) => s.zoomAt);
  const resetStage = useWorkspace((s) => s.resetStage);
  const { panX, panY, zoom } = useWorkspace((s) => s.stage);
  const world = { transform: `translate3d(${panX}px, ${panY}px, 0) scale(${zoom})` };

  // Held in refs, not state: re-rendering on every pointermove would fight the
  // drag it is supposed to be tracking.
  const drag = useRef<{ id: number; x: number; y: number; moved: boolean } | null>(null);

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      // Only a press on the stage background pans. A press on a panel belongs
      // to that panel, and hijacking it here is how "I clicked a button and the
      // room moved" happens.
      if (event.button !== 0 && event.button !== 1) return;
      if ((event.target as HTMLElement).closest(".surface, .orb, .fan, .dock, button, a, input, textarea, select")) return;
      drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const held = drag.current;
      if (!held || held.id !== event.pointerId) return;
      const dx = event.clientX - held.x;
      const dy = event.clientY - held.y;
      if (!held.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
      held.moved = true;
      held.x = event.clientX;
      held.y = event.clientY;
      pan(dx, dy);
    },
    [pan],
  );

  const onPointerUp = useCallback((event: React.PointerEvent<HTMLElement>) => {
    if (drag.current?.id === event.pointerId) drag.current = null;
  }, []);

  // Wheel is bound natively rather than through React's onWheel, because React
  // attaches wheel passively and a passive listener cannot preventDefault — so
  // the page would scroll behind the stage on every notch.
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const box = node.getBoundingClientRect();
      zoomAt(event.clientX - box.left, event.clientY - box.top, event.deltaY < 0 ? 1.12 : 1 / 1.12);
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  // Double-click empty space returns to the origin, which is the one gesture
  // every canvas app has and its absence is always noticed.
  const onDoubleClick = useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      if ((event.target as HTMLElement).closest(".surface, .orb, .fan, .dock")) return;
      resetStage();
    },
    [resetStage],
  );

  return (
    <main
      className="stage"
      ref={stageRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={onDoubleClick}
    >
      {/*
        The grid lives here, OUTSIDE the world layer, and reads the transform
        through CSS custom properties. The cells are sized and offset in screen
        space from those, so the floor stays crisp and evenly spaced at any
        zoom instead of scaling its own line weight.
      */}
      <StageGrid />
      <div className="world" style={world}>
        {children}
      </div>
    </main>
  );
}

function StageGrid() {
  const { panX, panY, zoom } = useWorkspace((s) => s.stage);
  // A 32px cell in world space is ~24px on screen at 0.75x and ~40px at 1.25x.
  // Below about 12px the lines turn into a solid wash, so the cell doubles
  // instead — the same trick ComfyUI and Figma use.
  const base = 32;
  let cell = base * zoom;
  while (cell < 12) cell *= 4;
  while (cell > 96) cell /= 4;

  return (
    <div
      className="stage-grid"
      aria-hidden="true"
      style={
        {
          "--cell": `${cell}px`,
          "--cell-x": `${((panX % cell) + cell) % cell}px`,
          "--cell-y": `${((panY % cell) + cell) % cell}px`,
        } as React.CSSProperties
      }
    />
  );
}

export { clampZoom };
