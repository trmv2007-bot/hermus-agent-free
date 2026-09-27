// The launch fan.
//
// There is no separate pod any more. The core and the launcher were two circles
// in one room that both answered "what can I open", and the operator had to work
// out which was which. They are one object now: the orb IS the launcher, the fan
// unfolds from it, and when the room is full the core shrinks beside a panel and
// carries the launcher with it rather than leaving a second dot behind.
//
// The fan still needs an anchor, and it has exactly one: the core's own
// placement, published through the store as `orbPlacement`. The orb paints itself
// from placeOrb(); recomputing that here would let the two answers drift.

import { useEffect, useState } from "react";
import { useWorkspace } from "../state/workspace-store";
import { KIND_TITLES } from "../state/surfaces";
import { ARC_SPAN_DEG, EDGE, fanRadius, fanSlots, HERO_SPAN_DEG, KIND_GLYPH, LAUNCHER_KINDS, POD_SIZE, RING_CLEARANCE, SLOT_SIZE } from "../state/launcher";

export function Launcher() {
  // The store's viewport is the room itself: the stage with the dock rail taken
  // off the bottom, measured by the shell.
  const room = useWorkspace((state) => state.viewport);
  const placement = useWorkspace((state) => state.orbPlacement);
  const fanOpen = useWorkspace((state) => state.fanOpen);
  const setFanOpen = useWorkspace((state) => state.setFanOpen);
  const openSurface = useWorkspace((state) => state.openSurface);
  const [hovered, setHovered] = useState<string | null>(null);
  /**
   * The fan is mounted for a beat AFTER it closes, so the entries can fly back
   * into the core instead of blinking out of existence.
   *
   * `entering` covers the stagger-in; `leaving` covers the stagger-out, reversed
   * so the last entry to arrive is the first to leave. Unmounting immediately on
   * close is what made the fan feel like it was switched off rather than closed.
   */
  const [entering, setEntering] = useState(false);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    if (fanOpen) {
      setLeaving(false);
      setEntering(true);
      const clear = window.setTimeout(() => setEntering(false), 420);
      return () => window.clearTimeout(clear);
    }
    if (!leaving) return;
    const clear = window.setTimeout(() => setLeaving(false), 380);
    return () => window.clearTimeout(clear);
  }, [fanOpen, leaving]);

  // Escape closes it from the keyboard, since the scrim is a pointer target.
  useEffect(() => {
    if (!fanOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFanOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fanOpen, setFanOpen]);

  // A close has to start the exit, so the transition into `leaving` is triggered
  // by the state change rather than only read from it.
  const [wasOpen, setWasOpen] = useState(fanOpen);
  useEffect(() => {
    if (wasOpen && !fanOpen) setLeaving(true);
    setWasOpen(fanOpen);
  }, [fanOpen, wasOpen]);

  if (!fanOpen && !leaving) return null;

  // Anchored to the core's CENTRE, expressed as a box origin so the existing arc
  // maths (which expects a top-left pod position) keeps working unchanged.
  const centre = placement
    ? { x: placement.x + placement.size / 2 - POD_SIZE / 2, y: placement.y + placement.size / 2 - POD_SIZE / 2 }
    : { x: EDGE, y: Math.max(EDGE, room.h - POD_SIZE - EDGE) };

  // Seven slots of trigonometry, cheaper than the memo that would track it.
  // A centred, room-sized core has nothing to point away from, so the fan rings
  // it and spreads evenly on all sides. A small core beside a panel still opens
  // to one side, because there it genuinely is shoved into a corner.
  const hero = (placement?.size ?? 0) > 200;
  const span = hero ? HERO_SPAN_DEG : ARC_SPAN_DEG;
  // The ring is measured from the core's centre, so it has to clear the core's
  // RADIUS — a 300px core is 150px out from centre, and a 92px ring drew every
  // entry inside it. Also clamped to the room: a ring wider than the stage
  // pushes entries to the corners, which is worse than a tight ring.
  const coreRadius = hero ? (placement?.size ?? 0) / 2 : POD_SIZE / 2;
  const roomRadius = Math.min(room.w, room.h) / 2 - SLOT_SIZE - EDGE;
  const innerRadius = Math.max(0, Math.min(coreRadius + RING_CLEARANCE, roomRadius));
  const slots = fanSlots(centre, room, LAUNCHER_KINDS, POD_SIZE, SLOT_SIZE, span);
  const radius = fanRadius(LAUNCHER_KINDS.length, SLOT_SIZE, span, innerRadius);

  return (
    <div className="fan" aria-label="surfaces you can open">
      {/* Clicking the dimmed field closes the fan. Without it the only way out is
          the core itself, a small target for a large gesture. */}
      <button type="button" className="fan-scrim" aria-label="close the launch fan" onClick={() => setFanOpen(false)} />

      {/* A hairline of the arc the entries sit on — the fan reads as an
          instrument, not as a menu that happens to be crooked. */}
      <span
        className="fan-arc"
        style={{
          left: centre.x + POD_SIZE / 2 - radius,
          top: centre.y + POD_SIZE / 2 - radius,
          width: radius * 2,
          height: radius * 2,
        }}
        data-hero={hero ? "true" : "false"}
        aria-hidden="true"
      />
      {slots.map((slot, index) => (
        <button
          key={slot.kind}
          type="button"
          className="fan-slot"
          // A label hanging off the right edge of the window is worse than no
          // label, so slots in the left half anchor their text inward.
          data-side={slot.x < centre.x ? "left" : "right"}
          // Marks the first paint after the fan opens, so the fly-out runs
          // once on entry and not on every later reposition.
          data-enter={entering ? "true" : "false"}
          data-leave={leaving && !fanOpen ? "true" : "false"}
          style={
            {
              left: slot.x,
              top: slot.y,
              width: SLOT_SIZE,
              height: SLOT_SIZE,
              // Launch vector: from where this entry sits back to the core's
              // centre, so every one flies out along its own route.
              "--fan-x": `${centre.x + POD_SIZE / 2 - (slot.x + SLOT_SIZE / 2)}px`,
              "--fan-y": `${centre.y + POD_SIZE / 2 - (slot.y + SLOT_SIZE / 2)}px`,
              // Reversed on the way out, so the arc collapses the same way it
              // opened instead of vanishing in submission order.
              animationDelay: entering
                ? `${index * 26}ms`
                : leaving && !fanOpen
                  ? `${(LAUNCHER_KINDS.length - 1 - index) * 22}ms`
                  : undefined,
              transitionDelay: `${index * 22}ms`,
            } as React.CSSProperties
          }
          onPointerEnter={() => setHovered(slot.kind)}
          onPointerLeave={() => setHovered((current) => (current === slot.kind ? null : current))}
          onFocus={() => setHovered(slot.kind)}
          onBlur={() => setHovered((current) => (current === slot.kind ? null : current))}
          onClick={() => {
            openSurface({ kind: slot.kind, source: { kind: "user" } });
            setFanOpen(false);
          }}
        >
          <span aria-hidden="true">{KIND_GLYPH[slot.kind]}</span>
          {/* The name lives in the room, not in a native tooltip. A `title`
              renders as a browser box that follows the cursor, is not part of the
              workspace, and disappears the moment you look away — which is exactly
              the "name box below the cursor" complaint. This is a real element
              inside the dashboard, so it survives a screenshot and a screen reader. */}
          <span className="fan-slot-label" data-active={hovered === slot.kind}>
            {KIND_TITLES[slot.kind]}
          </span>
          <span className="sr-only">open {slot.kind} surface</span>
        </button>
      ))}
    </div>
  );
}
