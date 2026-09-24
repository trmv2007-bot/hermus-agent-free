// The launch pod: the room's only always-visible way to create a surface.
//
// Drag it anywhere and the fan it opens bends to whichever quadrant has room, so
// it stays usable against any edge. Positions are stage-local — the chrome band
// along the bottom is not somewhere to put a clickable thing.

import { useRef, useState } from "react";
import { useWorkspace } from "../state/workspace-store";
import { KIND_TITLES } from "../state/surfaces";
import { EDGE, fanRadius, fanSlots, KIND_GLYPH, LAUNCHER_KINDS, POD_SIZE, SLOT_SIZE } from "../state/launcher";

/** Past this many pixels the pointer intends a drag, not a click. */
const DRAG_THRESHOLD = 5;

export function Launcher() {
  // The store's viewport is the room itself: the stage with the dock rail taken
  // off the bottom, measured by the shell.
  const room = useWorkspace((state) => state.viewport);
  const openSurface = useWorkspace((state) => state.openSurface);
  const [placed, setPlaced] = useState<{ x: number; y: number } | null>(null);
  const [open, setOpen] = useState(false);
  const pointer = useRef<{ ox: number; oy: number; from: { x: number; y: number }; moved: number } | null>(null);

  const parked = { x: EDGE, y: Math.max(EDGE, room.h - POD_SIZE - EDGE) };
  const anchor = placed ?? parked;
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
              style={{ left: slot.x, top: slot.y, width: SLOT_SIZE, height: SLOT_SIZE, transitionDelay: `${index * 22}ms` }}
              title={`open ${KIND_TITLES[slot.kind]}`}
              aria-label={`open ${slot.kind} surface`}
              onClick={() => {
                openSurface({ kind: slot.kind, source: { kind: "user" } });
                setOpen(false);
              }}
            >
              <span aria-hidden="true">{KIND_GLYPH[slot.kind]}</span>
            </button>
          ))}
        </div>
      ) : null}

      <div
        className={`pod ${open ? "pod-open" : ""}`}
        style={{ left: anchor.x, top: anchor.y, width: POD_SIZE, height: POD_SIZE }}
        onPointerDown={(event) => {
          pointer.current = { ox: event.clientX - anchor.x, oy: event.clientY - anchor.y, from: anchor, moved: 0 };
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
        title={`${open ? "close" : "open"} the launch fan · drag to move · double-click to park`}
      >
        <span className="pod-core" aria-hidden="true" />
        <span className="sr-only">launch surfaces</span>
      </div>
    </>
  );
}
