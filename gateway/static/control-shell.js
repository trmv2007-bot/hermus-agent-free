/* HERMUS flight-deck shell: rail, calm mode, command palette, activity drawer
 * and the directive composer.
 *
 * Everything here is navigation or an existing canonical endpoint. The palette
 * cannot invent a command: each entry either selects a tab, refreshes the
 * current panel through control-room.js, or posts to /command or
 * /computer/run exactly as the rest of the page does.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var rail = document.getElementById("rail");
  var pinBtn = document.getElementById("pinRail");
  var calmBtn = document.getElementById("calmBtn");
  var drawerBtn = document.getElementById("drawerBtn");
  var drawer = document.getElementById("activityDrawer");
  var drawerClose = document.getElementById("drawerClose");
  var feed = document.getElementById("activityFeed");
  var backdrop = document.getElementById("paletteBackdrop");
  var input = document.getElementById("paletteInput");
  var list = document.getElementById("paletteList");
  var openBtn = document.getElementById("paletteBtn");
  var ta = document.getElementById("directive");
  var sendBtn = document.getElementById("directiveSend");
  var stopBtn = document.getElementById("directiveStop");
  var modeSel = document.getElementById("directiveMode");
  var out = document.getElementById("directiveOut");
  var advancedBtn = document.getElementById("advancedBtn");
  var railScrim = document.getElementById("railScrim");
  var detailsBtn = document.getElementById("detailsBtn");
  var explainBtn = document.getElementById("explainBtn");
  var autonomyBtn = document.getElementById("autonomyBtn");
  var runState = document.getElementById("runState");
  var runStateText = document.getElementById("runStateText");
  var consoleFeed = document.getElementById("consoleEvents");

  var items = [];
  var filtered = [];
  var active = 0;

  function store(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) { /* private mode */ }
  }

  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  function note(message, kind) {
    if (window.toast) window.toast(message, kind || "info");
  }

  /* ---- advanced sheet --------------------------------------------------- */
  /* The eleven tabs are not part of the default view. Advanced opens them as
     a sheet so the room opens on one state, one input and one answer. */
  function applyAdvanced(open) {
    root.classList.toggle("advanced", !!open);
    if (advancedBtn) advancedBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var first = rail && rail.querySelector("[role=tab]");
      if (first && first.focus) first.focus({ preventScroll: true });
    } else if (advancedBtn && document.activeElement && rail && rail.contains(document.activeElement)) {
      advancedBtn.focus({ preventScroll: true });
    }
  }

  if (advancedBtn) {
    advancedBtn.addEventListener("click", function () { applyAdvanced(!root.classList.contains("advanced")); });
  }
  if (railScrim) railScrim.addEventListener("click", function () { applyAdvanced(false); });
  if (pinBtn) pinBtn.addEventListener("click", function () { applyAdvanced(false); });
  if (rail) {
    // Choosing a section closes the sheet; control-room.js still owns the
    // selection itself through its own listener.
    rail.addEventListener("click", function (evt) {
      var btn = evt.target && evt.target.closest ? evt.target.closest("[role=tab]") : null;
      if (btn) applyAdvanced(false);
    });
  }

  /* ---- optional depth --------------------------------------------------- */
  function applyDetails(on) {
    root.classList.toggle("overview-details", !!on);
    if (detailsBtn) detailsBtn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  function applyExplain(on) {
    root.classList.toggle("explain", !!on);
    if (explainBtn) explainBtn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  if (detailsBtn) {
    detailsBtn.addEventListener("click", function () {
      var on = !root.classList.contains("overview-details");
      applyDetails(on);
      store("hermus_details", on ? "1" : "0");
    });
  }
  if (explainBtn) {
    explainBtn.addEventListener("click", function () {
      var on = !root.classList.contains("explain");
      applyExplain(on);
      store("hermus_explain", on ? "1" : "0");
    });
  }

  /* ---- calm ------------------------------------------------------------- */
  function applyCalm(on) {
    root.classList.toggle("calm", !!on);
    if (calmBtn) calmBtn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  if (calmBtn) {
    calmBtn.addEventListener("click", function () {
      var on = !root.classList.contains("calm");
      applyCalm(on);
      store("hermus_calm", on ? "1" : null);
    });
  }

  /* ---- activity drawer -------------------------------------------------- */
  function applyDrawer(open) {
    if (!drawer) return;
    drawer.classList.toggle("on", !!open);
    if (drawerBtn) drawerBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var close = drawer.querySelector(".drawer-head button");
      if (close && close.focus) close.focus({ preventScroll: true });
    }
  }

  if (drawerBtn) drawerBtn.addEventListener("click", function () { applyDrawer(!drawer.classList.contains("on")); });
  if (drawerClose) drawerClose.addEventListener("click", function () { applyDrawer(false); });

  function renderEvents(rows) {
    if (!feed || !rows || !rows.length) return;
    feed.textContent = "";
    rows.slice(0, 40).forEach(function (event) {
      var row = document.createElement("div");
      row.className = "row";
      var type = document.createElement("span");
      type.className = "status";
      type.textContent = String(event.type || "event");
      var body = document.createElement("span");
      var data = event.data || {};
      var detail = data.goal || data.session_id || data.tool || data.message || "";
      body.textContent = detail ? String(detail).slice(0, 140) : String(event.ts || "");
      row.appendChild(type);
      row.appendChild(body);
      feed.appendChild(row);
    });
  }

  window.addEventListener("hermus:events", function (evt) {
    renderEvents((evt && evt.detail) || []);
    renderConsoleEvents((evt && evt.detail) || []);
  });

  /* ---- command palette -------------------------------------------------- */
  function collectTabs() {
    var seen = {};
    Array.prototype.forEach.call(document.querySelectorAll("nav [role=tab]"), function (btn) {
      var name = btn.getAttribute("data-tab");
      if (!name || seen[name]) return;
      seen[name] = true;
      var label = (btn.textContent || name).trim();
      items.push({ label: label, kind: "section", run: function () { if (window.selectTab) window.selectTab(name); } });
    });
  }

  function collectActions() {
    items.push({
      label: "Refresh the current panel",
      kind: "action",
      run: function () {
        var on = document.querySelector("nav [role=tab].on");
        var name = on && on.getAttribute("data-tab");
        if (name && window.refreshTab) window.refreshTab(name);
        else note("Nothing to refresh", "warn");
      }
    });
    items.push({ label: "Open live activity", kind: "view", run: function () { applyDrawer(true); } });
    items.push({
      label: "Toggle calm mode",
      kind: "view",
      run: function () {
        var on = !root.classList.contains("calm");
        applyCalm(on);
        store("hermus_calm", on ? "1" : null);
      }
    });
    items.push({
      label: "Replay a run timeline",
      kind: "nav",
      run: function () {
        if (window.selectTab) window.selectTab("overview");
        var field = document.getElementById("runId");
        if (field) field.focus();
      }
    });
    items.push({
      label: "Open the Systems console",
      kind: "nav",
      run: function () { if (window.selectTab) window.selectTab("systems"); }
    });
  }

  function paint() {
    if (!list) return;
    list.textContent = "";
    if (!filtered.length) {
      var empty = document.createElement("div");
      empty.className = "note";
      empty.style.padding = "10px";
      empty.textContent = "No matching command.";
      list.appendChild(empty);
      return;
    }
    filtered.forEach(function (item, index) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "palette-item";
      btn.id = "palette-opt-" + index;
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", index === active ? "true" : "false");
      var label = document.createElement("span");
      label.textContent = item.label;
      var kind = document.createElement("span");
      kind.className = "kind";
      kind.textContent = item.kind;
      btn.appendChild(label);
      btn.appendChild(kind);
      btn.addEventListener("mousemove", function () { active = index; markActive(); });
      btn.addEventListener("click", function () { runActive(index); });
      list.appendChild(btn);
    });
    markActive();
  }

  function markActive() {
    if (!list) return;
    Array.prototype.forEach.call(list.querySelectorAll("[role=option]"), function (node, index) {
      node.setAttribute("aria-selected", index === active ? "true" : "false");
    });
    if (input) input.setAttribute("aria-activedescendant", "palette-opt-" + active);
    var current = document.getElementById("palette-opt-" + active);
    if (current && current.scrollIntoView) current.scrollIntoView({ block: "nearest" });
  }

  function filter() {
    var q = (input && input.value ? input.value : "").trim().toLowerCase();
    filtered = q
      ? items.filter(function (item) { return item.label.toLowerCase().indexOf(q) !== -1 || item.kind.indexOf(q) !== -1; })
      : items.slice();
    active = 0;
    paint();
  }

  function openPalette() {
    if (!backdrop) return;
    backdrop.classList.add("on");
    if (input) {
      input.value = "";
      input.focus();
    }
    filter();
  }

  function closePalette() {
    if (backdrop) backdrop.classList.remove("on");
  }

  function runActive(index) {
    var item = filtered[index];
    closePalette();
    if (item && item.run) item.run();
  }

  if (openBtn) openBtn.addEventListener("click", openPalette);
  if (input) {
    input.addEventListener("input", filter);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, filtered.length - 1); markActive(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); markActive(); }
      else if (e.key === "Enter") { e.preventDefault(); runActive(active); }
      else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    });
  }
  if (backdrop) {
    backdrop.addEventListener("mousedown", function (e) { if (e.target === backdrop) closePalette(); });
  }

  /* ---- directive composer ---------------------------------------------- */
  /* One switch decides how a directive is run:
       on  → the runtime classifies it and a goal becomes a mission that plans,
             executes, verifies and repairs itself. Approvals still apply and
             red lines still block; nothing is auto-granted here.
       off → one reply, no mission (`prefer: chat`).
     Autonomy work is submitted to the canonical queue so the page stays
     usable and the work survives a closed tab; progress is polled from the
     job the gateway returns. */
  var currentJob = null;
  var pollTimer = null;

  function fetchJSON(url, body, method) {
    return fetch(url, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body || {})
    }).then(function (r) {
      return r.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        return { ok: r.ok, status: r.status, data: data || {} };
      });
    });
  }

  function setRun(state, text) {
    if (!runState) return;
    runState.className = "run-state" + (state ? " " + state : "");
    if (runStateText) runStateText.textContent = text || "ready";
  }

  function renderConsoleEvents(rows) {
    if (!consoleFeed) return;
    consoleFeed.textContent = "";
    (rows || []).slice(0, 5).forEach(function (event) {
      var row = document.createElement("div");
      row.className = "row";
      var when = document.createElement("span");
      when.className = "t";
      when.textContent = String(event.ts || "").slice(11, 19) || "--:--";
      var kind = document.createElement("span");
      kind.className = "e";
      kind.textContent = String(event.type || "event");
      var detail = document.createElement("span");
      detail.className = "d";
      detail.textContent = String(event.goal || event.tool || event.message || event.session_id || "");
      row.appendChild(when);
      row.appendChild(kind);
      row.appendChild(detail);
      consoleFeed.appendChild(row);
    });
  }

  function renderAnswer(text, meta) {
    if (!out) return;
    out.hidden = false;
    out.textContent = "";
    var body = document.createElement("div");
    body.className = "answer";
    body.textContent = String(text || "(nothing returned)");
    out.appendChild(body);
    if (body.textContent.length > 420) {
      body.classList.add("clip");
      var more = document.createElement("button");
      more.type = "button";
      more.className = "ghost act-more";
      more.textContent = "Show full answer";
      more.addEventListener("click", function () {
        body.classList.remove("clip");
        if (more.parentNode) more.parentNode.removeChild(more);
      });
      out.appendChild(more);
    }
    if (meta) {
      var caption = document.createElement("div");
      caption.className = "micro";
      caption.textContent = meta;
      out.appendChild(caption);
    }
  }

  function finish(data) {
    var payload = data || {};
    var response = String(
      payload.response || payload.final_answer || payload.final_proof || payload.summary || ""
    );
    var kind = String(payload.run_kind || "");
    var meta = [kind, payload.mission_id || ""].filter(Boolean).join(" \u00b7 ");
    renderAnswer(response, meta);
    setRun(kind === "mission_failed" ? "failed" : "done", kind === "mission" ? "mission done" : "done");
    if (ta) ta.value = "";
    if (stopBtn) stopBtn.hidden = true;
    currentJob = null;
  }

  function failRequest(res) {
    var message =
      (res.data && (res.data.message || res.data.detail || res.data.error)) ||
      ("request failed (" + res.status + ")");
    renderAnswer("Refused: " + message, "");
    setRun("failed", "refused");
    note("Directive refused: " + message, "error");
    if (sendBtn) sendBtn.disabled = false;
  }

  function failNetwork(err) {
    var message = err && err.message ? err.message : String(err);
    renderAnswer("Failed: " + message, "");
    setRun("failed", "failed");
    note("Directive failed: " + message, "error");
    if (sendBtn) sendBtn.disabled = false;
  }

  function pollJob(jobId) {
    var deadline = Date.now() + 20 * 60 * 1000;
    function tick() {
      pollTimer = null;
      fetchJSON("/jobs/" + encodeURIComponent(jobId))
        .then(function (data) {
          var status = String(data.status || "");
          if (status === "done") {
            finish(data.result || {});
            return;
          }
          if (status === "failed" || status === "cancelled" || status === "error") {
            renderAnswer("Job " + status + ": " + String(data.error || "open Jobs for details"), jobId);
            setRun("failed", status);
            if (stopBtn) stopBtn.hidden = true;
            currentJob = null;
            return;
          }
          setRun("running", status || "running");
          if (Date.now() > deadline) {
            setRun("failed", "still running — open Jobs");
            return;
          }
          pollTimer = setTimeout(tick, 2000);
        })
        .catch(function (err) {
          setRun("failed", "poll failed");
          note("Job poll failed: " + (err && err.message ? err.message : String(err)), "error");
        });
    }
    tick();
  }

  function sendDirective() {
    var text = (ta && ta.value ? ta.value : "").trim();
    if (!text) {
      note("Write a directive first.", "warn");
      if (ta) ta.focus();
      return;
    }
    var mode = (modeSel && modeSel.value) || "agent";
    if (sendBtn) sendBtn.disabled = true;
    if (out) {
      out.hidden = false;
      out.textContent = "working\u2026";
    }

    if (mode === "computer") {
      setRun("running", "computer task");
      fetchJSON("/computer/run", { objective: text })
        .then(function (res) {
          if (!res.ok) return failRequest(res);
          var data = res.data || {};
          renderAnswer("Computer task accepted: " + (data.task_id || data.id || "queued"), "computer");
          setRun("done", "queued");
        })
        .catch(failNetwork)
        .then(function () { if (sendBtn) sendBtn.disabled = false; });
      return;
    }

    var auto = autonomy && mode !== "chat";
    var body = { text: text, mode: mode, prefer: auto ? "auto" : "chat" };
    if (auto) body.async = true;
    setRun("running", auto ? "queued" : "replying");

    fetchJSON("/command", body)
      .then(function (res) {
        if (!res.ok) return failRequest(res);
        var data = res.data || {};
        if (data.async && data.job_id) {
          currentJob = data.job_id;
          if (stopBtn) stopBtn.hidden = false;
          setRun("running", "mission running");
          if (ta) ta.value = "";
          pollJob(data.job_id);
          return;
        }
        finish(data);
      })
      .catch(failNetwork)
      .then(function () { if (sendBtn) sendBtn.disabled = false; });
  }

  if (sendBtn) sendBtn.addEventListener("click", sendDirective);
  if (stopBtn) {
    stopBtn.addEventListener("click", function () {
      if (!currentJob) return;
      stopBtn.disabled = true;
      fetchJSON("/jobs/" + encodeURIComponent(currentJob) + "/cancel", {})
        .then(function () {
          if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
          setRun("failed", "stopped");
          renderAnswer("Stopped. The mission is recorded and can be resumed from Missions.", "cancelled");
          currentJob = null;
        })
        .catch(failNetwork)
        .then(function () { stopBtn.disabled = false; stopBtn.hidden = true; });
    });
  }

  /* ---- autonomy switch -------------------------------------------------- */
  var autonomy = read("hermus_autonomy") !== "0";

  function applyAutonomy(on) {
    autonomy = !!on;
    if (autonomyBtn) {
      autonomyBtn.classList.toggle("on", autonomy);
      autonomyBtn.setAttribute("aria-pressed", autonomy ? "true" : "false");
      autonomyBtn.textContent = autonomy ? "Autonomy on" : "Autonomy off";
    }
    store("hermus_autonomy", autonomy ? "1" : "0");
  }

  if (autonomyBtn) {
    autonomyBtn.addEventListener("click", function () {
      applyAutonomy(!autonomy);
      setRun("", "ready");
    });
  }
  if (modeSel) modeSel.addEventListener("change", function () { setRun("", "ready"); });

  if (ta) {
    ta.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        sendDirective();
      }
    });
  }

  /* ---- keyboard --------------------------------------------------------- */
  document.addEventListener("keydown", function (e) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "");
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      openPalette();
      return;
    }
    if (e.key === "Escape") {
      if (root.classList.contains("advanced")) applyAdvanced(false);
      else if (backdrop && backdrop.classList.contains("on")) closePalette();
      else if (drawer && drawer.classList.contains("on")) applyDrawer(false);
      return;
    }
    if (!typing && e.key === "[") {
      e.preventDefault();
      applyDrawer(!(drawer && drawer.classList.contains("on")));
    }
  });

  /* ---- boot ------------------------------------------------------------- */
  // The sheet is a deliberate act every time: never restore it open.
  applyAdvanced(false);
  applyDetails(read("hermus_details") === "1");
  applyExplain(read("hermus_explain") === "1");
  applyAutonomy(read("hermus_autonomy") !== "0");
  var storedCalm = read("hermus_calm");
  var prefersReduced = false;
  try {
    prefersReduced = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  } catch (e) { /* older browser */ }
  // An explicit choice always wins; with no stored choice the OS preference is
  // the default surface, not an afterthought.
  applyCalm(storedCalm === "1" || (storedCalm === null && prefersReduced));
  collectTabs();
  collectActions();
  filter();
})();
