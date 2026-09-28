// The HERMUS core.
//
// Three rules decide what is on screen here, and each is a reaction to something
// specific that happened:
//
//   Position answers "where is the work". placeOrb() puts the core beside the
//   surface that matters and out of the way when the room is full.
//
//   Colour and text answer "what state is this". Those carry the message on
//   their own, because they are the only channels that survive when motion is
//   correctly switched off.
//
//   Motion answers "what just happened". Every moving thing is paid for by a
//   real event: a tray entry arrived, a turn was spoken, the state changed, or
//   the microphone measured something. Nothing here free-runs on a timer, which
//   is the difference between a readout and a decoration that asks for
//   attention on a schedule nobody chose.
//
// The mic level is a real RMS reading when something has registered a level
// source (setLevelSource in state/orb.ts) and honestly absent when nothing has.
// The absent case is the interesting one: the orb then says it is listening and
// draws no waveform at all, rather than inventing one. A pulsing ring over a
// microphone nobody is measuring is a lie with a high frame rate.

import { useEffect, useMemo, useRef, useState } from "react";
import { coverage, recessionFor, type Rect } from "../state/depth";
import { nowMs, tickAttention } from "../state/attention";
import { useWorkspace, visibleSurfaces } from "../state/workspace-store";
import {
  advance,
  advanceAudio,
  idleOffset,
  initialAudio,
  initialPresence,
  makeRandom,
  motionFor,
  type AudioReading,
  type Presence,
  type PresenceState,
} from "../state/presence";
import {
  eventCountFor,
  hasLevelSource,
  ORB_SIZE,
  orbStateFor,
  placeOrb,
  readLevel,
  STATE_GLOW,
  STATE_LABEL,
} from "../state/orb";
import { getVoiceState, subscribeVoice, voiceOverridesOrb, type VoiceState } from "../voice/store";
import { getLoopState } from "../voice/loop";
import { grabOffset, screenToWorld, type ScreenFrame } from "../state/coords";
import "../styles/orb-motion.css";

/**
 * The palette, keyed by presence state.
 *
 * Two states are deliberately dim rather than bright. "blocked" is nearly out,
 * because a thing that is stuck should not be the brightest object in the room
 * and should certainly not be moving. "compacting" is dim for a different
 * reason: it is a wait, and a wait that glows looks like work happening, which
 * is the specific thing it is not.
 */
const HUES: Record<PresenceState, [string, string]> = {
  idle: ["#4fd1c5", "#3b82f6"],
  working: ["#60a5fa", "#a78bfa"],
  verifying: ["#f6c177", "#4fd1c5"],
  attention: ["#fbbf24", "#fb7185"],
  blocked: ["#64748b", "#475569"],
  asleep: ["#475569", "#334155"],
  // Listening is steady and open. It is waiting on you, not working.
  listening: ["#4fd1c5", "#22d3ee"],
  // Thinking turns the other way from working, so the two are separable at a
  // glance without reading the label.
  thinking: ["#a78bfa", "#818cf8"],
  speaking: ["#f6c177", "#4fd1c5"],
  compacting: ["#64748b", "#94a3b8"],
};

/** A hex colour plus an alpha, as one canvas fill string. */
function tint(rgb: string, alpha: number): string {
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    .toString(16)
    .padStart(2, "0");
  return `${rgb}${a}`;
}

/** How wide one event tick is on the ring. */
const TICK_ARC = Math.PI * 0.42;

interface PaintOptions {
  /** The system asked for less motion, so only discrete state is drawn. */
  reduceMotion: boolean;
}

/**
 * Turn "the person is typing over there" into a direction the eye can travel in.
 *
 * Expressed as a fraction of the Orb's own radius, because that is the unit
 * the painter already works in, and clamped to a small fraction of it. A core
 * that lunges across the room to look at something is a camera pan, not a
 * glance, and it would fight the body rather than lead it.
 */
function attentionGaze(
  att: { target: { x: number; y: number } | null; hold: number },
  position: { x: number; y: number },
  orbSize: number,
): { x: number; y: number } {
  if (!att.target || att.hold <= 0.001) return { x: 0, y: 0 };
  const vw = window.innerWidth || 1;
  const vh = window.innerHeight || 1;
  const cx = position.x + orbSize / 2;
  const cy = position.y + orbSize / 2;
  const dx = att.target.x * vw - cx;
  const dy = att.target.y * vh - cy;
  const dist = Math.hypot(dx, dy) || 1;
  // At most a third of the radius, so the look is legible but restrained.
  const reach = 0.34 * att.hold;
  return { x: (dx / dist) * reach, y: (dy / dist) * reach };
}

/**
 * Draw the Orb from the presence, not from a state.
 *
 * Every animated quantity arrives pre-licensed in `m`, and a channel whose
 * backing condition is not met is already zero by the time it gets here. The
 * painter draws what it is given; it does not decide what should move.
 */
function paint(
  ctx: CanvasRenderingContext2D,
  size: number,
  p: Presence,
  m: ReturnType<typeof motionFor>,
  opts: PaintOptions,
  lean: { x: number; y: number } = { x: 0, y: 0 },
  rec: { glow: number; blur: number; scale: number } = { glow: 1, blur: 0, scale: 1 },
) {
  const r = size / 2;
  const [a, b] = HUES[p.state];
  const act = p.activation;
  // Sound without a cast: PresenceState and OrbState are the same set of
  // states, which is what stops this lookup from ever returning undefined.
  // Depth of field and presence glow multiply rather than replace, so a core
  // that is behind a panel dims whichever state it is in.
  const glow = STATE_GLOW[p.state] * rec.glow;

  ctx.filter = "none";
  ctx.clearRect(0, 0, size, size);
  // Softening the whole body, not just the gradient, is what reads as
  // depth. A blurred edge is the cue the eye uses for distance.
  if (rec.blur > 0.01) ctx.filter = `blur(${rec.blur.toFixed(2)}px)`;

  // --- body ----------------------------------------------------------------
  // The breath is the only clock-driven term left in the whole painter, it is
  // scaled by activation, and it is off entirely under reduced motion.
  const drift = p.state === "idle" && p.move ? idleOffset(p, r) : { x: 0, y: 0 };
  const swell = opts.reduceMotion ? 0 : m.swell;
  const cx = r + drift.x;
  const cy = r + drift.y;
  const radius = r * 0.92 * (1 + swell) * rec.scale;

  const gradient = ctx.createRadialGradient(cx, cy, radius * 0.1, cx, cy, radius);
  gradient.addColorStop(0, tint(b, 0.78 * glow * (0.35 + act * 0.65)));
  gradient.addColorStop(0.55, tint(a, 0.2 * glow * act));
  gradient.addColorStop(1, tint(a, 0));
  ctx.fillStyle = gradient;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fill();

  // --- the eye -------------------------------------------------------------
  // One focus point that moves with the idle move. The cheapest possible thing
  // that reads as looking somewhere, and unlike a rotating ring it does not
  // announce itself as a mechanism.
  ctx.fillStyle = b;
  ctx.globalAlpha = (0.4 + act * 0.5) * glow;
  ctx.beginPath();
  ctx.ellipse(cx + (m.gaze.x + lean.x) * r, cy + (m.gaze.y + lean.y) * r, r * 0.12, r * 0.12 * m.aperture, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;

  // --- the ring, turned by real events --------------------------------------
  // `m.spin` is a function of the event count, not of elapsed time, so a quiet
  // room draws a still ring and a busy one draws a trail that empties as those
  // events age out. Nothing here loops.
  if (m.ring > 0.01) {
    ctx.strokeStyle = a;
    ctx.lineWidth = 1.25;
    ctx.globalAlpha = 0.85 * m.ring;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.52, 0, Math.PI * 2);
    ctx.stroke();

    m.ticks.forEach((lit, i) => {
      if (lit <= 0.01) return;
      // Newest tick leads. The angle comes from the same accumulator as the
      // ring, so the ticks and the ring can never disagree about where the
      // newest event is.
      const at = m.spin - i * ((Math.PI * 2) / Math.max(1, m.ticks.length));
      ctx.globalAlpha = 0.9 * lit;
      ctx.lineWidth = 1 + 1.5 * lit;
      ctx.beginPath();
      ctx.arc(cx, cy, r * 0.62, at - TICK_ARC / 2, at + TICK_ARC / 2);
      ctx.stroke();
    });
  }
  ctx.globalAlpha = 1;

  // --- the audio ring ------------------------------------------------------
  // Drawn only when there is a real amplitude to draw. While speaking without an
  // amplitude tap the transport-derived shape is shown instead, and `measured`
  // stays false so the two never get confused.
  if (m.audio !== null) {
    const amp = m.audio;
    ctx.strokeStyle = b;
    ctx.lineWidth = 1 + 2.5 * amp;
    ctx.globalAlpha = 0.35 + 0.5 * amp;
    ctx.beginPath();
    ctx.arc(cx, cy, r * (0.74 + 0.12 * amp), 0, Math.PI * 2);
    ctx.stroke();

    // Peak hold, so a syllable that already ended is still legible.
    if (m.audioPeak > amp + 0.01) {
      ctx.globalAlpha = 0.3 * (m.audioPeak - amp);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, r * (0.74 + 0.12 * m.audioPeak), -0.6, 0.6);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;

  // --- transition ----------------------------------------------------------
  // A state change tumbles the shell briefly, so a change is never ambiguous
  // even when the two states differ only in colour. Paid for by the real change
  // that just happened, and suppressed under reduced motion.
  if (!opts.reduceMotion && p.transitionEnergy > 0.01) {
    ctx.globalAlpha = p.transitionEnergy * 0.5;
    ctx.strokeStyle = b;
    ctx.lineWidth = 1;
    for (let i = 0; i < 3; i += 1) {
      // The tumble is a function of elapsed time, but only for the few hundred
      // ms a transition energy lasts, so it cannot become a free-running
      // animation.
      const spin = p.time / 260 + (i * Math.PI * 2) / 3;
      ctx.beginPath();
      ctx.arc(cx, cy, r * (0.94 - i * 0.05), spin, spin + 0.5 + p.transitionEnergy * 0.6);
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
}

/**
 * How many real signals the room has produced.
 *
 * Tray entries are the runtime's own readouts and loop turns are real
 * conversations. Both only grow, and the presence machine keeps the count
 * monotonic, so a trimmed tray can never rewind the ring.
 */
function realEventCount(tray: ReadonlyArray<{ at: number }>): number {
  return eventCountFor(tray) + getLoopState().turns.length;
}

export function Orb() {
  const surfaces = useWorkspace((state) => state.surfaces);
  const order = useWorkspace((state) => state.order);
  const viewport = useWorkspace((state) => state.viewport);
  const tray = useWorkspace((state) => state.tray);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Surface rects drawn in front of the core, read inside the frame loop. A ref and
  // not state, because this changes while a panel is dragged and the core must
  // not re-render sixty times a second because someone moved a window.
  const frontRects = useRef<Rect[]>([]);
  // `moved` is how far the press travelled, which is what separates a click on
  // the core (open the fan) from a drag (reposition it).
  const drag = useRef<{ screen: ScreenFrame; dx: number; dy: number; startX: number; startY: number; moved: number } | null>(null);
  const [manual, setManual] = useState<{ x: number; y: number } | null>(null);
  const setFanOpen = useWorkspace((store) => store.setFanOpen);
  const fanOpen = useWorkspace((store) => store.fanOpen);
  // The stage is zoomable. Pointer deltas are screen pixels and the orb is placed
  // in world coordinates, so the grab offset has to be measured in the same
  // units or the orb drifts out from under the cursor · slowly when zoomed out,
  // quickly when zoomed in, which is exactly what it was doing.
  const stage = useWorkspace((store) => store.stage);

  const visible = useMemo(() => visibleSurfaces({ surfaces, order }), [surfaces, order]);
  const auto = useMemo(() => placeOrb(visible, viewport), [visible, viewport]);
  // The visible surfaces, as world rects, for the depth-of-field check.
  // World coordinates, which is what `coverage` is written against -- the
  // core and the panels are positioned in the same space, so mixing screen
  // pixels in here would silently compare two different coordinate systems.
  frontRects.current = useMemo(
    () => visibleSurfaces({ surfaces, order }).map((v) => ({ x: v.geometry.x, y: v.geometry.y, w: v.geometry.w, h: v.geometry.h })),
    [surfaces, order],
  );

  const position = manual ?? auto;
  // A dragged orb keeps the size the room asked for only while it is where the
  // room put it; once you have placed it yourself it stays full size.
  const size = manual ? ORB_SIZE : auto.size;
  // Voice wins over the tray. A room that is mid-task and also being spoken to
  // should look like it is being spoken to · otherwise the mouth moves and the
  // orb still reads as busy, and the user cannot tell which one has attention.
  const trayState = useMemo(() => orbStateFor(tray), [tray]);
  const [voiceState, setVoiceState] = useState<VoiceState>(getVoiceState());
  useEffect(() => subscribeVoice(setVoiceState), []);
  const state = (voiceOverridesOrb(voiceState) ?? trayState) as PresenceState;
  const setOrbPlacement = useWorkspace((store) => store.setOrbPlacement);

  // The frame loop reads all of this every frame, so it lives in refs rather
  // than in the effect's dependency list. Re-creating the loop on a state change
  // used to reseed the presence and restart every animation from zero, which is
  // why the orb twitched each time it changed its mind.
  const stateRef = useRef(state);
  stateRef.current = state;
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const trayRef = useRef(tray);
  trayRef.current = tray;
  const voiceRef = useRef(voiceState);
  voiceRef.current = voiceState;
  /** Real signals seen, monotonic, so a trimmed tray cannot rewind the ring. */
  const eventsRef = useRef(0);

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

    // prefers-reduced-motion is read once and then watched. The canvas
    // animation is driven from rAF, so no media query in CSS can switch it off;
    // the browser setting has to be honoured here in JS or it is not honoured
    // at all. A user who has asked for less motion should not have to reload.
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const opts: PaintOptions = { reduceMotion: query?.matches ?? false };
    const onMotionPreference = (event: MediaQueryListEvent) => {
      opts.reduceMotion = event.matches;
    };
    query?.addEventListener?.("change", onMotionPreference);

    const ratio = window.devicePixelRatio || 1;
    let paintedSize = -1;
    // The presence is a VALUE advanced per frame, not a pile of mutable globals,
    // which is what makes the aliveness replayable from a seed and therefore
    // testable at all.
    const seed = Math.floor(Math.random() * 1e9);
    const rand = makeRandom(seed);
    let p = initialPresence(seed);
    let a = initialAudio();
    let last = performance.now();
    let frame = 0;

    const draw = (time: number) => {
      const dt = Math.min(64, time - last);
      last = time;
      const s = sizeRef.current;
      if (s !== paintedSize) {
        canvas.width = s * ratio;
        canvas.height = s * ratio;
        // setTransform rather than scale, because scale compounds and a resize
        // would leave the orb drawn at a multiple of its own size.
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        paintedSize = s;
      }

      // Real signals, read outside React so a level arriving 20 times a second
      // cannot re-render the room. A missing level stays missing all the way to
      // the painter, which is what stops an invented waveform.
      eventsRef.current = Math.max(eventsRef.current, realEventCount(trayRef.current));
      const reading: AudioReading = {
        mic: readLevel("mic"),
        playback: readLevel("playback"),
        // "speaking" is written when playback starts and cleared when the audio
        // element really ends, so this is transport timing rather than a guess
        // at a duration.
        playing: voiceRef.current.phase === "speaking",
      };

      p = advance(p, dt, stateRef.current, rand, eventsRef.current);
      a = advanceAudio(a, dt, p.time, reading);
      // The eye turns toward whoever is typing. This is the whole of it, and it
      // is worth the line: a system that streams an answer in seconds while
      // visibly ignoring you for all of them reads as busy, not as listening.
      const att = tickAttention(dt, nowMs());
      // A panel drawn over the core is something in front of it, so the core
      // goes soft and dim rather than being either ignored or hidden. Without
      // this the room is flat: the core is either fully present under every
      // panel or absent behind them.
      const rec = recessionFor(
        coverage({ x: position.x, y: position.y, w: size, h: size }, frontRects.current),
      );
      paint(ctx, s, p, motionFor(p, a), opts, attentionGaze(att, position, size), rec);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      query?.removeEventListener?.("change", onMotionPreference);
    };
    // Empty on purpose: everything the loop needs is read through a ref, so it
    // is created once for the life of the component and no state change can
    // restart or reseed it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const listening = voiceState.phase === "listening" || voiceState.phase === "hearing";
  const micMeasured = hasLevelSource("mic");
  const label = STATE_LABEL[state];

  // The title is the honest place to say what the orb does not know. A user
  // hovering an orb that is listening to a microphone it cannot read deserves
  // to be told that, rather than being shown a confident ring.
  const notes = [
    label,
    manual
      ? "placed by you"
      : auto.anchor === "crowded"
        ? "pushed aside, the room is full"
        : auto.anchor === "focused"
          ? "beside the focused surface"
          : "no work in view",
    listening && !micMeasured ? "the microphone is open but its level is not published to the core" : "",
  ].filter(Boolean);
  const title = notes.join(" · ");

  return (
    <div
      className={`orb orb-${state} ${manual ? "orb-manual" : "orb-auto"} ${size < ORB_SIZE ? "orb-small" : ""} ${
        // The core shrinks to a companion beside whatever is open rather than
        // disappearing. placeOrb() already moves it out of the way of live work,
        // and a status light that vanishes the moment you open something cannot
        // be told apart from one that is simply broken.
        visible.length ? "orb-dim" : ""
      } ${listening ? "orb-mic-live" : ""}`}
      style={{ left: position.x, top: position.y, width: size, height: size }}
      title={title}
      role="button"
      tabIndex={0}
      aria-expanded={fanOpen}
      aria-label={`HERMUS core, ${label}. Open the launch fan.`}
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
        // the launch fan. The core IS the launcher now · there is no second dot
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
      <span className={`orb-state orb-label-${state}`}>{label}</span>
    </div>
  );
}
