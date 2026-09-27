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
//   3. Panning must not re-render React. It used to live in the workspace store,
//      so every keypress made a new `stage` object and re-rendered the stage,
//      the backdrop and EVERY open panel — thirty times a second while a key was
//      held. That was the jitter. The pan is a transform on one element, so it
//      is written to that element directly and React never hears about it.
//   4. Zoom DOES stay in the store, because panel drag maths reads it. It
//      changes on a wheel notch rather than on key repeat, so it costs nothing.

import { useCallback, useEffect, useRef } from "react";
import { useWorkspace } from "../state/workspace-store";
import { getPan, movePan, resetPan, subscribePan } from "../state/pan";
import { ActivityRail } from "./ActivityRail";

/** Pixels the pointer must travel before a press counts as a drag, not a click. */
const DRAG_SLOP = 4;

export function StageCanvas({ children }: { children: React.ReactNode }) {
  const stageRef = useRef<HTMLElement | null>(null);
  const worldRef = useRef<HTMLDivElement | null>(null);
  const zoom = useWorkspace((s) => s.stage.zoom);
  const zoomAt = useWorkspace((s) => s.zoomAt);
  const resetStage = useWorkspace((s) => s.resetStage);

  // Refs, not state, so the transform can be written without a render.
  const panRef = useRef(getPan());
  const zoomRef = useRef(zoom);

  /** The one place the transform is written. */
  const applyTransform = useCallback(() => {
    const node = worldRef.current;
    if (!node) return;
    const { x, y } = panRef.current;
    node.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${zoomRef.current})`;
  }, []);

  useEffect(() => {
    panRef.current = getPan();
  }, []);

  useEffect(() => {
    zoomRef.current = zoom;
    applyTransform();
  }, [zoom, applyTransform]);

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

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLElement>) => {
    const held = drag.current;
    if (!held || held.id !== event.pointerId) return;
    const dx = event.clientX - held.x;
    const dy = event.clientY - held.y;
    if (!held.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
    held.moved = true;
    held.x = event.clientX;
    held.y = event.clientY;
    movePan(dx, dy);
  }, []);

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
      // A wheel over a panel that can still scroll in that direction belongs to
      // the panel, not to the room.
      //
      // Without this, every attempt to read past the bottom of a panel zooms the
      // entire room, and a panel with no overflow left cannot be scrolled at
      // all because there is no fallback. That is the worst version of the bug:
      // the gesture does something, just never the thing you asked for.
      const target = event.target as HTMLElement | null;
      if (target && target !== node) {
        const scroller = target.closest<HTMLElement>(".surface-body, .panel, [data-scrollable]");
        if (scroller && scroller !== node) {
          const canScroll = (axis: "y" | "x") => {
            const primary = axis === "y" ? "scrollHeight" : "scrollWidth";
            const box = axis === "y" ? "clientHeight" : "clientWidth";
            const extent = scroller[primary] - scroller[box];
            if (extent <= 1) return false;
            // At the end of its travel, the panel is done and the room should
            // take the gesture back. Otherwise a panel scrolled to the end
            // traps the wheel and the room can never be zoomed from over it.
            const delta = axis === "y" ? event.deltaY : event.deltaX;
            const pos = axis === "y" ? scroller.scrollTop : scroller.scrollLeft;
            if (delta > 0) return pos < extent - 1;
            if (delta < 0) return pos > 1;
            return false;
          };
          if (canScroll("y") || (event.deltaX !== 0 && canScroll("x"))) return;
        }
      }
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
      resetPan();
    },
    [resetStage],
  );

  useEffect(() => subscribePan(() => applyTransform()), [applyTransform]);

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
        No grid here. The floor is the backdrop's, driven by the same two
        numbers — see `.bd-grid`. Drawing a second one inside the stage was what
        made the floor beat against itself.
      */}
      <div className="world" ref={worldRef}>
        {children}
      </div>

      {/*
        Outside the world layer on purpose. The rail is about the ROOM, so it
        stays put while you pan and zoom the thing it is describing — a readout
        that scrolls off screen with the content is not a readout.
      */}
      <ActivityRail />
    </main>
  );
}


