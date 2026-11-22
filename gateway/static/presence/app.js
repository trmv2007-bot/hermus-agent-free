/* ===========================================================================
   HERMUS presence renderer.

   One canvas, one character, one arrow. No framework, no build step: the same
   file runs in the Electron shell, in a browser tab for screenshots, and under
   Playwright.

   Layers, painted back to front every frame:
     aura    · additive bloom, breathes
     rings   · three hairline orbitals at different radii, speeds and parallax
     iris    · two counter-rotating arcs — the "eye" that reads as attention
     core    · layered radial gradients + a slow vertical scan
     motes   · sparse deterministic dust on the nearest two rings
     point   · travelling arrow, hairline ring and label, outside the target

   States change colour, speed, tightness and which sub-systems are awake; every
   quantity is eased, so a state change is a morph rather than a cut.
   =========================================================================== */

(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const tokenQuery = params.get("token") ? `?token=${encodeURIComponent(params.get("token"))}` : "";
  const STATIC = params.get("static") === "1";
  const REDUCED =
    params.get("reduced") === "1" ||
    (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const BACKDROP = params.get("backdrop");
  const BARE = params.get("bare") === "1";
  const PINNED = Boolean(params.get("state") || params.get("say") || params.get("point"));

  if (BACKDROP) document.body.dataset.backdrop = BACKDROP;
  if (REDUCED) document.body.dataset.reduced = "true";

  const canvas = document.getElementById("stage");
  const ctx = canvas.getContext("2d");
  const bubble = document.getElementById("bubble");
  const bubbleText = document.getElementById("bubble-text");
  const labelEl = document.getElementById("point-label");
  const labelText = document.getElementById("point-label-text");
  const srEl = document.getElementById("sr");

  const PALETTES = {
    idle: { core: [186, 246, 255], rim: [127, 227, 255], aura: [58, 148, 198] },
    listening: { core: [210, 251, 255], rim: [152, 237, 255], aura: [76, 182, 232] },
    thinking: { core: [172, 240, 255], rim: [110, 214, 255], aura: [54, 138, 198] },
    acting: { core: [236, 253, 255], rim: [178, 246, 255], aura: [72, 196, 230] },
    speaking: { core: [218, 250, 255], rim: [142, 232, 255], aura: [68, 176, 222] },
    error: { core: [255, 216, 194], rim: [255, 166, 128], aura: [206, 100, 72] },
    sleeping: { core: [134, 180, 204], rim: [90, 142, 172], aura: [36, 76, 100] },
  };

  // Mood scalars, blended by state weight. spin/tighten/glow/motes are
  // multipliers, hz is a pulse frequency, drift is the sleeping sink.
  const MOODS = {
    idle: { spin: 1.0, tighten: 1.0, glow: 1.0, motes: 1.0, hz: 0.22, drift: 0, ripple: 0 },
    listening: { spin: 1.25, tighten: 1.04, glow: 1.18, motes: 0.7, hz: 0.6, drift: 0, ripple: 1 },
    thinking: { spin: 3.1, tighten: 0.94, glow: 1.1, motes: 1.7, hz: 1.1, drift: 0, ripple: 0.2 },
    acting: { spin: 1.9, tighten: 0.88, glow: 1.34, motes: 0.45, hz: 1.45, drift: 0, ripple: 0.35 },
    speaking: { spin: 1.15, tighten: 1.0, glow: 1.24, motes: 0.9, hz: 3.1, drift: 0, ripple: 0.5 },
    error: { spin: 0.7, tighten: 1.02, glow: 1.05, motes: 0.5, hz: 5.4, drift: 0, ripple: 0 },
    sleeping: { spin: 0.28, tighten: 1.1, glow: 0.42, motes: 0.12, hz: 0.12, drift: 3.4, ripple: 0 },
  };

  const STATES = Object.keys(MOODS);
  const state = {
    name: "idle",
    weights: { idle: 1 },
    visible: true,
    point: null,
    say: null,
    inputIdleMs: null,
    displays: [],
  };

  let viewport = { w: window.innerWidth, h: window.innerHeight, dpr: 1 };
  let origin = { x: 0, y: 0 };
  let char = { x: 0, y: 0, r: 44 };
  let interactive = false;

  // ------------------------------------------------------------- utilities
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);
  const TAU = Math.PI * 2;

  function mixPalette(weights) {
    const out = { core: [0, 0, 0], rim: [0, 0, 0], aura: [0, 0, 0] };
    let total = 0;
    for (const name of STATES) {
      const w = weights[name] || 0;
      if (w <= 0) continue;
      total += w;
      const pal = PALETTES[name] || PALETTES.idle;
      for (const key of ["core", "rim", "aura"]) {
        out[key][0] += pal[key][0] * w;
        out[key][1] += pal[key][1] * w;
        out[key][2] += pal[key][2] * w;
      }
    }
    if (!total) return PALETTES.idle;
    for (const key of ["core", "rim", "aura"]) {
      out[key] = out[key].map((v) => Math.round(v / total));
    }
    return out;
  }

  function mixMood(weights) {
    const out = { spin: 0, tighten: 0, glow: 0, motes: 0, hz: 0, drift: 0, ripple: 0 };
    let total = 0;
    for (const name of STATES) {
      const w = weights[name] || 0;
      if (w <= 0) continue;
      total += w;
      for (const key of Object.keys(out)) out[key] += (MOODS[name][key] || 0) * w;
    }
    if (!total) return MOODS.idle;
    for (const key of Object.keys(out)) out[key] /= total;
    return out;
  }

  const rgba = (triple, a) => `rgba(${triple[0]},${triple[1]},${triple[2]},${a})`;

  // --------------------------------------------------------------- viewport
  function resize() {
    viewport = {
      w: window.innerWidth,
      h: window.innerHeight,
      dpr: Math.min(window.devicePixelRatio || 1, 2),
    };
    canvas.width = Math.round(viewport.w * viewport.dpr);
    canvas.height = Math.round(viewport.h * viewport.dpr);
    canvas.style.width = `${viewport.w}px`;
    canvas.style.height = `${viewport.h}px`;
    ctx.setTransform(viewport.dpr, 0, 0, viewport.dpr, 0, 0);

    const layout = state.displays && state.displays.length ? state.displays : null;
    if (layout) {
      origin = {
        x: Math.min(...layout.map((d) => d.bounds.x)),
        y: Math.min(...layout.map((d) => d.bounds.y)),
      };
    } else {
      origin = { x: 0, y: 0 };
    }

    const dockParam = (params.get("dock") || "br").toLowerCase();
    const primary = layout ? layout.find((d) => d.index === 0) || layout[0] : null;
    const home = primary
      ? { x: primary.bounds.x - origin.x, y: primary.bounds.y - origin.y, w: primary.bounds.w, h: primary.bounds.h }
      : { x: 0, y: 0, w: viewport.w, h: viewport.h };
    const margin = viewport.w < 520 ? 44 : 62;
    const radius = clamp(Math.min(home.w, home.h) * 0.052, 30, 50);
    char = {
      r: radius,
      x:
        dockParam === "bl" ? home.x + margin + radius
        : dockParam === "bc" ? home.x + home.w / 2
        : dockParam === "br" ? home.x + home.w - margin - radius
        : home.x + home.w - margin - radius,
      y:
        dockParam === "bc" ? home.y + home.h - margin - radius
        : home.y + home.h - margin - radius,
    };
    draw(performance.now() / 1000, true);
  }

  // ---------------------------------------------------------------- texture
  const MOTES = (() => {
    const out = [];
    let seed = 20261123;
    const rand = () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed / 0x7fffffff;
    };
    for (let i = 0; i < 22; i += 1) {
      out.push({
        radius: 1.16 + rand() * 1.24,
        angle: rand() * TAU,
        speed: 0.05 + rand() * 0.22,
        size: 0.7 + rand() * 1.2,
        alpha: 0.16 + rand() * 0.4,
        depth: 0.4 + rand() * 0.9,
      });
    }
    return out;
  })();

  function drawAura(t, mood, pal) {
    const breath = 1 + Math.sin((t / 4.6) * TAU) * 0.018;
    const r = char.r * 2.9 * breath * mood.tighten;
    const g = ctx.createRadialGradient(char.x, char.y, char.r * 0.2, char.x, char.y, r);
    g.addColorStop(0, rgba(pal.aura, 0.34 * mood.glow));
    g.addColorStop(0.42, rgba(pal.aura, 0.1 * mood.glow));
    g.addColorStop(1, rgba(pal.aura, 0));
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(char.x, char.y, r, 0, TAU);
    ctx.fill();
    ctx.restore();
  }

  function ring(cx, cy, radius, dash, spin, stroke, width) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(spin);
    ctx.beginPath();
    ctx.setLineDash(dash);
    ctx.lineWidth = width;
    ctx.strokeStyle = stroke;
    ctx.arc(0, 0, radius, 0, TAU);
    ctx.stroke();
    ctx.restore();
  }

  function drawRings(t, mood, pal) {
    const R = char.r;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    // Parallax: each orbital spins at its own rate and breathes on its own
    // phase, so the stack reads as depth instead of one flat dial.
    ring(char.x, char.y, R * 1.92 * breatheOffset(t, 0) * mood.tighten, [1.5, 8], t * 0.06 * mood.spin,
      rgba(pal.rim, 0.2 * mood.glow), 1);
    ring(char.x, char.y, R * 1.58 * breatheOffset(t, 0.6) * mood.tighten, [22, 30], -t * 0.11 * mood.spin,
      rgba(pal.rim, 0.26 * mood.glow), 1);
    ring(char.x, char.y, R * 1.3 * breatheOffset(t, 1.1) * mood.tighten, [46, 96], t * 0.045 * mood.spin,
      rgba(pal.rim, 0.34 * mood.glow), 1);
    ctx.restore();
  }

  function breatheOffset(t, phase) {
    return 1 + Math.sin((t / 4.6) * TAU + phase) * 0.014;
  }

  function drawIris(t, mood, pal, toward) {
    const R = char.r;
    const spin = t * 0.16 * mood.spin;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.lineWidth = 1.6;
    for (let i = 0; i < 2; i += 1) {
      const dir = i === 0 ? 1 : -1;
      const radius = R * (1.42 - i * 0.1) * mood.tighten;
      const start = spin * dir + i * Math.PI;
      const span = 1.15 + Math.sin(t * 0.5 + i) * 0.12;
      const grad = ctx.createLinearGradient(char.x - radius, char.y, char.x + radius, char.y);
      grad.addColorStop(0, rgba(pal.rim, 0));
      grad.addColorStop(0.5, rgba(pal.rim, 0.5 * mood.glow));
      grad.addColorStop(1, rgba(pal.rim, 0));
      ctx.strokeStyle = grad;
      ctx.beginPath();
      ctx.arc(char.x, char.y, radius, start, start + span);
      ctx.stroke();
    }
    if (toward) {
      // Acting: one short arc aligned with the bearing of the target.
      const radius = R * 1.52 * mood.tighten;
      ctx.strokeStyle = rgba(pal.core, 0.62);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(char.x, char.y, radius, toward - 0.22, toward + 0.22);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawCore(t, mood, pal) {
    const R = char.r;
    const pulse = 1 + Math.sin(t * TAU * mood.hz) * 0.02 * Math.min(1, mood.hz);
    const radius = R * 0.92 * pulse * mood.tighten;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";

    const core = ctx.createRadialGradient(
      char.x - radius * 0.22,
      char.y - radius * 0.28,
      radius * 0.06,
      char.x,
      char.y,
      radius
    );
    core.addColorStop(0, rgba(pal.core, 0.96));
    core.addColorStop(0.32, rgba(pal.core, 0.62 * mood.glow));
    core.addColorStop(0.72, rgba(pal.rim, 0.2 * mood.glow));
    core.addColorStop(1, rgba(pal.rim, 0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(char.x, char.y, radius, 0, TAU);
    ctx.fill();

    // Vertical scan: one hairline crossing the core, never bright enough to read as a bar.
    const scanY = char.y + ((t * 0.34) % 1.6 - 0.8) * radius;
    const scan = ctx.createLinearGradient(char.x - radius, scanY, char.x + radius, scanY);
    scan.addColorStop(0, rgba(pal.core, 0));
    scan.addColorStop(0.5, rgba(pal.core, 0.16 * mood.glow));
    scan.addColorStop(1, rgba(pal.core, 0));
    ctx.fillStyle = scan;
    ctx.fillRect(char.x - radius, scanY, radius * 2, 1);

    // Specular highlight — the one crisp detail that makes it read as a lens.
    const spec = ctx.createRadialGradient(
      char.x - radius * 0.34,
      char.y - radius * 0.4,
      0,
      char.x - radius * 0.34,
      char.y - radius * 0.4,
      radius * 0.42
    );
    spec.addColorStop(0, "rgba(255,255,255,0.5)");
    spec.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = spec;
    ctx.beginPath();
    ctx.arc(char.x - radius * 0.34, char.y - radius * 0.4, radius * 0.42, 0, TAU);
    ctx.fill();

    ctx.strokeStyle = rgba(pal.rim, 0.42 * mood.glow);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(char.x, char.y, radius, 0, TAU);
    ctx.stroke();
    ctx.restore();
  }

  function drawMotes(t, mood, pal) {
    if (mood.motes <= 0.05) return;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const mote of MOTES) {
      const angle = mote.angle + t * mote.speed * mood.spin * (mote.depth > 1 ? -1 : 1);
      const radius = char.r * mote.radius * breatheOffset(t, mote.depth * 1.7) * mood.tighten;
      const x = char.x + Math.cos(angle) * radius;
      const y = char.y + Math.sin(angle) * radius * 0.98;
      ctx.fillStyle = rgba(pal.core, mote.alpha * mood.motes * 0.8);
      ctx.beginPath();
      ctx.arc(x, y, mote.size * (0.7 + mote.depth * 0.5), 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }

  function drawRipples(t, mood, pal) {
    if (mood.ripple <= 0.05) return;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    const count = 2;
    for (let i = 0; i < count; i += 1) {
      const phase = ((t * 0.55 + i / count) % 1);
      const radius = char.r * (1.32 + phase * 1.05);
      const alpha = (1 - phase) * 0.3 * mood.ripple * mood.glow;
      ctx.strokeStyle = rgba(pal.rim, alpha);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(char.x, char.y, radius, 0, TAU);
      ctx.stroke();
    }
    ctx.restore();
  }

  // ------------------------------------------------------- point targeting
  // The live point: rect in DIP space, plus a local clock so the retract is
  // driven by this renderer's own timeline rather than the server's clock.
  const choreo = {
    id: null,
    rect: null,
    label: "",
    startedAt: 0,
    totalMs: 0,
    fadingOut: false,
    fadeAt: 0,
  };

  function setPoint(point, now) {
    if (!point) {
      if (choreo.rect) {
        choreo.fadingOut = true;
        choreo.fadeAt = now;
      }
      return;
    }
    if (point.id === choreo.id) return;
    choreo.id = point.id;
    choreo.rect = {
      x: point.x - origin.x,
      y: point.y - origin.y,
      w: point.w,
      h: point.h,
    };
    choreo.label = point.label || "";
    choreo.startedAt = now;
    choreo.totalMs = Math.max(600, Number(point.ttl_ms || 4200));
    choreo.fadingOut = false;
    const tone = state.name === "error" ? "error" : "idle";
    labelEl.dataset.tone = tone;
    labelText.textContent = choreo.label;
    srEl.textContent = choreo.label ? `Pointing at ${choreo.label}` : "Pointing at a screen location";
  }

  // Nearest point on the ring box (target inflated by `gap`) along the ray from
  // `from` to the rect centre. The arrow therefore always stops outside the
  // target, at any angle.
  function ringIntersection(from, rect, gap) {
    const cx = rect.x + rect.w / 2;
    const cy = rect.y + rect.h / 2;
    const halfW = rect.w / 2 + gap;
    const halfH = rect.h / 2 + gap;
    const dx = from.x - cx;
    const dy = from.y - cy;
    if (dx === 0 && dy === 0) return { x: cx, y: cy - halfH };
    const sx = dx === 0 ? Infinity : halfW / Math.abs(dx);
    const sy = dy === 0 ? Infinity : halfH / Math.abs(dy);
    const s = Math.min(sx, sy);
    return { x: cx + dx * s, y: cy + dy * s };
  }

  function bezierAt(p0, p1, p2, t) {
    const mt = 1 - t;
    return {
      x: mt * mt * p0.x + 2 * mt * t * p1.x + t * t * p2.x,
      y: mt * mt * p0.y + 2 * mt * t * p1.y + t * t * p2.y,
    };
  }

  function drawPoint(t, now, pal) {
    if (!choreo.rect) return;
    const rect = choreo.rect;
    const gap = 8;
    let progress = 1;
    let fade = 1;
    const elapsed = now - choreo.startedAt;

    if (choreo.fadingOut) {
      progress = 1;
      fade = clamp(1 - (now - choreo.fadeAt) / 260, 0, 1);
      if (fade <= 0) {
        choreo.rect = null;
        choreo.id = null;
        labelEl.dataset.visible = "false";
        return;
      }
    } else if (!REDUCED && !STATIC) {
      progress = clamp(elapsed / 420, 0, 1);
      const remaining = choreo.totalMs - elapsed;
      if (remaining < 420) fade = clamp(remaining / 420, 0, 1);
    }

    const centre = { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 };
    const offscreen =
      centre.x < -40 || centre.y < -40 || centre.x > viewport.w + 40 || centre.y > viewport.h + 40;

    if (offscreen) {
      drawBeacon(centre, pal, fade);
      labelEl.dataset.visible = "false";
      return;
    }

    const start = {
      x: char.x + (centre.x - char.x) * 0.12,
      y: char.y + (centre.y - char.y) * 0.12,
    };
    const end = ringIntersection(start, rect, gap);
    const midDistance = Math.hypot(end.x - start.x, end.y - start.y);
    const bend = clamp(midDistance * 0.14, 14, 74);
    const perp = { x: -(end.y - start.y), y: end.x - start.x };
    const perpLen = Math.hypot(perp.x, perp.y) || 1;
    const control = {
      x: (start.x + end.x) / 2 + (perp.x / perpLen) * bend,
      y: (start.y + end.y) / 2 + (perp.y / perpLen) * bend,
    };

    ctx.save();
    ctx.globalCompositeOperation = "lighter";

    // While the arrow travels it is the brightest thing on screen; once it has
    // arrived the long haul across the desktop drops to a hairline and only a
    // short stub, the head and the ring keep the attention. A pointer that left
    // a bright line lying over whatever it crossed would be covering things it
    // has nothing to do with.
    const travelled = easeOutCubic(progress);
    const head = bezierAt(start, control, end, travelled);
    const arrived = progress >= 1;
    const settle = arrived ? clamp((elapsed - 420) / 520, 0, 1) : 0;
    const stubT = arrived ? 0.86 : Math.max(0, travelled - 0.22);

    ctx.strokeStyle = rgba(pal.rim, (arrived ? 0.055 : 0.1) * fade);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.quadraticCurveTo(control.x, control.y, end.x, end.y);
    ctx.stroke();

    ctx.strokeStyle = rgba(pal.core, (0.55 - 0.2 * settle) * fade);
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    const tailPoint = bezierAt(start, control, end, stubT);
    ctx.moveTo(tailPoint.x, tailPoint.y);
    for (let s = stubT; s <= travelled; s += 0.02) {
      const p = bezierAt(start, control, end, s);
      ctx.lineTo(p.x, p.y);
    }
    ctx.lineTo(head.x, head.y);
    ctx.stroke();

    if (progress < 1) {
      const glow = ctx.createRadialGradient(head.x, head.y, 0, head.x, head.y, 12);
      glow.addColorStop(0, rgba(pal.core, 0.9 * fade));
      glow.addColorStop(1, rgba(pal.core, 0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(head.x, head.y, 12, 0, TAU);
      ctx.fill();
    } else {
      // A chevron where the arrow landed: the "here" mark, pointing along the
      // tangent, sitting on the ring rather than inside the target.
      const before = bezierAt(start, control, end, 0.985);
      const angle = Math.atan2(head.y - before.y, head.x - before.x);
      // Nudge the mark outward along its own direction so its wings cannot
      // reach back inside the ring and touch the target.
      ctx.save();
      ctx.translate(head.x + Math.cos(angle) * 6, head.y + Math.sin(angle) * 6);
      ctx.rotate(angle);
      ctx.strokeStyle = rgba(pal.core, (0.85 - 0.35 * settle) * fade);
      ctx.lineWidth = 1.7;
      ctx.beginPath();
      ctx.moveTo(-7, -6);
      ctx.lineTo(0, 0);
      ctx.lineTo(-7, 6);
      ctx.stroke();
      ctx.restore();
    }
    ctx.restore();

    // Ring: hairline box outside the target + four corner brackets + pulses.
    const ringed = inflateRect(rect, gap);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    const pulsePhase = ((now - choreo.startedAt) / 1500) % 1;
    const pulseAlpha = REDUCED || STATIC ? 0 : (1 - pulsePhase) * 0.3 * clamp(progress, 0, 1) * fade;
    if (pulseAlpha > 0.01) {
      const grown = inflateRect(ringed, pulsePhase * 16);
      ctx.strokeStyle = rgba(pal.rim, pulseAlpha);
      ctx.lineWidth = 1;
      roundRect(ctx, grown, 12);
      ctx.stroke();
    }
    ctx.strokeStyle = rgba(pal.rim, 0.68 * clamp(progress * 1.4, 0, 1) * fade);
    ctx.lineWidth = 1.2;
    roundRect(ctx, ringed, 10);
    ctx.stroke();

    const bracket = Math.min(18, ringed.w / 3, ringed.h / 3);
    ctx.strokeStyle = rgba(pal.core, 0.9 * fade);
    ctx.lineWidth = 1.6;
    corners(ringed, bracket);
    ctx.restore();

    // Label: outside the ring, measured, then placed by the same rule the
    // Python geometry uses (above → below → mirror to stay on screen).
    if (choreo.label) {
      labelEl.textContent = choreo.label;
      if (!labelEl.dataset.w) {
        labelEl.dataset.visible = "true";
        labelEl.dataset.w = String(labelEl.offsetWidth);
        labelEl.dataset.h = String(labelEl.offsetHeight);
        labelEl.dataset.visible = "false";
      }
      const size = [Number(labelEl.dataset.w), Number(labelEl.dataset.h)];
      const box = placeOutside(ringed, size, [viewport.w, viewport.h], 8);
      if (box) {
        labelEl.style.transform = `translate3d(${Math.round(box.x)}px, ${Math.round(box.y)}px, 0)`;
        labelEl.dataset.visible = clamp(progress * 2 - 0.6, 0, 1) > 0.35 && !choreo.fadingOut ? "true" : "false";
      } else {
        labelEl.dataset.visible = "false";
      }
    } else {
      labelEl.dataset.visible = "false";
    }
  }

  function inflateRect(rect, pad) {
    return { x: rect.x - pad, y: rect.y - pad, w: rect.w + pad * 2, h: rect.h + pad * 2 };
  }

  function roundRect(context, r, radius) {
    const rad = Math.min(radius, r.w / 2, r.h / 2);
    context.beginPath();
    context.moveTo(r.x + rad, r.y);
    context.arcTo(r.x + r.w, r.y, r.x + r.w, r.y + r.h, rad);
    context.arcTo(r.x + r.w, r.y + r.h, r.x, r.y + r.h, rad);
    context.arcTo(r.x, r.y + r.h, r.x, r.y, rad);
    context.arcTo(r.x, r.y, r.x + r.w, r.y, rad);
    context.closePath();
  }

  function corners(r, len) {
    const pts = [
      [r.x, r.y, 1, 1],
      [r.x + r.w, r.y, -1, 1],
      [r.x, r.y + r.h, 1, -1],
      [r.x + r.w, r.y + r.h, -1, -1],
    ];
    ctx.beginPath();
    for (const [x, y, sx, sy] of pts) {
      ctx.moveTo(x + sx * len, y);
      ctx.lineTo(x, y);
      ctx.lineTo(x, y + sy * len);
    }
    ctx.stroke();
  }

  function placeOutside(ring, size, canvasSize, gap) {
    const [w, h] = size;
    const x = clamp(ring.x + ring.w / 2 - w / 2, 6, Math.max(6, canvasSize[0] - w - 6));
    const above = ring.y - h - gap;
    const below = ring.y + ring.h + gap;
    if (above >= 6) return { x, y: above };
    if (below + h <= canvasSize[1] - 6) return { x, y: below };
    return null;
  }

  function drawBeacon(centre, pal, fade) {
    const angle = Math.atan2(centre.y - char.y, centre.x - char.x);
    const radius = char.r * 1.95;
    const x = char.x + Math.cos(angle) * radius;
    const y = char.y + Math.sin(angle) * radius;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.strokeStyle = rgba(pal.core, 0.7 * fade);
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(-6, -6);
    ctx.lineTo(6, 0);
    ctx.lineTo(-6, 6);
    ctx.stroke();
    ctx.restore();
  }

  // ---------------------------------------------------------------- bubble
  const bubbleState = { text: "", pending: "" };

  function typingNow() {
    const idle = state.inputIdleMs;
    return typeof idle === "number" && idle < 700;
  }

  function updateBubble(now) {
    const activeSay = state.say && Date.now() / 1000 < Number(state.say.expires_at || 0);
    const wanted = activeSay ? state.say.text : "";
    if (wanted && wanted !== bubbleState.text) {
      // Never appear while the person is typing in that spot; hold, then show.
      bubbleState.pending = wanted;
    }
    if (!wanted) {
      bubbleState.pending = "";
      bubbleState.text = "";
      bubble.dataset.visible = "false";
      return;
    }
    if (bubbleState.pending && !typingNow()) {
      bubbleState.text = bubbleState.pending;
      bubbleState.pending = "";
      bubbleText.textContent = bubbleState.text;
      srEl.textContent = bubbleState.text;
    }
    if (bubbleState.text && !typingNow()) {
      const width = bubble.offsetWidth;
      const height = bubble.offsetHeight;
      const x = clamp(char.x - width / 2, 8, Math.max(8, viewport.w - width - 8));
      const y = clamp(char.y - char.r * 1.9 - height - 12, 8, Math.max(8, viewport.h - height - 8));
      bubble.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(y)}px, 0)`;
      bubble.dataset.visible = "true";
    } else {
      bubble.dataset.visible = "false";
    }
  }

  // ------------------------------------------------------------------ loop
  let lastFrame = 0;

  function stepWeights(dt) {
    const target = state.name in MOODS ? state.name : "idle";
    for (const name of STATES) {
      const goal = name === target ? 1 : 0;
      const current = state.weights[name] || 0;
      const speed = goal > current ? 1.9 : 2.6;
      const next = current + (goal - current) * clamp(dt * speed, 0, 1);
      state.weights[name] = Math.abs(next - goal) < 0.004 ? goal : next;
    }
  }

  function draw(t, force) {
    const now = performance.now();
    const dt = lastFrame ? Math.min(0.05, (now - lastFrame) / 1000) : 0.016;
    lastFrame = now;
    if (STATIC || force) {
      for (const name of STATES) state.weights[name] = name === state.name ? 1 : 0;
    } else {
      stepWeights(dt);
    }

    ctx.clearRect(0, 0, viewport.w, viewport.h);
    if (BARE) {
      updateBubble(now);
      return;
    }
    const pal = mixPalette(state.weights);
    const mood = mixMood(state.weights);

    const drift = state.name === "sleeping" ? Math.sin(t * 0.6) * 1.4 : 0;
    const bob = Math.sin(t * 0.5) * 1.1;
    const anchor = { x: char.x, y: char.y + drift + bob };
    const savedChar = { x: char.x, y: char.y };
    char = { ...char, x: anchor.x, y: anchor.y };

    drawAura(t, mood, pal);
    drawRings(t, mood, pal);
    drawRipples(t, mood, pal);
    drawIris(t, mood, pal, choreo.rect ? Math.atan2(choreoCenterY() - char.y, choreoCenterX() - char.x) : null);
    drawCore(t, mood, pal);
    drawMotes(t, mood, pal);
    if (choreo.rect) drawPoint(t, now, pal);

    updateBubble(now);
    char = { ...char, x: savedChar.x, y: savedChar.y };
  }

  function choreoCenterX() {
    return choreo.rect ? choreo.rect.x + choreo.rect.w / 2 : char.x;
  }

  function choreoCenterY() {
    return choreo.rect ? choreo.rect.y + choreo.rect.h / 2 : char.y;
  }

  function frame() {
    const t = performance.now() / 1000;
    draw(t, false);
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ data
  function applySnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;
    // A pinned page (?state / ?say / ?point) renders exactly what it was asked
    // for: screenshots and the typing-guard check must not be overwritten by
    // whatever the live gateway happens to be doing.
    if (PINNED) return;
    if (snapshot.state && snapshot.state in MOODS) state.name = snapshot.state;
    state.visible = snapshot.visible !== false;
    if (Array.isArray(snapshot.displays) && snapshot.displays.length !== state.displays.length) {
      state.displays = snapshot.displays;
      resize();
    }
    state.say = snapshot.say || null;
    if (typeof snapshot.input_idle_ms === "number") state.inputIdleMs = snapshot.input_idle_ms;
    const now = performance.now();
    state.point = snapshot.point || null;
    setPoint(snapshot.point, now);
    if (shell) {
      if (state.visible) shell.show();
      else shell.hide();
    }
  }

  async function poll() {
    try {
      const res = await fetch(`/api/presence/state${tokenQuery}`, { cache: "no-store" });
      if (res.ok) applySnapshot(await res.json());
    } catch (err) {
      /* offline gateway: keep the character breathing, retry on next tick */
    }
  }

  function connect() {
    if (STATIC) return;
    try {
      const source = new EventSource(`/api/presence/stream${tokenQuery}`);
      source.onmessage = (event) => {
        try {
          applySnapshot(JSON.parse(event.data));
        } catch (err) {
          /* a truncated frame is not worth losing the stream over */
        }
      };
      source.onerror = () => {
        /* EventSource retries on its own; polling covers a proxy that kills it */
        poll();
      };
    } catch (err) {
      poll();
    }
    poll();
    setInterval(poll, 5000);
  }

  // ------------------------------------------------- pinned demo parameters
  function applyPinnedParams() {
    const pinned = params.get("state");
    if (pinned && pinned in MOODS) state.name = pinned;
    const pointParam = params.get("point");
    if (pointParam) {
      const [x, y, w, h] = pointParam.split(",").map(Number);
      if ([x, y, w, h].every((v) => Number.isFinite(v))) {
        const label = params.get("label") || "";
        state.point = { id: "pinned", x, y, w, h, label, ttl_ms: 60_000 };
        setPoint(state.point, performance.now() - 900);
      }
    }
    const sayParam = params.get("say");
    if (sayParam) {
      state.say = { text: sayParam, expires_at: Date.now() / 1000 + 900 };
      state.inputIdleMs = 5000;
    }
  }

  // ----------------------------------------------------------------- shell
  const shell = window.presenceShell || null;
  if (shell) {
    shell.ready?.();
    shell.onInputIdle?.((idleMs) => {
      state.inputIdleMs = typeof idleMs === "number" ? idleMs * 1000 : idleMs;
    });
    window.addEventListener("mousemove", (event) => {
      const near = Math.hypot(event.clientX - char.x, event.clientY - char.y) < char.r * 1.35;
      if (near !== interactive) {
        interactive = near;
        shell.setInteractive?.(near);
      }
    });
  }

  window.addEventListener("resize", resize);
  resize();
  applyPinnedParams();
  if (STATIC) {
    draw(2.6, true);
    setTimeout(() => draw(2.6, true), 60);
  } else {
    requestAnimationFrame(frame);
    connect();
  }
  document.documentElement.dataset.ready = "true";
})();
