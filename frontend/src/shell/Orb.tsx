// The HERMUS core.
//
// Position comes from placeOrb() — next to the surface that matters, out of the
// way when the room is full. Motion comes from orbStateFor() — the newest real
// runtime signal, never a timer pretending to be activity. Drag it anywhere;
// double-click to let it find its own spot again.

import { useEffect, useMemo, useRef, useState } from "react";
import { useWorkspace, visibleSurfaces } from "../state/workspace-store";
import { advance, breath, initialPresence, makeRandom, type Presence, type PresenceState } from "../state/presence";
import { ORB_SIZE, orbStateFor, placeOrb, STATE_LABEL, type OrbState } from "../state/orb";
import { grabOffset, screenToWorld, type ScreenFrame } from "../state/coords";

const HUES: Record<OrbState, [string, string]> = {
  idle: ["#4fd1c5", "#3b82f6"],
  working: ["#60a5fa", "#a78bfa"],
  verifying: ["#f6c177", "#4fd1c5"],
  attention: ["#fbbf24", "#fb7185"],
  blocked: ["#64748b", "#475569"],
};

/**
 * How fast a state's ring turns, and how much of it is lit.
 *
 * Working turns briskly and sweeps most of the circle; verifying turns the
 * other way so the two are distinguishable without reading the label; blocked
 * does not turn at all, because a thing that is stuck should not look busy.
 */
const MOTION: Record<OrbState, { speed: number; sweep: number; swell: number }> = {
  idle: { speed: 0.0004, sweep: 0.35, swell: 0.02 },
  working: { speed: 0.0022, sweep: 0.9, swell: 0.05 },
  verifying: { speed: -0.0016, sweep: 0.9, swell: 0.035 },
  attention: { speed: 0.0008, sweep: 0.35, swell: 0.03 },
  blocked: { speed: 0, sweep: 0, swell: 0 },
};

/**
 * Draw the Orb from a presence, not a state.
 *
 * The state decides colour and speed; the presence decides everything about
 * whether it looks inhabited — the breath, the drift, the gaze, the blink.
 * Splitting them this way is the whole trick: the room already had a state,
 * and a state alone is what made this read as an indicator.
 */
function paint(ctx: CanvasRenderingContext2D, size: number, t: number, p: Presence) {
  const r = size / 2;
  const state = PRESENCE_TO_ORB[p.state];
  const [a, b] = HUES[state];
  const motion = MOTION[state];
  const act = p.activation;

  ctx.clearRect(0, 0, size, size);

  // --- body: the breath, and the gentle drift of the current idle move ------
  const swell = 1 + breath(p.time, act) * 3 + Math.sin(t / 300 + motion.speed) * motion.swell;
  const drift = idleOffset(p, r);
  const cx = r + drift.x;
  const cy = r + drift.y;

  const gradient = ctx.createRadialGradient(cx, cy, r * 0.1, cx, cy, r * 0.92);
  gradient.addColorStop(0, `${b}${Math.round(190 * act + 40).toString(16).padStart(2, "0")}`);
  gradient.addColorStop(0.55, `${a}33`);
  gradient.addColorStop(1, "rgba(6,10,18,0)");
  ctx.fillStyle = gradient;
  ctx.beginPath();
  ctx.arc(cx, cy, r * 0.92 * swell, 0, Math.PI * 2);
  ctx.fill();

  // --- the eye -------------------------------------------------------------
  // A single focus point that moves. It is the cheapest possible thing that
  // makes an orb look like it is looking somewhere, and unlike a rotating ring
  // it does not announce itself as a mechanism.
  const gaze = gazeOffset(p, r * 0.2);
  const open = p.blinking ? 0.12 : 1;
  ctx.fillStyle = b;
  ctx.globalAlpha = 0.55 + act * 0.45;
  ctx.beginPath();
  ctx.ellipse(cx + gaze.x, cy + gaze.y, r * 0.13, r * 0.13 * open, 0, 0, Math.PI * 2);
  ctx.fill();

  // --- rings ---------------------------------------------------------------
  ctx.globalAlpha = 0.85 * act;
  ctx.strokeStyle = a;
  ctx.lineWidth = 1.25;
  ctx.beginPath();
  ctx.arc(cx, cy, r * 0.52 * swell, 0, Math.PI * 2);
  ctx.stroke();

  if (motion.sweep > 0) {
    ctx.lineWidth = 2.25;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.66, t * motion.speed, t * motion.speed + Math.PI * motion.sweep);
    ctx.stroke();
  }

  ctx.globalAlpha = 0.5 * act;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(cx, cy, r * 0.8, t * -motion.speed * 0.6, t * -motion.speed * 0.6 + Math.PI * 0.5);
  ctx.stroke();

  // --- transition energy ---------------------------------------------------
  // A state change tumbles the shell briefly, so a change is never ambiguous
  // even when the two states differ only in colour.
  if (p.transitionEnergy > 0.01) {
    ctx.globalAlpha = p.transitionEnergy * 0.5;
    ctx.strokeStyle = b;
    ctx.lineWidth = 1;
    for (let i = 0; i < 3; i += 1) {
      const spin = p.time / 260 + (i * Math.PI * 2) / 3;
      ctx.beginPath();
      ctx.arc(cx, cy, r * (0.94 - i * 0.05), spin, spin + 0.5 + p.transitionEnergy * 0.6);
      ctx.stroke();
    }
  }

  ctx.globalAlpha = 1;
}

/** Where the idle move wants the focus to sit, eased in and out. */
function idleOffset(p: Presence, r: number): { x: number; y: number } {
  if (!p.move) return { x: 0, y: 0 };
  const span = p.moveEndsAt > p.moveStartedAt ? p.moveEndsAt - p.moveStartedAt : 1;
  const t = Math.min(1, Math.max(0, (p.time - p.moveStartedAt) / span));
  // Ease both ends so a move starts and stops rather than snapping.
  const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
  switch (p.move.kind) {
    // Frequent, small, horizontal — the commonest thing someone waiting does.
    case "glance":
      return { x: Math.sin(e * Math.PI * 2) * r * 0.05, y: 0 };
    case "hover":
      return { x: 0, y: -e * r * 0.07 };
    case "tilt":
      return { x: Math.sin(e * Math.PI) * r * 0.04, y: Math.cos(e * Math.PI) * r * 0.03 };
    // A long, focused stare. Barely moves — the stillness is the point.
    case "gaze":
      return { x: 0, y: 0 };
    case "stretch":
      return { x: 0, y: -e * r * 0.12 };
    case "wink":
      return { x: Math.sin(e * Math.PI) * r * 0.03, y: 0 };
    case "yawn":
      return { x: 0, y: e * r * 0.05 };
    default:
      return { x: 0, y: 0 };
  }
}

/** Where the eye looks, which follows the body but overshoots slightly. */
function gazeOffset(p: Presence, r: number): { x: number; y: number } {
  const body = idleOffset(p, r * 1.6);
  return { x: body.x * 1.25, y: body.y * 1.25 };
}

/** Presence states that are not one of the room's five, mapped to the nearest. */
const PRESENCE_TO_ORB: Record<PresenceState, OrbState> = {
  idle: "idle",
  asleep: "idle",
  listening: "working",
  thinking: "working",
  speaking: "working",
  // Compacting is a wait, and a wait that looks busy reads as a hang. It is
  // dim and slow on purpose: the explanation is carried by the visual because
  // a spoken "give me a second" every time context is compacted is maddening.
  compacting: "verifying",
  working: "working",
  verifying: "verifying",
  attention: "attention",
  blocked: "blocked",
};

export function Orb() {
  const surfaces = useWorkspace((state) => state.surfaces);
  const order = useWorkspace((state) => state.order);
  const viewport = useWorkspace((state) => state.viewport);
  const tray = useWorkspace((state) => state.tray);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // `moved` is how far the press travelled, which is what separates a click on
  // the core (open the fan) from a drag (reposition it).
  const drag = useRef<{ screen: ScreenFrame; dx: number; dy: number; startX: number; startY: number; moved: number } | null>(null);
  const [manual, setManual] = useState<{ x: number; y: number } | null>(null);
  const setFanOpen = useWorkspace((store) => store.setFanOpen);
  const fanOpen = useWorkspace((store) => store.fanOpen);
  // The stage is zoomable. Pointer deltas are screen pixels and the orb is placed
  // in world coordinates, so the grab offset has to be measured in the same
  // units or the orb drifts out from under the cursor — slowly when zoomed out,
  // quickly when zoomed in, which is exactly what it was doing.
  const stage = useWorkspace((store) => store.stage);

  const visible = useMemo(() => visibleSurfaces({ surfaces, order }), [surfaces, order]);
  const auto = useMemo(() => placeOrb(visible, viewport), [visible, viewport]);
  const position = manual ?? auto;
  // A dragged orb keeps the size the room asked for only while it is where the
  // room put it; once you have placed it yourself it stays full size.
  const size = manual ? ORB_SIZE : auto.size;
  const state = useMemo(() => orbStateFor(tray), [tray]);
  const setOrbPlacement = useWorkspace((store) => store.setOrbPlacement);

  // Publish the real placement so the pod can dock against it. Without this the
  // pod has to recompute placeOrb() itself and the two answers drift the moment
  // either one changes.
  useEffect(() => {
    setOrbPlacement({ x: position.x, y: position.y, size });
  }, [position.x, position.y, size, setOrbPlacement]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = size * ratio;
    canvas.height = size * ratio;
    ctx.scale(ratio, ratio);
    // The presence is a VALUE advanced per frame, not a pile of mutable
    // globals — which is what makes the aliveness replayable from a seed and
    // therefore testable at all.
    const seed = Math.floor(Math.random() * 1e9);
    const rand = makeRandom(seed);
    let p = initialPresence(seed);
    let last = performance.now();
    let frame = 0;
    const draw = (time: number) => {
      const dt = Math.min(64, time - last);
      last = time;
      p = advance(p, dt, state, rand);
      paint(ctx, size, time, p);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [state, size]);

  const title = `${STATE_LABEL[state]} · ${manual ? "placed by you" : auto.anchor === "crowded" ? "pushed aside — the room is full" : auto.anchor === "focused" ? "beside the focused surface" : "no work in view"}`;

  return (
    <div
      className={`orb orb-${state} ${manual ? "orb-manual" : "orb-auto"} ${size < ORB_SIZE ? "orb-small" : ""} ${
        // The core shrinks to a companion beside whatever is open rather than
        // disappearing. Hiding it outright was the wrong call: placeOrb() already
        // moves it out of the way of live work, and a status light that vanishes
        // the moment you open something is worse than useless — you cannot tell
        // "idle" from "gone".
        visible.length ? "orb-dim" : ""
      }`}
      style={{ left: position.x, top: position.y, width: size, height: size }}
      title={title}
      role="button"
      tabIndex={0}
      aria-expanded={fanOpen}
      aria-label={`HERMUS core — ${STATE_LABEL[state]}. Open the launch fan.`}
      onPointerDown={(event) => {
        // Screen -> world, done once and held for the life of the drag.
        //
        // Three things have to be undone, and missing any one of them is what
        // makes a dragged object "hold further away each time you grab it":
        //   - the stage's own offset on the page (the topbar sits above it, so
        //     clientX is not stage-relative)
        //   - the pan
        //   - the zoom
        //
        // Dividing by the zoom alone is not enough, and is actively wrong: it
        // leaves the stage origin in the number, so the orb jumps the moment
        // you press it and the offset compounds on every re-grab.
        const stageEl = (event.currentTarget as HTMLElement).offsetParent as HTMLElement | null;
        const rect = stageEl?.getBoundingClientRect();
        const screen: ScreenFrame = { originX: rect?.left ?? 0, originY: rect?.top ?? 0, ...stage };
        // Where the press landed, in SCREEN pixels. Travel is measured from
        // here and not from the orb's own position: the orb moves as you drag,
        // so `current - position` collapses to one frame's increment every
        // event and a real drag reads as a click.
        drag.current = {
          screen,
          ...grabOffset(screen, event.clientX, event.clientY, position.x, position.y),
          startX: event.clientX,
          startY: event.clientY,
          moved: 0,
        };
        (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const held = drag.current;
        if (!held) return;
        held.moved = Math.max(held.moved, Math.hypot(event.clientX - held.startX, event.clientY - held.startY));
        const world = screenToWorld(held.screen, event.clientX, event.clientY);
        setManual({ x: world.x - held.dx, y: world.y - held.dy });
      }}
      onPointerUp={() => {
        const held = drag.current;
        drag.current = null;
        // A press that never travelled is a click, and a click on the core opens
        // the launch fan. The core IS the launcher now — there is no second dot
        // in the room, so this is the only way in.
        if (!held || held.moved <= 4) setFanOpen((current) => !current);
      }}
      onDoubleClick={() => setManual(null)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        setFanOpen((current) => !current);
      }}
    >
      <canvas ref={canvasRef} style={{ width: size, height: size }} />
      <span className="orb-state">{STATE_LABEL[state]}</span>
    </div>
  );
}
