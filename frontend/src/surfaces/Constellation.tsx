// The memory constellation.
//
// An Obsidian-style graph rendered as a real 3D field: nodes are laid out in a
// rotating 3D cloud, drawn on a 2D canvas with a perspective divide. Drag to
// spin it, scroll to zoom, click a node to read it.
//
// Two things this deliberately does not do:
//
//  - It does not invent edges. Every line here is one the backend derived and
//    labelled with its reasons; the header says out loud that they are inferred,
//    because a graph that looks stored but is not is exactly the lie §4 forbids.
//  - It does not run a physics simulation. Positions come from a deterministic
//    spherical layout seeded by the node id, so the same memory always draws in
//    the same place — a graph that reshuffles on every refresh is unreadable.
//
// Rendering on a 2D canvas rather than WebGL is a deliberate choice for a few
// hundred nodes: a perspective divide is a few multiplies per node, there is no
// shader to compile, and it degrades to a static picture if the RAF loop is
// throttled in a background tab.

import { useEffect, useMemo, useRef, useState } from "react";
import type { MemoryGraph, MemoryNode } from "../api/client";

/** Kind → hue, so the five typed stores read as different substances. */
const KIND_HUE: Record<string, number> = {
  working: 190,
  episodic: 28,
  semantic: 150,
  procedural: 265,
  project: 210,
};

function hueFor(kind: string): number {
  return KIND_HUE[kind] ?? 200;
}

interface Placed extends MemoryNode {
  /** Unit-sphere position, mutated in place each frame by the rotation. */
  px: number;
  py: number;
  pz: number;
  /** Screen position from the last frame, for hit-testing. */
  sx: number;
  sy: number;
  depth: number;
}

/**
 * Deterministic spherical layout.
 *
 * A Fibonacci sphere gives an even distribution with no clustering, and seeding
 * the phase from the id means a given memory sits in the same place on every
 * load. Radius varies with importance so the important ones sit further out and
 * read as bigger through perspective as well as through their own radius.
 */
function layout(nodes: MemoryNode[]): Placed[] {
  const count = nodes.length;
  const golden = Math.PI * (3 - Math.sqrt(5));
  return nodes.map((node, i) => {
    const seed = String(node.id);
    let hash = 0;
    for (let c = 0; c < seed.length; c += 1) hash = (hash * 31 + seed.charCodeAt(c)) >>> 0;
    const jitter = ((hash % 1000) / 1000 - 0.5) * 0.55;

    const y = count > 1 ? 1 - (i / (count - 1)) * 2 : 0;
    const radiusAt = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * i + jitter;

    // Importance 0..10 maps to a shell radius, so the cloud has depth.
    const shell = 0.55 + (Math.max(0, Math.min(10, node.importance)) / 10) * 0.75;

    return {
      ...node,
      px: Math.cos(theta) * radiusAt * shell,
      py: y * shell,
      pz: Math.sin(theta) * radiusAt * shell,
      sx: 0,
      sy: 0,
      depth: 0,
    };
  });
}

export function MemoryConstellation({ graph }: { graph: MemoryGraph }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [spin, setSpin] = useState({ x: -0.35, y: 0.6 });
  const [zoom, setZoom] = useState(1);
  const [picked, setPicked] = useState<Placed | null>(null);
  const drag = useRef<{ x: number; y: number; sx: number; sy: number; moved: number } | null>(null);
  const frame = useRef({ spinX: -0.35, spinY: 0.6, zoom: 1, t: 0 });

  const nodes = useMemo(() => layout(graph.nodes), [graph.nodes]);

  // Spin and zoom live in a ref as well as state: the RAF loop reads them every
  // frame and must not restart on every wheel tick, but React still needs the
  // values to re-render labels.
  useEffect(() => {
    frame.current.spinX = spin.x;
    frame.current.spinY = spin.y;
    frame.current.zoom = zoom;
  }, [spin, zoom]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    const ratio = window.devicePixelRatio || 1;

    const resize = () => {
      const box = wrap.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(box.width * ratio));
      canvas.height = Math.max(1, Math.round(box.height * ratio));
      canvas.style.width = `${box.width}px`;
      canvas.style.height = `${box.height}px`;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(wrap);

    const draw = (time: number) => {
      const box = wrap.getBoundingClientRect();
      const w = box.width;
      const h = box.height;
      const cx = w / 2;
      const cy = h / 2;
      const { spinX, spinY, zoom: z } = frame.current;
      // A slow idle drift, paused the moment the user grabs it, so the field
      // reads as alive without fighting the pointer.
      if (!drag.current) frame.current.spinY += 0.0016;

      const cosY = Math.cos(spinY);
      const sinY = Math.sin(spinY);
      const cosX = Math.cos(spinX);
      const sinX = Math.sin(spinX);
      const focal = Math.max(180, Math.min(w, h) * 0.9);

      ctx.clearRect(0, 0, w, h);

      // Project every node, then draw edges behind the nodes that are in front.
      const projected = nodes.map((node) => {
        // Rotate about Y, then about X.
        const x1 = node.px * cosY - node.pz * sinY;
        const z1 = node.px * sinY + node.pz * cosY;
        const y1 = node.py * cosX - z1 * sinX;
        const z2 = node.py * sinX + z1 * cosX;
        const persp = focal / (focal + z2 * 190);
        return {
          node,
          sx: cx + x1 * persp * 118 * z,
          sy: cy + y1 * persp * 118 * z,
          depth: z2,
          scale: persp,
        };
      });

      const byId = new Map(projected.map((p) => [String(p.node.id), p]));

      ctx.lineWidth = 1;
      for (const edge of graph.edges) {
        const a = byId.get(String(edge.source));
        const b = byId.get(String(edge.target));
        if (!a || !b) continue;
        // Fade with depth so the back of the cloud recedes.
        const avg = 1 - (a.depth + b.depth) / 2.6;
        const alpha = Math.max(0.04, Math.min(0.5, edge.weight * avg * 0.75));
        ctx.strokeStyle = `rgba(120, 200, 220, ${alpha.toFixed(3)})`;
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy);
        ctx.lineTo(b.sx, b.sy);
        ctx.stroke();
      }

      for (const p of projected) {
        const r = Math.max(1.2, p.node.radius * p.scale * z);
        const hue = hueFor(p.node.kind);
        const near = Math.max(0.25, Math.min(1, 1 - p.depth * 0.55));
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${hue}, 78%, ${52 + near * 14}%, ${(0.35 + near * 0.6).toFixed(3)})`;
        ctx.fill();
        if (p.node.pinned) {
          ctx.strokeStyle = `hsla(${hue}, 90%, 74%, ${near.toFixed(3)})`;
          ctx.lineWidth = 1.4;
          ctx.stroke();
        }
        // Only the near ones get a halo, or the far side turns into soup.
        if (near > 0.82) {
          ctx.beginPath();
          ctx.arc(p.sx, p.sy, r + 3.5, 0, Math.PI * 2);
          ctx.strokeStyle = `hsla(${hue}, 90%, 70%, 0.16)`;
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }

      // Write positions back for hit-testing.
      for (const p of projected) {
        p.node.sx = p.sx;
        p.node.sy = p.sy;
        p.node.depth = p.depth;
      }

      frame.current.t = time;
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [nodes, graph.edges]);

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    drag.current = { x: event.clientX, y: event.clientY, sx: spin.x, sy: spin.y, moved: 0 };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const held = drag.current;
    if (!held) return;
    const dx = event.clientX - held.x;
    const dy = event.clientY - held.y;
    held.moved = Math.max(held.moved, Math.hypot(dx, dy));
    setSpin({ x: Math.max(-1.4, Math.min(1.4, held.sx + dy * 0.006)), y: held.sy + dx * 0.006 });
  };
  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const held = drag.current;
    drag.current = null;
    if (!held || held.moved <= 4) {
      // A click, not a spin: pick whatever node is nearest the pointer.
      const box = event.currentTarget.getBoundingClientRect();
      const px = event.clientX - box.left;
      const py = event.clientY - box.top;
      let best: Placed | null = null;
      let bestDist = 22;
      for (const node of nodes) {
        const d = Math.hypot(node.sx - px, node.sy - py);
        if (d < bestDist) {
          bestDist = d;
          best = node;
        }
      }
      setPicked(best);
    }
  };
  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    setZoom((current) => Math.max(0.45, Math.min(3.2, current * (event.deltaY > 0 ? 0.9 : 1.1))));
  };

  if (!graph.nodes.length) {
    return (
      <div className="constellation constellation-empty">
        <p className="muted">nothing stored yet — write something and it appears here</p>
      </div>
    );
  }

  return (
    <div className="constellation">
      <div className="constellation-head">
        <span className="muted tiny">
          {graph.nodes.length} memories · {graph.edges.length} inferred links · drag to spin, scroll to zoom, click to read
        </span>
        {graph.edges_are_inferred ? (
          // Stated, not implied. The store keeps no links; these are derived.
          <span className="constellation-caveat" title={graph.note}>
            links are inferred, not stored
          </span>
        ) : null}
      </div>

      <div className="constellation-stage" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={() => (drag.current = null)}
          onWheel={onWheel}
          role="img"
          aria-label={`memory constellation: ${graph.nodes.length} memories connected by ${graph.edges.length} inferred links`}
        />
        {picked ? (
          <div className="constellation-card">
            <button type="button" className="ghost tiny" onClick={() => setPicked(null)}>
              close
            </button>
            <p className="constellation-card-text">{picked.full}</p>
            <p className="muted tiny">
              {picked.kind} · {picked.project} · importance {picked.importance}
            </p>
          </div>
        ) : null}
      </div>

      <ul className="constellation-legend">
        {graph.kinds.map((kind) => (
          <li key={kind}>
            <i style={{ background: `hsl(${hueFor(kind)}, 78%, 58%)` }} />
            {kind}
          </li>
        ))}
      </ul>
    </div>
  );
}
