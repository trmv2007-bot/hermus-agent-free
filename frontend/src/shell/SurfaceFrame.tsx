// One surface on screen: its frame, its toolbar, and the only two ways geometry
// changes from the mouse — drag the header, drag the corner. Both call the
// workspace store, so an agent moving the same surface mid-drag is not a race
// against a component-local copy of the layout.

import { useRef } from "react";
import { useWorkspace } from "../state/workspace-store";
import { MIN_H, MIN_W, type Surface } from "../state/surfaces";
import { rendererFor } from "../surfaces/registry";

export function SurfaceFrame({ surface }: { surface: Surface }) {
  const moveSurface = useWorkspace((state) => state.moveSurface);
  const resizeSurface = useWorkspace((state) => state.resizeSurface);
  const focusSurface = useWorkspace((state) => state.focusSurface);
  const closeSurface = useWorkspace((state) => state.closeSurface);
  const minimizeSurface = useWorkspace((state) => state.minimizeSurface);
  const maximizeSurface = useWorkspace((state) => state.maximizeSurface);
  const unmaximizeSurface = useWorkspace((state) => state.unmaximizeSurface);
  const hideSurface = useWorkspace((state) => state.hideSurface);
  const dockSurface = useWorkspace((state) => state.dockSurface);
  const togglePin = useWorkspace((state) => state.togglePin);
  const viewport = useWorkspace((state) => state.viewport);
  const drag = useRef<{ dx: number; dy: number } | null>(null);
  const grow = useRef<{ ox: number; oy: number; w: number; h: number } | null>(null);
  const render = rendererFor(surface.kind);
  const geometry = surface.geometry;

  return (
    <section
      className={`surface ${surface.focused ? "focused" : ""} ${surface.dock !== "none" ? `docked docked-${surface.dock}` : ""}`}
      style={{ left: geometry.x, top: geometry.y, width: geometry.w, height: geometry.h, zIndex: geometry.z }}
      onPointerDown={() => focusSurface(surface.id)}
      aria-label={surface.title}
    >
      <header
        className="surface-bar"
        onPointerDown={(event) => {
          if ((event.target as HTMLElement).closest("button")) return;
          drag.current = { dx: event.clientX - geometry.x, dy: event.clientY - geometry.y };
          (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          moveSurface(surface.id, event.clientX - drag.current.dx, event.clientY - drag.current.dy);
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
      >
        <span className={`kind kind-${surface.kind}`} aria-hidden="true" />
        <h2 title={surface.title}>{surface.title}</h2>
        <span className={`source-tag tag-${surface.source.kind}`}>{surface.source.kind}</span>
        <div className="surface-tools">
          <button type="button" title="pin — survives a layout reset" className={surface.pinned ? "on" : ""} onClick={() => togglePin(surface.id)}>
            {surface.pinned ? "pinned" : "pin"}
          </button>
          <button type="button" title="dock left" onClick={() => dockSurface(surface.id, surface.dock === "left" ? "none" : "left")}>
            ◧
          </button>
          <button type="button" title="dock right" onClick={() => dockSurface(surface.id, surface.dock === "right" ? "none" : "right")}>
            ◨
          </button>
          <button
            type="button"
            title={surface.restoreGeometry ? "restore size" : "maximise"}
            onClick={() => (surface.restoreGeometry ? unmaximizeSurface(surface.id) : maximizeSurface(surface.id))}
          >
            {surface.restoreGeometry ? "❐" : "□"}
          </button>
          <button type="button" title="hide — stays open, takes no space" onClick={() => hideSurface(surface.id)}>
            ⌄
          </button>
          <button type="button" title="minimize" onClick={() => minimizeSurface(surface.id)}>
            –
          </button>
          <button type="button" title="close" onClick={() => closeSurface(surface.id)}>
            ×
          </button>
        </div>
      </header>

      <div className="surface-body">
        {render({ surfaceId: surface.id })}
      </div>

      <span
        className="grip"
        title="drag to resize"
        onPointerDown={(event) => {
          grow.current = { ox: event.clientX, oy: event.clientY, w: geometry.w, h: geometry.h };
          (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
          event.stopPropagation();
        }}
        onPointerMove={(event) => {
          if (!grow.current) return;
          resizeSurface(
            surface.id,
            Math.max(MIN_W, Math.min(grow.current.w + (event.clientX - grow.current.ox), viewport.w - geometry.x)),
            Math.max(MIN_H, Math.min(grow.current.h + (event.clientY - grow.current.oy), viewport.h - geometry.y)),
          );
        }}
        onPointerUp={() => {
          grow.current = null;
        }}
      />
    </section>
  );
}
