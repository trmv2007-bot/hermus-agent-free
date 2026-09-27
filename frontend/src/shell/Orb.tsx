// The HERMUS core.
//
// Position comes from placeOrb() — next to the surface that matters, out of the
// way when the room is full. Motion comes from orbStateFor() — the newest real
// runtime signal, never a timer pretending to be activity. Drag it anywhere;
// double-click to let it find its own spot again.

import { useEffect, useMemo, useRef, useState } from "react";
import { useWorkspace, visibleSurfaces } from "../state/workspace-store";
import { ORB_SIZE, orbStateFor, placeOrb, STATE_LABEL, type OrbState } from "../state/orb";

const HUES: Record<OrbState, [string, string]> = {
  idle: ["#4fd1c5", "#3b82f6"],
  working: ["#60a5fa", "#a78bfa"],
  verifying: ["#f6c177", "#4fd1c5"],
  attention: ["#fbbf24", "#fb7185"],
  blocked: ["#64748b", "#475569"],
};

function paint(ctx: CanvasRenderingContext2D, size: number, t: number, state: OrbState) {
  const r = size / 2;
  ctx.clearRect(0, 0, size, size);
  const [a, b] = HUES[state];
  const gradient = ctx.createRadialGradient(r, r, r * 0.1, r, r, r * 0.92);
  gradient.addColorStop(0, `${b}cc`);
  gradient.addColorStop(0.55, `${a}33`);
  gradient.addColorStop(1, "rgba(6,10,18,0)");
  ctx.fillStyle = gradient;
  ctx.beginPath();
  ctx.arc(r, r, r * 0.92, 0, Math.PI * 2);
  ctx.fill();

  const breath = state === "idle" ? 1 + Math.sin(t / 900) * 0.02 : state === "blocked" ? 1 : 1 + Math.sin(t / 260) * 0.05;
  ctx.strokeStyle = a;
  ctx.lineWidth = 1.25;
  ctx.globalAlpha = 0.85;
  ctx.beginPath();
  ctx.arc(r, r, r * 0.52 * breath, 0, Math.PI * 2);
  ctx.stroke();

  // A sweep only turns when something is actually running; verifying turns
  // against it, so the two states are distinguishable without a label.
  const speed = state === "working" ? 0.0022 : state === "verifying" ? -0.0016 : state === "attention" ? 0.0008 : 0.0004;
  const sweep = state === "blocked" ? 0 : Math.PI * (state === "working" || state === "verifying" ? 0.9 : 0.35);
  ctx.lineWidth = 2.25;
  ctx.beginPath();
  ctx.arc(r, r, r * 0.66, t * speed, t * speed + sweep);
  ctx.stroke();

  ctx.globalAlpha = 0.5;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(r, r, r * 0.8, t * -speed * 0.6, t * -speed * 0.6 + Math.PI * 0.5);
  ctx.stroke();
  ctx.globalAlpha = 1;
}

export function Orb() {
  const surfaces = useWorkspace((state) => state.surfaces);
  const order = useWorkspace((state) => state.order);
  const viewport = useWorkspace((state) => state.viewport);
  const tray = useWorkspace((state) => state.tray);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // `moved` is how far the press travelled, which is what separates a click on
  // the core (open the fan) from a drag (reposition it).
  const drag = useRef<{ dx: number; dy: number; moved: number } | null>(null);
  const [manual, setManual] = useState<{ x: number; y: number } | null>(null);
  const setFanOpen = useWorkspace((store) => store.setFanOpen);
  const fanOpen = useWorkspace((store) => store.fanOpen);

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
    let frame = 0;
    const draw = (time: number) => {
      paint(ctx, size, time, state);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [state, size]);

  const title = `${STATE_LABEL[state]} · ${manual ? "placed by you" : auto.anchor === "crowded" ? "pushed aside — the room is full" : auto.anchor === "focused" ? "beside the focused surface" : "no work in view"}`;

  return (
    <div
      className={`orb orb-${state} ${manual ? "orb-manual" : "orb-auto"} ${size < ORB_SIZE ? "orb-small" : ""} ${
        // The core is the launcher, so it hides while a panel is open — the
        // panel's own controls take over and a floating circle over live work is
        // just an obstruction. It comes back the moment the room empties.
        visible.length ? "orb-tucked" : ""
      }`}
      style={{ left: position.x, top: position.y, width: size, height: size }}
      title={title}
      role="button"
      tabIndex={0}
      aria-expanded={fanOpen}
      aria-label={`HERMUS core — ${STATE_LABEL[state]}. Open the launch fan.`}
      onPointerDown={(event) => {
        drag.current = { dx: event.clientX - position.x, dy: event.clientY - position.y, moved: 0 };
        (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const held = drag.current;
        if (!held) return;
        const x = event.clientX - held.dx;
        const y = event.clientY - held.dy;
        held.moved = Math.max(held.moved, Math.hypot(x - position.x, y - position.y));
        setManual({ x, y });
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
