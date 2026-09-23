/* God's Eye — dependency-free 3D orbital globe for the HERMUS cockpit.
 *
 * A point-cloud sphere rendered with plain canvas math (no three.js / Cesium),
 * so the control room stays local-first, offline and build-free. The motif is
 * credited to the open-source gods-eye-view project; this renderer is an
 * original visual only — it plots no real geodata and makes no data claims.
 *
 * mountGodsEye(canvas, opts) lets the same renderer drive extra canvases
 * (the JARVIS hood instrument space) without duplicating the math.
 */
(function () {
  "use strict";

  const REDUCED =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Fibonacci sphere: even point distribution without a library.
  const POINTS = [];
  const N = 900;
  const GA = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = GA * i;
    POINTS.push([Math.cos(th) * r, y, Math.sin(th) * r]);
  }

  function mountGodsEye(canvas, opts) {
    if (!canvas) return null;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    opts = opts || {};
    const speed = opts.speed || 0.0035;
    const isActive = opts.active || function () { return true; };

    const TILT = 0.42; // fixed pitch so we look down on the sphere ("god's eye")
    let yaw = 0.6;
    let w = 0;
    let h = 0;
    let dpr = 1;

    function resize() {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      const rect = canvas.getBoundingClientRect();
      w = Math.max(1, Math.round(rect.width));
      h = Math.max(1, Math.round(rect.height));
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function project(p, cy, sy, ct, st, cx, cyy, R) {
      // rotate about Y (yaw), then about X (tilt), orthographic projection.
      const x = p[0] * cy + p[2] * sy;
      const z = -p[0] * sy + p[2] * cy;
      const y = p[1] * ct - z * st;
      const z2 = p[1] * st + z * ct;
      return { x: cx + x * R, y: cyy + y * R, depth: z2 };
    }

    function draw() {
      ctx.clearRect(0, 0, w, h);
      const cx = w / 2;
      const cyy = h / 2;
      const R = Math.min(w, h) * 0.36;
      const cy = Math.cos(yaw);
      const sy = Math.sin(yaw);
      const ct = Math.cos(TILT);
      const st = Math.sin(TILT);

      // Atmosphere glow.
      const glow = ctx.createRadialGradient(cx, cyy, R * 0.2, cx, cyy, R * 1.5);
      glow.addColorStop(0, "rgba(56,189,248,0.16)");
      glow.addColorStop(0.6, "rgba(56,189,248,0.05)");
      glow.addColorStop(1, "rgba(56,189,248,0)");
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, w, h);

      // Sphere limb.
      ctx.beginPath();
      ctx.arc(cx, cyy, R, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(125,211,252,0.35)";
      ctx.lineWidth = 1;
      ctx.stroke();

      // Latitude rings (ellipses under the tilt) for a wireframe read.
      ctx.strokeStyle = "rgba(125,211,252,0.10)";
      for (let lat = -60; lat <= 60; lat += 30) {
        const rad = (lat * Math.PI) / 180;
        const rr = Math.cos(rad) * R;
        const yy = Math.sin(rad) * R * st;
        ctx.beginPath();
        ctx.ellipse(cx, cyy - yy, rr, rr * Math.abs(ct) * 0.9 + 0.5, 0, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Point cloud, back-to-front so near points read brighter.
      const proj = POINTS.map((p) => project(p, cy, sy, ct, st, cx, cyy, R));
      proj.sort((a, b) => a.depth - b.depth);
      for (const q of proj) {
        const front = q.depth >= 0;
        const a = front ? 0.25 + q.depth * 0.6 : 0.06;
        const size = front ? 1.4 + q.depth * 1.2 : 0.8;
        ctx.beginPath();
        ctx.arc(q.x, q.y, size, 0, Math.PI * 2);
        ctx.fillStyle = front
          ? "rgba(125,211,252," + a.toFixed(3) + ")"
          : "rgba(100,116,139," + a.toFixed(3) + ")";
        ctx.fill();
      }

      // Two orbital rings to sell the satellite/god's-eye framing.
      ctx.save();
      ctx.translate(cx, cyy);
      for (const [rx, ry, rot, alpha] of [
        [R * 1.35, R * 0.42, -0.35, 0.3],
        [R * 1.6, R * 0.5, 0.25, 0.16],
      ]) {
        ctx.beginPath();
        ctx.ellipse(0, 0, rx, ry, rot, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(56,189,248," + alpha + ")";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      ctx.restore();
    }

    function tick() {
      // Only animate while the mount is actually on screen and visible.
      if (!document.hidden && isActive()) {
        yaw += speed;
        draw();
      }
      requestAnimationFrame(tick);
    }

    window.addEventListener("resize", () => {
      resize();
      draw();
    });

    resize();
    draw();
    if (!REDUCED) requestAnimationFrame(tick);
    return { resize: resize, draw: draw };
  }

  window.mountGodsEye = mountGodsEye;

  const defaultCanvas = document.getElementById("godsEyeCanvas");
  if (defaultCanvas) {
    mountGodsEye(defaultCanvas, {
      active: function () {
        const t = document.getElementById("tab-chat");
        return !!t && t.classList.contains("on");
      },
    });
  }
})();
