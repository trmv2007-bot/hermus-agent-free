/* HERMUS living core — a state projection, never a decoration.
 *
 * Every ring, arc and spoke is derived from something the gateway actually
 * reported: /readyz, /api/jarvis/status, /computer/status, /remote/approvals,
 * /api/v1/harness/overview and the real event feed. When nothing is known the
 * core goes offline and says so instead of breathing convincingly.
 *
 * No dependencies, one canvas, capped DPR, paused while the tab is hidden and
 * reduced to a still frame when the user asks for calm or the OS requests
 * reduced motion.
 */
(function () {
  "use strict";

  var canvas = document.getElementById("hermusCore");
  if (!canvas || !canvas.getContext) return;

  var ctx = canvas.getContext("2d");
  var stateEl = document.getElementById("coreState");
  var nameEl = document.getElementById("coreName");
  var subEl = document.getElementById("coreSub");
  var nowEl = document.getElementById("coreNow");
  var detailEl = document.getElementById("coreDetail");
  var queueEl = document.getElementById("nowQueue");
  var agentsEl = document.getElementById("nowAgents");
  var modelEl = document.getElementById("nowModel");
  var contextEl = document.getElementById("nowContext");
  var harnessCells = document.getElementById("harnessContext");
  var harnessTurn = document.getElementById("harnessTurn");

  var COLORS = {
    offline: "#7d94ad",
    idle: "#5fd8ff",
    listening: "#43e8b8",
    thinking: "#a7ebff",
    acting: "#ffc86b",
    verifying: "#7fa8ff",
    blocked: "#ffc86b",
    error: "#ff6584"
  };

  var state = "offline";
  var progress = 0;
  var burst = 0;
  var size = 260;
  var dpr = 1;
  var lastEventId = "";
  var primed = false;
  var lastEventAt = 0;
  var toolUntil = 0;
  var verifyUntil = 0;
  var errorUntil = 0;
  var model = "--";
  var coreBusy = false;
  var eventsBusy = false;

  function calm() {
    return document.documentElement.classList.contains("calm");
  }

  function reduced() {
    try {
      return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function resize() {
    var rect = canvas.getBoundingClientRect();
    size = Math.max(140, Math.min(rect.width || 260, 320));
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function ring(radius, from, to, color, width, alpha, segments) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    if (segments) {
      ctx.setLineDash(segments);
    }
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, radius, from, to);
    ctx.stroke();
    ctx.restore();
  }

  function draw(t) {
    var c = size / 2;
    var color = COLORS[state] || COLORS.idle;
    var motion = !calm() && !reduced();
    var clock = motion ? t / 1000 : 0;
    var breathe = 1 + Math.sin(clock * 1.3) * (state === "idle" ? 0.012 : 0.02);

    ctx.clearRect(0, 0, size, size);

    // Ambient halo
    var halo = ctx.createRadialGradient(c, c, size * 0.1, c, c, size * 0.5);
    halo.addColorStop(0, hexA(color, 0.20));
    halo.addColorStop(0.55, hexA(color, 0.06));
    halo.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, size, size);

    // Outer HUD ring (slow counter-rotation)
    ring(size * 0.46, 0, Math.PI * 2, color, 1, 0.22);
    ring(size * 0.46, clock * 0.18, clock * 0.18 + Math.PI * 0.6, color, 1, 0.55);
    ring(size * 0.44, 0, Math.PI * 2, color, 1, 0.14, [2, 7]);

    // Progress arc: mission/queue completion when known, otherwise a quiet tick
    var p = Math.max(0, Math.min(1, progress));
    ring(size * 0.39, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (p || 0.002), color, 2, p ? 0.85 : 0.18);

    // Thinking: three counter-rotating arcs
    if (state === "thinking" || state === "verifying") {
      var spin = state === "verifying" ? -1 : 1;
      for (var i = 0; i < 3; i++) {
        var from = spin * clock * (0.9 + i * 0.22) + (i * Math.PI * 2) / 3;
        ring(size * (0.31 - i * 0.03), from, from + Math.PI * 0.5, color, 2, 0.55 - i * 0.12);
      }
    }

    // Acting: angular ticks that light up with real tool events
    if (state === "acting" || state === "blocked" || burst > 0) {
      var ticks = 24;
      for (var k = 0; k < ticks; k++) {
        var a = (k / ticks) * Math.PI * 2 + clock * 0.25;
        var on = burst > 0 ? (k % 3 === 0) : (k % 4 === 0);
        if (!on) continue;
        var r1 = size * 0.335;
        var r2 = size * (0.355 + 0.02 * Math.sin(clock * 3 + k));
        ctx.save();
        ctx.globalAlpha = burst > 0 ? 0.9 : 0.4;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.moveTo(c + Math.cos(a) * r1, c + Math.sin(a) * r1);
        ctx.lineTo(c + Math.cos(a) * r2, c + Math.sin(a) * r2);
        ctx.stroke();
        ctx.restore();
      }
    }

    // Core
    var coreR = size * 0.17 * breathe;
    var core = ctx.createRadialGradient(c - coreR * 0.3, c - coreR * 0.35, coreR * 0.1, c, c, coreR);
    core.addColorStop(0, hexA(color, 0.95));
    core.addColorStop(0.45, hexA(color, 0.35));
    core.addColorStop(1, "rgba(4,7,12,0.05)");
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(c, c, coreR, 0, Math.PI * 2);
    ctx.fill();

    ctx.save();
    ctx.globalAlpha = 0.85;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(c, c, coreR, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();

    // Spokes: a burst decays over ~700ms after a real event
    if (burst > 0.01) {
      for (var s = 0; s < 10; s++) {
        var ang = (s / 10) * Math.PI * 2 + clock * 0.6;
        var near = size * (0.2 + 0.16 * (1 - burst));
        var far = size * (0.24 + 0.2 * (1 - burst));
        ctx.save();
        ctx.globalAlpha = burst * 0.75;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.moveTo(c + Math.cos(ang) * near, c + Math.sin(ang) * near);
        ctx.lineTo(c + Math.cos(ang) * far, c + Math.sin(ang) * far);
        ctx.stroke();
        ctx.restore();
      }
    }

    if (state === "error") {
      ctx.save();
      ctx.globalAlpha = 0.25 + 0.2 * Math.sin(clock * 6);
      ctx.strokeStyle = COLORS.error;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(c, c, size * 0.42, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  function hexA(hex, alpha) {
    var h = String(hex || "").replace("#", "");
    if (h.length !== 6) return "rgba(95,216,255," + alpha + ")";
    var r = parseInt(h.slice(0, 2), 16);
    var g = parseInt(h.slice(2, 4), 16);
    var b = parseInt(h.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  function setState(next, meta) {
    if (!COLORS[next]) next = "idle";
    var m = meta || {};
    var changed = next !== state;
    state = next;
    if (typeof m.progress === "number") progress = m.progress;
    if (m.now && nowEl) nowEl.textContent = m.now;
    if (m.detail && detailEl) detailEl.textContent = m.detail;
    if (m.name && nameEl) nameEl.textContent = m.name;
    if (m.sub && subEl) subEl.textContent = m.sub;
    if (changed) {
      if (stateEl) {
        stateEl.textContent = next;
        stateEl.style.color = COLORS[next];
      }
      burst = 1;
      try {
        window.dispatchEvent(new CustomEvent("hermus:core", { detail: { state: next, meta: m } }));
      } catch (e) { /* older browsers */ }
    }
    // Any state write can alter the canvas (colour, progress, readouts);
    // repaint immediately instead of waiting for the next trickle.
    requestDraw();
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function kpi(value, label) {
    return '<div class="kpi"><span class="v">' + esc(value) + '</span><span class="l">' + esc(label) + "</span></div>";
  }

  function fmtTokens(n) {
    var v = Number(n || 0);
    if (!v) return "--";
    return v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(v);
  }

  function getJSON(url) {
    // A hung probe must not stall the whole core. Abort after 4s and let the
    // caller decide what an unknown value should look like.
    var controller = null;
    var timer = null;
    var options = { headers: { Accept: "application/json" } };
    try {
      if (window.AbortController) {
        controller = new AbortController();
        options.signal = controller.signal;
        timer = window.setTimeout(function () { try { controller.abort(); } catch (e) {} }, 4000);
      }
    } catch (e) { /* older browser */ }

    return fetch(url, options)
      .then(function (r) {
        return r.text().then(function (text) {
          var body = null;
          try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
          return { ok: r.ok, status: r.status, body: body };
        });
      })
      .catch(function () { return { ok: false, status: 0, body: null }; })
      .then(function (result) {
        if (timer) window.clearTimeout(timer);
        return result;
      });
  }

  function pollEvents() {
    // A slow probe must never stack: the previous poll finishes first.
    if (eventsBusy) return Promise.resolve();
    eventsBusy = true;
    return getJSON("/events/recent?limit=24").then(function (res) {
      var rows = (res.ok && res.body && res.body.events) || [];
      if (!rows.length) return;
      var newest = rows[0] || {};
      var id = String(newest.id || newest.ts || "");
      if (!primed) {
        // First poll establishes a baseline; history is not a live event.
        lastEventId = id;
        primed = true;
        lastEventAt = Date.now();
      }
      var isNew = id && id !== lastEventId;
      if (isNew) {
        lastEventId = id;
        var type = String(newest.type || "");
        var data = newest.data || {};
        if (/tool|computer|action/i.test(type)) {
          toolUntil = Date.now() + 6000;
        }
        if (/verif/i.test(type) || /phase/i.test(type) && /verify/i.test(String(data.phase || ""))) {
          verifyUntil = Date.now() + 5000;
        }
        if (/fail|error/i.test(type)) {
          errorUntil = Date.now() + 8000;
        }
        if (detailEl && type) {
          detailEl.textContent = "last event: " + type + (data.goal || data.session_id ? " · " + String(data.goal || data.session_id).slice(0, 120) : "");
        }
        // New event evidence can add an activity arc to the canvas.
        requestDraw();
      }
      lastEventAt = Date.now();
      try {
        window.dispatchEvent(new CustomEvent("hermus:events", { detail: rows }));
      } catch (e) { /* ignore */ }
    }).then(function (value) {
      eventsBusy = false;
      return value;
    }, function () {
      eventsBusy = false;
    });
  }

  function pollCore() {
    if (coreBusy) return Promise.resolve();
    coreBusy = true;
    // Show "gateway is up" as soon as /readyz answers, instead of holding the
    // core offline until the slowest probe in the batch returns.
    fetch("/readyz", { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (r.ok && state === "offline") {
          setState("idle", { now: "Reading gateway state…", progress: 0.1 });
        }
      })
      .catch(function () {});
    return Promise.all([
      fetch("/readyz", { headers: { Accept: "application/json" } }).catch(function () { return { ok: false }; }),
      getJSON("/api/jarvis/status"),
      getJSON("/api/v1/harness/overview"),
      getJSON("/computer/status"),
      getJSON("/remote/approvals"),
      getJSON("/presence")
    ]).then(function (out) {
      var ready = out[0];
      var jarvis = out[1].ok ? out[1].body : null;
      var harness = out[2].ok ? out[2].body : null;
      var computer = out[3].ok ? out[3].body : null;
      var remote = out[4].ok ? out[4].body : null;
      var presence = out[5].ok ? out[5].body : null;

      var counts = (jarvis && jarvis.counts) || {};
      var queue = (jarvis && jarvis.queue) || {};
      var active = Number(counts.active_jobs || 0) + Number(counts.active_runs || 0);
      var agents = Number(counts.agents || 0);
      // /computer/status exposes "active" as "control is armed, not halted".
      // It is NOT a running task, so activity must be derived from real task
      // state, never from that flag.
      var runningTasks = 0;
      if (computer) {
        if (Array.isArray(computer.tasks)) {
          runningTasks = computer.tasks.filter(function (t) { return /running|executing|in_progress/i.test(String((t && t.status) || "")); }).length;
        }
        runningTasks = Math.max(runningTasks, Number((computer.task_stats || {}).running || 0));
        if (computer.current_task) runningTasks = Math.max(runningTasks, 1);
      }
      var pending = 0;
      if (remote) {
        if (Array.isArray(remote.pending)) pending = remote.pending.length;
        else if (remote.status && typeof remote.status === "object") pending = Number(remote.status.pending || 0);
        else if (Array.isArray(remote.approvals)) pending = remote.approvals.length;
      }

      if (presence) {
        var identity = presence.identity || {};
        if (nameEl && identity.name) nameEl.textContent = String(identity.name).toUpperCase();
        if (identity.model) model = identity.model;
        requestDraw();
      }

      if (queueEl) queueEl.textContent = String(active);
      if (agentsEl) agentsEl.textContent = String(agents);
      if (modelEl) modelEl.textContent = model;

      var lastTurn = harness && harness.last_turn;
      var budget = (harness && harness.budget && harness.budget.tokens) || 0;
      var used = lastTurn && lastTurn.tokens_after;
      var overBudget = !!(used && budget && Number(used) > Number(budget));
      if (contextEl) {
        contextEl.textContent = used ? fmtTokens(used) + "/" + fmtTokens(budget) : "--";
        // The numbers must agree with the words: if the last turn ended over
        // the budget the page says so instead of claiming "within budget".
        contextEl.style.color = overBudget ? "var(--warn, #fbbf24)" : "";
        contextEl.title = "context tokens used / per-turn budget"
          + (overBudget ? " — the last turn ended over budget; the harness trims on the next turn" : "");
      }
      if (harnessCells && harness) {
        var s = harness.sessions || {};
        var f = harness.files || {};
        harnessCells.innerHTML = kpi(String(s.active || 0) + " / " + String(s.total || 0), "live sessions")
          + kpi(String(f.pending || 0), "notices")
          + kpi(lastTurn && lastTurn.compacted ? fmtTokens(lastTurn.tokens_before) + "→" + fmtTokens(lastTurn.tokens_after) : "none", "compaction")
          + kpi((lastTurn && lastTurn.recall_mode) || "—", "recall mode");
      }
      if (harnessTurn && lastTurn) {
        harnessTurn.textContent = "Last turn: " + (lastTurn.compacted ? "compacted" : (overBudget ? "over budget (trimmed next turn)" : "within budget"))
          + " · " + String(lastTurn.recall_count || 0) + " recall hit(s) in " + String(lastTurn.recall_ms || 0) + "ms"
          + " · " + String(lastTurn.notices || 0) + " notice(s).";
      }

      var now = Date.now();
      // Single-writer rule: while a user command is live (nexus owns the hero
      // copy) or just answered, the queue-derived lines must not fight it.
      var nx = (window.HermusNexus && window.HermusNexus.state) || null;
      var nexusOwns = !!(nx && (nx.busy || nx.missionLive));
      var nexusAnswered = !!(nx && nx.answeredAt && (now - nx.answeredAt) < 12000);
      if (!ready || !ready.ok) {
        setState("offline", {
          now: "Gateway unreachable. Nothing is running.",
          detail: "The core mirrors /readyz. When the gateway is down it dims and says so; it never animates a fiction.",
          progress: 0
        });
        return;
      }
      if (nexusOwns) {
        return;
      }
      if (nexusAnswered) {
        setState("idle", { now: "Answered — the response is in the conversation under the command bar.", progress: 0 });
      } else if (now < errorUntil) {
        setState("error", { now: "A run failed. Replay it from Telemetry.", progress: 0.02 });
      } else if (pending > 0) {
        setState("blocked", { now: pending + " action(s) awaiting your approval.", detail: "Pending actions are listed on the Remote tab. Nothing yellow proceeds without a grant.", progress: 0.5 });
      } else if (now < verifyUntil) {
        setState("verifying", { now: "Verifying the last action against evidence.", progress: 0.75 });
      } else if (now < toolUntil || runningTasks > 0) {
        setState("acting", { now: "Executing through the canonical tool path.", progress: 0.55 });
      } else if (active > 0) {
        setState("thinking", { now: active + " active job(s) on the queue.", progress: 0.25 });
      } else {
        setState("idle", { now: "Idle. The queue is clear.", detail: "Every value on this page is a live probe, a persisted record or an event-log replay.", progress: 0 });
      }
    }).then(function (value) {
      coreBusy = false;
      return value;
    }, function () {
      coreBusy = false;
      setState("offline", {
        now: "Gateway unreachable. Nothing is running.",
        detail: "The core mirrors /readyz. When the gateway is down it dims and says so; it never animates a fiction.",
        progress: 0
      });
    });
  }

  var raf = 0;
  var calmTimer = 0;

  // Calm and reduced-motion pages must not redraw a still image sixty times
  // a second: that pegs software renderers and burns battery. While motion
  // is allowed the loop runs on every animation frame; otherwise it trickles
  // one frame per second (so transient activity arcs still expire on time)
  // and state changes repaint immediately through requestDraw().
  function motionAllowed() {
    return !calm() && !reduced();
  }

  function stopFrame() {
    if (raf) window.cancelAnimationFrame(raf);
    if (calmTimer) window.clearTimeout(calmTimer);
    raf = 0;
    calmTimer = 0;
  }

  function frame(t) {
    raf = 0;
    if (burst > 0) burst = Math.max(0, burst - 0.016);
    draw(t);
    if (motionAllowed()) {
      raf = window.requestAnimationFrame(frame);
    } else if (!calmTimer) {
      calmTimer = window.setTimeout(function () {
        calmTimer = 0;
        if (!raf) raf = window.requestAnimationFrame(frame);
      }, 1000);
    }
  }

  function requestDraw() {
    if (raf) return; // a frame is already queued
    if (calmTimer) {
      window.clearTimeout(calmTimer);
      calmTimer = 0;
    }
    raf = window.requestAnimationFrame(frame);
  }

  function start() {
    resize();
    requestDraw();
    pollCore();
    pollEvents();
    window.setInterval(pollCore, 5000);
    window.setInterval(pollEvents, 2500);
  }

  window.addEventListener("resize", function () {
    resize();
    requestDraw();
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      stopFrame();
    } else {
      requestDraw();
    }
  });
  window.addEventListener("pagehide", stopFrame);

  window.HermusCore = {
    setState: setState,
    state: function () { return state; },
    burst: function () { burst = 1; requestDraw(); },
    // Test hook: lets the Node harness drive one poll deterministically.
    pollOnce: pollCore
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
