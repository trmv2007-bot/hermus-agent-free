// The depth field behind the surfaces.
//
// Three stacked layers and one animation frame: the parallax writes CSS custom
// properties directly instead of React state, so moving the mouse never causes a
// surface to re-render.

import { useEffect, useRef } from "react";
import { useWorkspace } from "../state/workspace-store";
import { getPan, subscribePan } from "../state/pan";
import { gridCell } from "../state/grid";

/** The world-space cell. Everything else derives from this. */
export function Backdrop() {
  const ref = useRef<HTMLDivElement>(null);
  const zoom = useWorkspace((state) => state.stage.zoom);

  // The floor follows the same pan and zoom as the world layer, so panning moves
  // the ground with the panels instead of sliding the room over a static floor.
  // Subscribed to rather than read from state, because panning does not go
  // through React — this is one style write, not a re-render of the tree.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const paint = () => {
      const { x, y } = getPan();
      const cell = gridCell(zoom);
      el.style.setProperty("--cell", `${cell}px`);
      el.style.setProperty("--cell-x", `${((x % cell) + cell) % cell}px`);
      el.style.setProperty("--cell-y", `${((y % cell) + cell) % cell}px`);
    };
    paint();
    return subscribePan(paint);
  }, [zoom]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    let frame = 0;
    let targetX = 0;
    let targetY = 0;
    let currentX = 0;
    let currentY = 0;

    const onMove = (event: PointerEvent) => {
      targetX = event.clientX / Math.max(1, window.innerWidth) - 0.5;
      targetY = event.clientY / Math.max(1, window.innerHeight) - 0.5;
    };

    const tick = () => {
      currentX += (targetX - currentX) * 0.06;
      currentY += (targetY - currentY) * 0.06;
      el.style.setProperty("--px", currentX.toFixed(4));
      el.style.setProperty("--py", currentY.toFixed(4));
      frame = requestAnimationFrame(tick);
    };

    const onVisibility = () => {
      cancelAnimationFrame(frame);
      if (!document.hidden) frame = requestAnimationFrame(tick);
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <div className="backdrop" ref={ref} aria-hidden="true">
      <div className="bd-grid" />
      <div className="bd-glow" />
      <div className="bd-vignette" />
    </div>
  );
}
