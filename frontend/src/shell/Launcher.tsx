// The launch pod: the room's only always-visible way to create a surface.
//
// Drag it anywhere and the fan it opens bends to whichever quadrant has room, so
// it stays usable against any edge. Positions are stage-local — the chrome band
// along the bottom is not somewhere to put a clickable thing.

import { useRef, useState } from "react";
import { useWorkspace } from "../state/workspace-store";
import { KIND_TITLES } from "../state/surfaces";
import { fanRadius, fanSlots, KIND_GLYPH, LAUNCHER_KINDS, POD_SIZE, SLOT_SIZE, podHome } from "../state/launcher";

/** Past this many pixels the pointer intends a drag, not a click. */
const DRAG_THRESHOLD = 5;

export function Launcher() {
  // The store's viewport is the room itself: the stage with the dock rail taken
  // off the bottom, measured by the shell.
  const room = useWorkspace((state) => state.viewport);
  const orbPlacement = useWorkspace((state) => state.orbPlacement);
  const openSurface = useWorkspace((state) => state.openSurface);
  const [placed, setPlaced] = useState<{ x: number; y: number } | null>(null);
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  // Dragging is state, not a ref: the CSS transition has to be switched off the
  // instant the drag starts, and a ref change does not re-render, so the pod
  // would lag a frame behind the pointer on pickup.
  const [dragging, setDragging] = useState(false);
  const pointer = useRef<{ ox: number; oy: number; from: { x: number; y: number }; moved: number } | null>(null);

  // The pod has one home and it belongs to the orb: docked against a small core,
  // standing off at mid-left when the core has the room to itself. A pod the
  // user dragged keeps its own position until they double-click to re-dock.
  const home = podHome(orbPlacement, room);
  const anchor = placed ?? home;
  // Seven slots of trigonometry, cheaper than the memo that would track it.
  const slots = fanSlots(anchor, room, LAUNCHER_KINDS);
  const radius = fanRadius(LAUNCHER_KINDS.length, SLOT_SIZE);

  return (
    <>
      {open ? (
        <div className="fan" aria-label="surfaces you can open">
          {/* A hairline of the arc the entries sit on — the fan reads as an
              instrument, not as a menu that happens to be crooked. */}
          <span
            className="fan-arc"
            style={{
              left: anchor.x + POD_SIZE / 2 - radius,
              top: anchor.y + POD_SIZE / 2 - radius,
              width: radius * 2,
              height: radius * 2,
            }}
            aria-hidden="true"
          />
          {slots.map((slot, index) => (
            <button
              key={slot.kind}
              type="button"
              className="fan-slot"
              // A label hanging off the right edge of the window is worse than no
              // label, so slots in the left half anchor their text inward.
              data-side={slot.x < anchor.x ? "left" : "right"}
              style={{ left: slot.x, top: slot.y, width: SLOT_SIZE, height: SLOT_SIZE, transitionDelay: `${index * 22}ms` }}
              onPointerEnter={() => setHovered(slot.kind)}
              onPointerLeave={() => setHovered((current) => (current === slot.kind ? null : current))}
              onFocus={() => setHovered(slot.kind)}
              onBlur={() => setHovered((current) => (current === slot.kind ? null : current))}
              onClick={() => {
                openSurface({ kind: slot.kind, source: { kind: "user" } });
                setOpen(false);
              }}
            >
              <span aria-hidden="true">{KIND_GLYPH[slot.kind]}</span>
              {/* The name lives in the room, not in a native tooltip. A `title`
                  renders as a browser box that follows the cursor, is not part of
                  the workspace, and disappears the moment you look away — which
                  is exactly the "name box below the cursor" complaint. This is a
                  real element inside the dashboard, so it survives a screenshot,
                  a second monitor, and a screen reader. */}
              <span className="fan-slot-label" data-active={hovered === slot.kind}>
                {KIND_TITLES[slot.kind]}
              </span>
              <span className="sr-only">open {slot.kind} surface</span>
            </button>
          ))}
        </div>
      ) : null}

      <div
        className={`pod ${open ? "pod-open" : ""} ${dragging ? "pod-dragging" : ""}`}
        style={{ left: anchor.x, top: anchor.y, width: POD_SIZE, height: POD_SIZE }}
        onPointerDown={(event) => {
          pointer.current = { ox: event.clientX - anchor.x, oy: event.clientY - anchor.y, from: anchor, moved: 0 };
          setDragging(true);
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const held = pointer.current;
          if (!held) return;
          const next = { x: event.clientX - held.ox, y: event.clientY - held.oy };
          // Distance from where the press landed, not accumulated jitter: a pod
          // nudged back to its start is still a click.
          held.moved = Math.max(held.moved, Math.hypot(next.x - held.from.x, next.y - held.from.y));
          if (held.moved > DRAG_THRESHOLD) setPlaced(next);
        }}
        onPointerUp={() => {
          const wasDrag = (pointer.current?.moved ?? 0) > DRAG_THRESHOLD;
          pointer.current = null;
          setDragging(false);
          if (!wasDrag) setOpen((current) => !current);
        }}
        onDoubleClick={() => setPlaced(null)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          setOpen((current) => !current);
        }}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label="launch surfaces — drag to move, double-click to re-dock to the core"
      >
        <span className="pod-core" aria-hidden="true" />
        {/* Two unlabaged circles in one room is a guessing game: the orb reports
            state, the pod opens things, and nothing on screen said which was
            which. The pod names itself; the orb already names its state. */}
        <span className="pod-label" aria-hidden="true">
          surfaces
        </span>
      </div>
    </>
  );
}
