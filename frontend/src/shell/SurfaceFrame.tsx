// One surface on screen: its frame, its title bar, and the only two ways geometry
// changes from the mouse — drag the header, drag an edge.
//
// The toolbar is deliberately two buttons. It used to carry seven (pin, dock
// left, dock right, maximise, hide, minimize, close), which is a control surface
// in a room whose whole premise is that the thing on screen is the interface.
// Docking and pinning still work — they are store actions, reachable from the
// agent and from `reset layout` — they just no longer compete for the header.
//
// Geometry is owned by the store, so an agent moving the same surface mid-drag is
// not a race against a component-local copy of the layout.

import { useRef, useState } from "react";
import { useWorkspace } from "../state/workspace-store";
import { screenDeltaToWorld } from "../state/coords";
import { MIN_H, MIN_W, type Surface } from "../state/surfaces";
import { rendererFor } from "../surfaces/registry";

/** Which edges a handle pulls. A corner is two of them. */
type Dir = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

const HANDLES: Array<{ dir: Dir; className: string }> = [
  { dir: "n", className: "edge-n" },
  { dir: "s", className: "edge-s" },
  { dir: "e", className: "edge-e" },
  { dir: "w", className: "edge-w" },
  { dir: "ne", className: "edge-ne" },
  { dir: "nw", className: "edge-nw" },
  { dir: "se", className: "edge-se" },
  { dir: "sw", className: "edge-sw" },
];

export function SurfaceFrame({ surface }: { surface: Surface }) {
  const moveSurface = useWorkspace((state) => state.moveSurface);
  const resizeSurface = useWorkspace((state) => state.resizeSurface);
  // Pointer deltas are screen pixels; surface geometry is world coordinates.
  // See state/coords.ts for why the stage origin has to come out too.
  const stage = useWorkspace((state) => state.stage);
  const focusSurface = useWorkspace((state) => state.focusSurface);
  const closeSurface = useWorkspace((state) => state.closeSurface);
  const minimizeSurface = useWorkspace((state) => state.minimizeSurface);
  const viewport = useWorkspace((state) => state.viewport);
  const drag = useRef<{ x: number; y: number; gx: number; gy: number } | null>(null);
  const grow = useRef<{ ox: number; oy: number; w: number; h: number; x: number; y: number; dir: Dir } | null>(null);
  // Held geometry drives the lifted look and the live readout. It is component
  // state on purpose: it is view furniture, and the store stays the layout owner.
  const [held, setHeld] = useState(false);
  const render = rendererFor(surface.kind);
  const geometry = surface.geometry;

  /**
   * Resize from any edge, not just the bottom-right corner.
   *
   * Pulling a west or north edge has to move the origin as well as change the
   * size — otherwise the panel grows away from the edge you grabbed and the
   * handle you are holding slides out from under the pointer. Size is clamped
   * to MIN_W/MIN_H and to the room, and when a clamp bites the origin is pulled
   * back so the panel can never be pushed off the top or left of the stage.
   */
  const pull = (event: React.PointerEvent<HTMLElement>, dir: Dir) => {
    const held0 = grow.current;
    if (!held0) return;
    // The stage is zoomable, and pointer deltas arrive in SCREEN pixels while
    // surfaces store WORLD coordinates. Without dividing by the zoom, dragging
    // an edge at 2x moves the panel twice as far as the cursor travelled — the
    // classic "resizing feels broken when zoomed" bug. A move is a delta, so
    // only the zoom applies.
    const step = screenDeltaToWorld(stage, event.clientX - held0.ox, event.clientY - held0.oy);
    const dx = step.x;
    const dy = step.y;

    let { x, y, w, h } = held0;

    if (dir.includes("e")) w = held0.w + dx;
    if (dir.includes("s")) h = held0.h + dy;
    if (dir.includes("w")) {
      w = held0.w - dx;
      x = held0.x + dx;
    }
    if (dir.includes("n")) {
      h = held0.h - dy;
      y = held0.y + dy;
    }

    // Clamp size, then correct the origin so the opposite edge stays put.
    if (w < MIN_W) {
      if (dir.includes("w")) x -= MIN_W - w;
      w = MIN_W;
    }
    if (h < MIN_H) {
      if (dir.includes("n")) y -= MIN_H - h;
      h = MIN_H;
    }
    // Keep the panel on the stage.
    if (x < 0) {
      w += x;
      x = 0;
      if (w < MIN_W) w = MIN_W;
    }
    if (y < 0) {
      h += y;
      y = 0;
      if (h < MIN_H) h = MIN_H;
    }
    w = Math.min(w, viewport.w - x);
    h = Math.min(h, viewport.h - y);

    if (x !== geometry.x || y !== geometry.y) moveSurface(surface.id, x, y);
    if (w !== geometry.w || h !== geometry.h) resizeSurface(surface.id, w, h);
  };

  return (
    <section
      className={`surface ${surface.focused ? "focused" : ""} ${held ? "held" : ""} ${
        surface.dock !== "none" ? `docked docked-${surface.dock}` : ""
      }`}
      style={{ left: geometry.x, top: geometry.y, width: geometry.w, height: geometry.h, zIndex: geometry.z }}
      onPointerDown={() => focusSurface(surface.id)}
      aria-label={surface.title}
    >
      <span className="hud-corners" aria-hidden="true" />

      {/* Eight handles. The old single bottom-right grip was technically
          resizable and practically not — a 14px dot in the corner of a window
          is a thing you have to aim at. Edges are what every window uses. */}
      {HANDLES.map(({ dir, className }) => (
        <span
          key={dir}
          className={`resize-handle ${className}`}
          data-dir={dir}
          role="separator"
          aria-label={`resize from the ${dir} edge`}
          onPointerDown={(event) => {
            grow.current = {
              ox: event.clientX,
              oy: event.clientY,
              w: geometry.w,
              h: geometry.h,
              x: geometry.x,
              y: geometry.y,
              dir,
            };
            setHeld(true);
            event.currentTarget.setPointerCapture(event.pointerId);
            event.stopPropagation();
          }}
          onPointerMove={(event) => pull(event, dir)}
          onPointerUp={() => {
            grow.current = null;
            setHeld(false);
          }}
        />
      ))}

      <header
        className="surface-bar"
        onPointerDown={(event) => {
          if ((event.target as HTMLElement).closest("button")) return;
          // A move is a DELTA, so only the zoom applies — the stage origin and
          // the pan cancel out between two screen positions. Reconstructing an
          // absolute position here is what made panels slide away from where
          // they were at any zoom other than 1:1.
          drag.current = { x: event.clientX, y: event.clientY, gx: geometry.x, gy: geometry.y };
          setHeld(true);
          (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          const held0 = drag.current;
          const step = screenDeltaToWorld(stage, event.clientX - held0.x, event.clientY - held0.y);
          moveSurface(surface.id, held0.gx + step.x, held0.gy + step.y);
        }}
        onPointerUp={() => {
          drag.current = null;
          setHeld(false);
        }}
      >
        <span className={`kind kind-${surface.kind}`} aria-hidden="true" />
        <h2 title={surface.title}>{surface.title}</h2>
        {held ? (
          <span className="geom mono">
            {geometry.x},{geometry.y} · {geometry.w}×{geometry.h}
          </span>
        ) : null}
        <span className={`source-tag tag-${surface.source.kind}`}>{surface.source.kind}</span>
        <div className="surface-tools">
          <button
            type="button"
            className="win-btn"
            title="minimize — stays open, goes to the dock"
            aria-label={`minimize ${surface.title}`}
            onClick={() => minimizeSurface(surface.id)}
          >
            –
          </button>
          <button
            type="button"
            className="win-btn win-close"
            title="close"
            aria-label={`close ${surface.title}`}
            onClick={() => closeSurface(surface.id)}
          >
            ×
          </button>
        </div>
      </header>

      <div className="surface-body">{render({ surfaceId: surface.id })}</div>
    </section>
  );
}
