/**
 * JARVIS HUD — futuristic readouts wired to REAL gateway data.
 *
 * Honesty contract (see footer of /control): the UI never owns truth and
 * never simulates success. Every number below comes from /api/jarvis/status
 * (a factual aggregate: resource monitor, job queue, fleet registry, run
 * bus). When a probe is missing or fails, the gauge renders an explicit
 * "n/a" state — it never invents a value.
 *
 * This file is additive: it renders into #jarvisHud and polls only its own
 * endpoint, so it coexists with control-room.js's existing refresh loops.
 */
(function () {
  "use strict";

  var RING_C = 2 * Math.PI * 40; // circumference for r=40 rings

  function $(sel) { return document.querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmtBytes(n) {
    if (n == null) return "n/a";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var v = n;
    var i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return v.toFixed(v >= 10 || i === 0 ? 0 : 1) + " " + units[i];
  }
  function fmtUptime(sec) {
    if (sec == null) return "n/a";
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    return (h ? h + "h " : "") + (h || m ? m + "m " : "") + s + "s";
  }

  function gauge(id, label) {
    return '<div class="hud-gauge" id="' + id + '-wrap">'
      + '<svg viewBox="0 0 100 100" aria-hidden="true">'
      + '<circle class="ring-bg" cx="50" cy="50" r="40"></circle>'
      + '<circle class="ring-val" id="' + id + '" cx="50" cy="50" r="40" '
      + 'stroke-dasharray="' + RING_C.toFixed(1) + '" stroke-dashoffset="' + RING_C.toFixed(1) + '"></circle>'
      + '<text x="50" y="47" text-anchor="middle" font-size="15" fill="currentColor" '
      + 'font-family="monospace" id="' + id + '-txt">--</text>'
      + '<text x="50" y="60" text-anchor="middle" font-size="7" fill="currentColor" opacity="0.6" '
      + 'font-family="monospace" id="' + id + '-unit"></text>'
      + "</svg>"
      + '<span class="gauge-label">' + esc(label) + "</span>"
      + "</div>";
  }

  function buildFrame() {
    var host = $("#jarvisHud");
    if (!host) return false;
    host.innerHTML = ""
      + '<span class="hud-corner-tl"></span><span class="hud-corner-br"></span>'
      + '<span class="hud-sweep"></span>'
      + '<div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;">'
      + '<h2 style="margin:0;font-size:14px;letter-spacing:0.18em;text-transform:uppercase;">&#9670; JARVIS HUD</h2>'
      + '<span class="pill" id="hudState">syncing</span>'
      + "</div>"
      + '<div class="hud-gauges">'
      + gauge("hudCpu", "cpu (sys)")
      + gauge("hudRam", "ram (sys)")
      + gauge("hudDisk", "disk")
      + "</div>"
      + '<div class="hud-kv" style="margin-top:12px;">'
      + '<span class="k">uptime</span><span class="v" id="hudUptime">--</span>'
      + '<span class="k">queue</span><span class="v" id="hudQueue">--</span>'
      + '<span class="k">agents</span><span class="v" id="hudAgents">--</span>'
      + '<span class="k">runs</span><span class="v" id="hudRuns">--</span>'
      + '<span class="k">tools</span><span class="v" id="hudTools">--</span>'
      + '<span class="k">providers</span><span class="v" id="hudProviders">--</span>'
      + "</div>"
      + '<div class="hud-bars">'
      + '<div class="hud-bar"><div class="bar-head"><span>jobs running</span><span id="hudJobsTxt">0</span></div>'
      + '<div class="bar-track"><div class="bar-fill" id="hudJobsBar"></div></div></div>'
      + "</div>"
      + '<div class="hud-voice" id="hudVoice">waiting for fleet events&#8230;</div>'
      + '<div class="hud-ticker" id="hudTicker" aria-hidden="true"></div>';
    return true;
  }

  function setRing(id, percent, unit) {
    var ring = $("#" + id);
    var txt = $("#" + id + "-txt");
    var unitEl = $("#" + id + "-unit");
    if (!ring || !txt) return;
    if (percent == null || isNaN(percent)) {
      ring.style.strokeDashoffset = String(RING_C);
      ring.classList.remove("warn", "crit");
      txt.textContent = "n/a";
      if (unitEl) unitEl.textContent = "";
      return;
    }
    var p = Math.max(0, Math.min(100, percent));
    ring.style.strokeDashoffset = String(RING_C * (1 - p / 100));
    ring.classList.toggle("warn", p > 60 && p <= 85);
    ring.classList.toggle("crit", p > 85);
    txt.textContent = String(Math.round(p));
    if (unitEl) unitEl.textContent = unit || "%";
  }

  function setBar(id, done, total, txtId) {
    var fill = $("#" + id);
    var txt = txtId ? $("#" + txtId) : null;
    if (!fill) return;
    var pct = total > 0 ? (done / Math.max(1, total)) * 100 : 0;
    fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    fill.classList.toggle("warn", pct > 60 && pct <= 85);
    fill.classList.toggle("crit", pct > 85);
    if (txt) txt.textContent = String(done);
  }

  function stateClass(p) {
    if (p == null) return "";
    if (p > 85) return "err";
    if (p > 60) return "warn";
    return "ok";
  }

  var lastTickerTs = 0;
  function tick(events) {
    var host = $("#hudTicker");
    if (!host || !events || !events.length) return;
    // Newest last per the API; show the latest 5, newest first.
    var rows = events.slice(-5).reverse().map(function (e) {
      var status = String(e.status || e.state || "");
      var cls = /done|ok|complete/i.test(status) ? "st-ok"
        : /fail|error|blocked|cancel/i.test(status) ? "st-err" : "st-warn";
      return '<div class="tick"><span class="ts">' + esc(e.ts || e.timestamp || "") + "</span>"
        + '<span class="st-' + (cls === "st-ok" ? "ok" : cls === "st-err" ? "err" : "warn") + '">&#9679;</span> '
        + esc(e.kind || e.type || "event") + " " + esc(status) + "</div>";
    });
    host.innerHTML = rows.join("");
  }

  function render(d) {
    var tel = d.telemetry || {};
    setRing("hudCpu", typeof tel.system_cpu_percent === "number" ? tel.system_cpu_percent : null, "%");
    setRing("hudRam", tel.system_memory ? tel.system_memory.used_percent : null, "%");
    setRing("hudDisk", tel.disk ? tel.disk.used_percent : null, "%");

    var q = d.queue || {};
    var counts = d.counts || {};
    var qTxt = $("#" + "hudQueue");
    if (qTxt) {
      var running = (q.by_status || {}).running || 0;
      var pending = (q.by_status || {}).queued || 0;
      qTxt.textContent = running + " running / " + pending + " queued";
      qTxt.className = "v " + stateClass(pending > 10 ? 70 : pending > 3 ? 65 : null);
    }

    var el = $("#hudUptime");
    if (el) el.textContent = fmtUptime((d.gateway || {}).uptime_seconds);
    el = $("#hudAgents");
    if (el) el.textContent = counts.agents != null ? String(counts.agents) : "n/a";
    el = $("#hudRuns");
    if (el) el.textContent = counts.active_runs != null ? String(counts.active_runs) + " active" : "n/a";
    el = $("#hudTools");
    if (el) el.textContent = counts.tools != null ? String(counts.tools) : "n/a";
    el = $("#hudProviders");
    if (el) el.textContent = Array.isArray(d.providers) ? String(d.providers.length) : "n/a";

    setBar("hudJobsBar", counts.active_jobs || 0, 20, "hudJobsTxt");

    var hudState = $("#hudState");
    if (hudState) {
      hudState.textContent = "live";
      hudState.className = "pill ok";
    }
  }

  function markOffline(message) {
    var hudState = $("#hudState");
    if (hudState) {
      hudState.textContent = message || "offline";
      hudState.className = "pill err";
    }
    setRing("hudCpu", null, "%");
    setRing("hudRam", null, "%");
    setRing("hudDisk", null, "%");
  }

  function say(text) {
    var el = $("#hudVoice");
    if (!el) return;
    el.textContent = text;
    try {
      if (window.speechSynthesis && window.localStorage.getItem("jarvis_voice_enabled") === "1") {
        var u = new SpeechSynthesisUtterance(text);
        window.speechSynthesis.speak(u);
      }
    } catch (e) { /* speech is optional; the line is the source of truth */ }
  }

  var pollTimer = null;
  function poll() {
    fetch("/api/jarvis/status", { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      })
      .then(function (d) {
        render(d);
        var runs = (d.runs || []).filter(function (r) { return r.ts || r.timestamp; });
        tick(runs);
      })
      .catch(function () {
        markOffline("offline");
        say("Gateway connection lost.");
      });
  }

  // Voice line reacts to the fleet WS the control room already opens, so it
  // announces real state changes without a second connection.
  document.addEventListener("hermes:fleet-event", function (ev) {
    var m = ev.detail || {};
    if (m.type === "fleet.state_changed") {
      var st = m.new_state || m.state;
      if (st) { say((m.agent_name || "Agent") + " is now " + st); }
    } else if (m.type === "fleet.mission_opened") {
      say("Mission opened" + (m.mission_id ? ": " + m.mission_id : ""));
    } else if (m.type === "fleet.mission_terminated") {
      say("Mission terminated" + (m.mission_id ? ": " + m.mission_id : ""));
    }
  });

  function init() {
    if (!$("#jarvisHud")) {
      // Panel not present (e.g. other surfaces reuse this file): no-op.
      return;
    }
    buildFrame();
    poll();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(poll, 5000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Expose for tests/console without leaking into global scope beyond one name.
  window.jarvisHud = { poll: poll, say: say, buildFrame: buildFrame };
})();
