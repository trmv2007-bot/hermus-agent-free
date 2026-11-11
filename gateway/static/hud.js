/* HERMUS — FAIRY CORE runtime. Cosmetic layer only, zero backend calls.
 *
 * Four jobs, all additive — nexus.js still owns every real interaction:
 *   1. Boot sequence   — a short, honest "systems online" ceremony.
 *   2. Fairy avatar    — a small companion whose moods mirror the REAL core
 *                        state (hermus:core events + HermesNexus.state),
 *                        with deadpan quips on transitions.
 *   3. Transcript skin — the assistant's rows speak as "FAIRY".
 *   4. Event ticker    — mirrors real feed rows along the bottom edge.
 *
 * Everything degrades: reduced-motion collapses the boot to a blink, the
 * ticker stops scrolling, the avatar stops fluttering. No network, no truth.
 */
(function () {
  "use strict";

  function $(sel) { return document.querySelector(sel); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function reduced() {
    try { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
    catch (e) { return false; }
  }
  function later(fn, ms) { return window.setTimeout(fn, ms); }

  /* ------------------------------------------------------------------ *
   * 1. Boot sequence.
   * Full ceremony on the first load of a session; a quick blink after
   * that (same-tab navigations shouldn't pay the toll twice). */
  var BOOT_KEY = "hermus_fairy_booted";

  function boot() {
    if (reduced()) { markBooted(); return; }
    var quick = false;
    try { quick = sessionStorage.getItem(BOOT_KEY) === "1"; } catch (e) { /* private mode */ }

    var wrap = el("div", "hud-boot");
    wrap.setAttribute("role", "status");
    wrap.appendChild(el("div", "boot-mark", "HERMUS"));
    wrap.appendChild(el("div", "boot-sub", "fairy core // nexus runtime"));
    var lines = el("div", "boot-lines");
    var bar = el("i");
    var barHost = el("div", "boot-bar");
    barHost.appendChild(bar);
    wrap.appendChild(lines);
    wrap.appendChild(barHost);
    wrap.appendChild(el("div", "boot-skip", "press any key to skip"));
    document.body.appendChild(wrap);

    var SCRIPT = [
      ["nexus core", "ONLINE"],
      ["model fleet", "LINKED"],
      ["safety interlocks", "ARMED"],
      ["fairy consciousness", "AWAKE"],
      ["all systems", "NOMINAL"]
    ];
    var timers = [];
    var finished = false;

    function finish() {
      if (finished) return;
      finished = true;
      timers.forEach(window.clearTimeout);
      wrap.classList.add("done");
      later(function () { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); }, 550);
      markBooted();
      quip("boot");
    }
    function markBooted() {
      try { sessionStorage.setItem(BOOT_KEY, "1"); } catch (e) { /* ignore */ }
    }

    if (quick) {
      lines.innerHTML = "<b>CORE ONLINE</b>";
      bar.style.width = "100%";
      timers.push(later(finish, 650));
    } else {
      var t = 240;
      SCRIPT.forEach(function (pair) {
        timers.push(later(function () {
          var row = el("div");
          row.appendChild(document.createTextNode("> " + pair[0] + " "));
          var pad = el("span");
          pad.textContent = new Array(Math.max(2, 22 - pair[0].length)).join(".");
          pad.style.color = "rgba(200,255,77,0.35)";
          row.appendChild(pad);
          row.appendChild(el("b", null, pair[1]));
          lines.appendChild(row);
          bar.style.width = Math.round(((SCRIPT.indexOf(pair) + 1) / SCRIPT.length) * 100) + "%";
        }, t));
        t += 330;
      });
      timers.push(later(finish, t + 420));
    }

    wrap.addEventListener("click", finish);
    window.addEventListener("keydown", function onKey(e) {
      window.removeEventListener("keydown", onKey);
      finish();
    });
  }

  /* ------------------------------------------------------------------ *
   * 2. Fairy avatar — injected into #fairyStage (control.html). */
  var FAIRY_SVG =
    '<svg class="fairy" viewBox="0 0 120 120" aria-hidden="true">' +
    '<defs>' +
    '<radialGradient id="hudOrb" cx="42%" cy="38%" r="70%">' +
    '<stop offset="0%" stop-color="#ffffff"/>' +
    '<stop offset="45%" stop-color="#ff9adf"/>' +
    '<stop offset="100%" stop-color="#b44dff"/>' +
    '</radialGradient>' +
    '<linearGradient id="hudWingL" x1="0%" y1="0%" x2="100%" y2="100%">' +
    '<stop offset="0%" stop-color="rgba(65,232,255,0.5)"/>' +
    '<stop offset="100%" stop-color="rgba(255,122,217,0.06)"/>' +
    '</linearGradient>' +
    '<linearGradient id="hudWingR" x1="100%" y1="0%" x2="0%" y2="100%">' +
    '<stop offset="0%" stop-color="rgba(65,232,255,0.5)"/>' +
    '<stop offset="100%" stop-color="rgba(255,122,217,0.06)"/>' +
    '</linearGradient>' +
    '</defs>' +
    '<g class="wings">' +
    '<path class="wing w-l" fill="url(#hudWingL)" stroke="rgba(65,232,255,0.45)" stroke-width="0.8" ' +
    'd="M52 56 C 40 30, 16 26, 10 42 C 6 56, 22 62, 40 60 C 30 68, 34 76, 44 74 C 50 72, 52 64, 52 56 Z"/>' +
    '<path class="wing w-r" fill="url(#hudWingR)" stroke="rgba(65,232,255,0.45)" stroke-width="0.8" ' +
    'd="M68 56 C 80 30, 104 26, 110 42 C 114 56, 98 62, 80 60 C 90 68, 86 76, 76 74 C 70 72, 68 64, 68 56 Z"/>' +
    '</g>' +
    '<g class="orb">' +
    '<circle class="orb-halo" cx="60" cy="60" r="30" fill="none" stroke="rgba(65,232,255,0.35)" stroke-width="1"/>' +
    '<circle class="orb-ring" cx="60" cy="60" r="23" fill="none" stroke="rgba(255,122,217,0.6)" stroke-width="1" stroke-dasharray="10 14"/>' +
    '<circle class="orb-core" cx="60" cy="60" r="15" fill="url(#hudOrb)"/>' +
    '<circle cx="54" cy="54" r="4.4" fill="rgba(255,255,255,0.9)"/>' +
    '</g>' +
    '<g class="sparks">' +
    '<circle class="spark" cx="34" cy="34" r="1.4" fill="rgba(65,232,255,0.8)"/>' +
    '<circle class="spark" cx="88" cy="40" r="1.2" fill="rgba(255,122,217,0.8)"/>' +
    '<circle class="spark" cx="30" cy="72" r="1" fill="rgba(255,122,217,0.6)"/>' +
    '<circle class="spark" cx="92" cy="70" r="1.4" fill="rgba(65,232,255,0.7)"/>' +
    '</g>' +
    '</svg>';

  var QUIPS = {
    boot: "Fairy online. I checked the systems twice — you're welcome.",
    busy: "Working. Try not to touch anything.",
    done: "Done. It was trivial, really.",
    error: "That failed. I'd fix it myself, but you hold the permissions.",
    blocked: "An action needs your signature. I don't do paperwork.",
    offline: "Gateway's gone dark. Even I can't charm a dead socket.",
    idle: null
  };
  var lastQuipAt = 0;

  function quip(kind) {
    var line = QUIPS[kind];
    var host = $(".fairy-quip");
    if (!line || !host) return;
    var now = Date.now();
    if (now - lastQuipAt < 6000) return;
    lastQuipAt = now;
    host.textContent = line;
  }

  var mood = "idle";
  function setMood(next) {
    if (next === mood) return;
    mood = next;
    var stage = $("#fairyStage");
    if (stage) stage.setAttribute("data-mood", next);
    if (QUIPS[next]) quip(next);
  }

  function fairyStage() {
    var host = $("#fairyStage");
    if (!host) return;
    host.innerHTML = FAIRY_SVG;
    var caption = el("div", "fairy-quip", "");
    caption.setAttribute("aria-live", "polite");
    host.appendChild(caption);
    host.setAttribute("data-mood", "idle");

    // Real moods: the core canvas narrates its own state changes.
    window.addEventListener("hermus:core", function (e) {
      var s = String((e.detail && e.detail.state) || "idle");
      if (s === "offline") setMood("offline");
      else if (s === "error") setMood("error");
      else if (s === "blocked") setMood("blocked");
      else if (s === "acting" || s === "thinking" || s === "verifying") setMood("busy");
      else setMood("idle");
    });
    // Nexus owns run liveness; it outranks the poller while a run is live.
    window.setInterval(function () {
      var nx = window.HermusNexus && window.HermusNexus.state;
      if (nx && (nx.busy || nx.missionLive)) setMood("busy");
      else if (mood === "busy") setMood("idle");
    }, 1200);
  }

  /* ------------------------------------------------------------------ *
   * 3. Transcript skin — the assistant speaks as FAIRY.
   * nexus.js writes the rows (and owns persistence); we only re-voice
   * the speaker label on the way past. The greeting is a transient
   * bubble: it is never persisted to localStorage. */
  function skinRow(row) {
    if (!row || row.dataset.fairySkinned) return;
    row.dataset.fairySkinned = "1";
    if (row.classList.contains("hermus")) {
      var who = row.querySelector(".msg-who");
      if (who) who.textContent = "FAIRY";
    }
  }

  function greet() {
    var body = $("#transcriptBody");
    if (!body) return;
    if (body.querySelector(".msg")) return; // history exists — stay quiet
    var row = el("div", "msg hermus");
    row.dataset.fairySkinned = "1";
    row.innerHTML =
      '<div class="msg-who">FAIRY</div>' +
      '<div class="msg-text">Fairy online. The core is live and the queue is clear — I checked twice, you\'re welcome. Tell me what you need.</div>';
    body.innerHTML = "";
    body.appendChild(row);
  }

  function watchTranscript() {
    var body = $("#transcriptBody");
    if (!body || !window.MutationObserver) return;
    [].forEach.call(body.querySelectorAll(".msg"), skinRow);
    var obs = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        [].forEach.call(m.addedNodes, function (node) {
          if (node.nodeType !== 1 || !node.classList) return;
          skinRow(node);
          if (node.classList.contains("hermus")) {
            quip(node.classList.contains("error") ? "error" : "done");
          }
        });
      });
    });
    obs.observe(body, { childList: true });
  }

  /* ------------------------------------------------------------------ *
   * 4. Event ticker — mirrors real feed rows, deduped, at the bottom. */
  var tickerItems = [];
  var tickerSeen = {};

  function tickerHost() {
    var host = $("#hudTicker");
    if (host) return host;
    host = el("div", "ticker");
    host.id = "hudTicker";
    host.setAttribute("aria-hidden", "true");
    var tag = el("div", "ticker-tag", "◤ HERMUS");
    var track = el("div", "ticker-track");
    host.appendChild(tag);
    host.appendChild(track);
    document.body.appendChild(host);
    document.body.classList.add("has-ticker");
    return host;
  }

  function tickerRender() {
    var host = tickerHost();
    var track = host.querySelector(".ticker-track");
    if (!track) return;
    track.innerHTML = "";
    if (!tickerItems.length) { host.hidden = true; return; }
    host.hidden = false;
    var run = el("div", "ticker-run");
    // Two passes = a seamless marquee loop; content is tiny text.
    for (var pass = 0; pass < 2; pass++) {
      tickerItems.forEach(function (item) {
        var span = el("span", item.kind ? "k-" + item.kind : null, item.text);
        run.appendChild(span);
      });
    }
    track.appendChild(run);
    var width = run.scrollWidth || 600;
    var seconds = Math.max(18, Math.min(90, width / 55));
    run.style.animationDuration = seconds + "s";
  }

  function tickerPush(text, kind) {
    text = String(text || "").replace(/\s+/g, " ").trim();
    if (!text || text.length > 140) return;
    var key = text.toLowerCase();
    var now = Date.now();
    if (tickerSeen[key] && now - tickerSeen[key] < 120000) return; // 2min dedupe
    tickerSeen[key] = now;
    tickerItems.push({ text: text, kind: kind || "" });
    while (tickerItems.length > 9) tickerItems.shift();
    tickerRender();
  }

  function watchFeeds() {
    if (!window.MutationObserver) return;
    function observe(sel, extract) {
      var host = $(sel);
      if (!host) return;
      var obs = new MutationObserver(function () {
        [].forEach.call(host.children, extract);
      });
      obs.observe(host, { childList: true, subtree: true });
      [].forEach.call(host.children, extract);
    }
    // Activity rows carry data-msg + data-kind.
    observe("#activity", function (row) {
      if (row && row.dataset && row.dataset.msg) tickerPush(row.dataset.msg, row.dataset.kind);
    });
    // Telemetry rows are plain feed items; keep only their first line and
    // skip placeholders ("connecting to the event stream…") — a loading
    // note is not an event and must not parade as one.
    observe("#tmFeed", function (row) {
      if (!row || !row.textContent || !row.classList) return;
      if (row.classList.contains("note") || row.classList.contains("empty-event") || row.classList.contains("skeleton")) return;
      var line = row.textContent.trim().split("\n")[0].slice(0, 120);
      if (line) tickerPush(line, "");
    });
  }

  /* ------------------------------------------------------------------ */
  function start() {
    fairyStage();
    greet();
    watchTranscript();
    watchFeeds();
    boot();
  }

  window.HermusHUD = {
    version: "1.0",
    quip: quip,
    setMood: setMood,
    tickerPush: tickerPush,
    boot: boot,
    __test: { tickerItems: function () { return tickerItems.slice(); }, mood: function () { return mood; } }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
