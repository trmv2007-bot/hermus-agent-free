/* THE HOOD — JARVIS 3D instrument space.
 *
 * Opt-in full-power view: every instrument domain (fleet, missions, telemetry,
 * safety, systems) is pulled into a GPU-composited CSS-3D ring around the
 * God's-Eye globe. Panels are live DOM: the data comes from the same honest
 * probes as the face, and the controls call the same real endpoints. Nothing
 * here is simulated; a probe that fails says so inside its panel.
 */
(function () {
  "use strict";

  const REDUCED =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const hood = document.getElementById("hoodSpace");
  if (!hood) return;
  const room = document.getElementById("hoodRoom");

  const cam = { yaw: 0, pitch: -4, dol: -200 };
  let open = false;
  let dragging = false;
  let lastInteract = 0;
  let pollTimer = null;
  let globe = null;

  function applyCam() {
    room.style.transform =
      "translateZ(" + cam.dol.toFixed(1) + "px) rotateX(" + cam.pitch.toFixed(2) +
      "deg) rotateY(" + cam.yaw.toFixed(2) + "deg)";
  }

  function orbitLoop() {
    if (open && !REDUCED && !dragging && performance.now() - lastInteract > 4000) {
      cam.yaw += 0.03;
      applyCam();
    }
    requestAnimationFrame(orbitLoop);
  }

  // ---------------------------------------------------------------- open/close
  function openHood() {
    open = true;
    hood.hidden = false;
    document.body.classList.add("hood-open");
    lastInteract = performance.now();
    if (!globe && window.mountGodsEye) {
      globe = window.mountGodsEye(document.getElementById("hoodCanvas"), {
        speed: 0.005,
        active: function () { return open; },
      });
    }
    applyCam();
    refreshHood();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(refreshHood, 4000);
  }

  function closeHood() {
    open = false;
    hood.hidden = true;
    document.body.classList.remove("hood-open");
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  window.openHood = openHood;
  window.closeHood = closeHood;

  // ---------------------------------------------------------------- camera input
  hood.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".hood-panel, .hood-hud, .hood-detail, button")) return;
    dragging = true;
    hood.classList.add("dragging");
    lastInteract = performance.now();
    const sx = e.clientX, sy = e.clientY, y0 = cam.yaw, p0 = cam.pitch;
    const move = (ev) => {
      cam.yaw = y0 + (ev.clientX - sx) * 0.25;
      cam.pitch = Math.max(-20, Math.min(26, p0 - (ev.clientY - sy) * 0.12));
      applyCam();
    };
    const up = () => {
      dragging = false;
      hood.classList.remove("dragging");
      lastInteract = performance.now();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });

  hood.addEventListener("wheel", (e) => {
    e.preventDefault();
    cam.dol = Math.max(-320, Math.min(300, cam.dol - e.deltaY * 0.4));
    lastInteract = performance.now();
    applyCam();
  }, { passive: false });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && open) closeHood();
    if ((e.key === "h" || e.key === "H") && !open && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) openHood();
  });

  hood.addEventListener("click", (e) => {
    const face = e.target.closest(".hood-face");
    if (face) {
      closeHood();
      if (window.selectTab) selectTab(face.getAttribute("data-facetab"));
      return;
    }
    if (e.target.id === "hoodClose") { closeHood(); return; }
    if (e.target.id === "hoodReset") { cam.yaw = 0; cam.pitch = -4; cam.dol = -200; applyCam(); return; }
    if (e.target.id === "hoodEmergency") { if (window.toggleEmergency) toggleEmergency(); return; }
    const node = e.target.closest(".hood-node");
    if (node) showAgentDetail(node.getAttribute("data-agent"));
    const act = e.target.closest("[data-hood-act]");
    if (act) runAgentAct(act.getAttribute("data-hood-act"), act.getAttribute("data-agent"));
  });

  // ---------------------------------------------------------------- live data
  function panelBody(id) { return document.getElementById(id); }

  function fail(el, msg) { el.innerHTML = '<div class="note">probe failed: ' + esc(msg) + " — nothing substituted.</div>"; }

  function refreshHood() {
    if (!open) return;
    getJSON("/api/fleet/agents").then(({ j }) => {
      const el = panelBody("hoodFleet");
      const agents = (j && j.agents) || [];
      if (!el) return;
      if (!agents.length) { el.innerHTML = '<div class="note">No agents in fleet. The face tab spawns them.</div>'; return; }
      el.innerHTML = agents.map((a) =>
        '<button class="hood-node" data-agent="' + esc(a.agent_id || a.id) + '">' +
        '<span class="hood-dot s-' + esc((a.state || "idle").toLowerCase()) + '"></span>' +
        "<span>" + esc(a.name) + '</span><span class="st">' + esc(a.state || "--") + "</span></button>"
      ).join("");
    }).catch((e) => { const el = panelBody("hoodFleet"); if (el) fail(el, e.message); });

    getJSON("/missions").then(({ j }) => {
      const el = panelBody("hoodMissions");
      if (!el) return;
      const ms = (j && j.missions) || (Array.isArray(j) ? j : []);
      if (!ms.length) { el.innerHTML = '<div class="note">No missions. Autonomy drives them once proposed.</div>'; return; }
      el.innerHTML = ms.slice(0, 14).map((m) =>
        '<div class="hood-row"><span class="k">' + esc(m.mission_id || m.id) + "</span>" +
        '<span class="v ' + (m.state === "done" ? "ok" : m.state === "failed" ? "err" : m.state === "blocked" || m.state === "suspended" ? "warn" : "") + '">' +
        esc(m.state || "--") + " &#183; " + esc(String(m.progress != null ? m.progress : (m.subtasks ? m.subtasks.filter((s) => s.status === "done").length + "/" + m.subtasks.length : "--"))) +
        "</span></div>"
      ).join("");
    }).catch((e) => { const el = panelBody("hoodMissions"); if (el) fail(el, e.message); });

    getJSON("/events/recent?limit=50").then(({ j }) => {
      const el = panelBody("hoodTelemetry");
      if (!el) return;
      const evs = (j && (j.events || j.recent)) || (Array.isArray(j) ? j : []);
      if (!evs.length) { el.innerHTML = '<div class="note">Event bus empty.</div>'; return; }
      el.innerHTML = evs.slice(-14).reverse().map((ev) =>
        '<div class="hood-row"><span class="k">' + esc(ev.kind || ev.type || "?") + "</span>" +
        '<span class="v">' + esc(ev.sender || "--") + "</span></div>"
      ).join("");
    }).catch((e) => { const el = panelBody("hoodTelemetry"); if (el) fail(el, e.message); });

    Promise.all([
      getJSON("/emergency/status").catch((e) => ({ j: { _err: e.message } })),
      getJSON("/permissions/pending").catch((e) => ({ j: { _err: e.message } })),
    ]).then(([st, pend]) => {
      const el = panelBody("hoodSafety");
      if (!el) return;
      const rows = [];
      if (st.j && st.j._err) rows.push(['brake', 'probe failed: ' + st.j._err, "err"]);
      else {
        const active = !!(st.j.emergency_stop || {}).active;
        rows.push(["brake", active ? "ACTIVE" : "clear", active ? "err" : "ok"]);
      }
      if (pend.j && pend.j._err) rows.push(["pending approvals", "probe failed: " + pend.j._err, "err"]);
      else {
        const list = Array.isArray(pend.j) ? pend.j : (pend.j && (pend.j.pending || pend.j.approvals)) || [];
        rows.push(["pending approvals", String(list.length), list.length ? "warn" : "ok"]);
      }
      rows.push(["authority", "Safety Core outranks watchdog", ""]);
      el.innerHTML = rows.map((r) =>
        '<div class="hood-row"><span class="k">' + esc(r[0]) + '</span><span class="v ' + r[2] + '">' + esc(r[1]) + "</span></div>"
      ).join("");
    });

    getJSON("/api/v1/system/health").then(({ j }) => {
      const el = panelBody("hoodSystems");
      if (!el) return;
      const keys = Object.keys(j || {}).filter((k) => k[0] !== "_");
      if (!keys.length) { el.innerHTML = '<div class="note">Health probe returned nothing.</div>'; return; }
      el.innerHTML = keys.slice(0, 16).map((k) => {
        const v = j[k];
        const txt = v && typeof v === "object" ? (v.ok === true ? "ok" : v.ok === false ? "FAIL" : JSON.stringify(v).slice(0, 34)) : String(v);
        const cls = v && typeof v === "object" ? (v.ok === true ? "ok" : v.ok === false ? "err" : "") : "";
        return '<div class="hood-row"><span class="k">' + esc(k) + '</span><span class="v ' + cls + '">' + esc(txt) + "</span></div>";
      }).join("");
    }).catch((e) => { const el = panelBody("hoodSystems"); if (el) fail(el, e.message); });
  }

  // ---------------------------------------------------------------- agent control
  let detailAgent = null;

  function showAgentDetail(id) {
    detailAgent = id;
    getJSON("/api/fleet/agents").then(({ j }) => {
      const a = ((j && j.agents) || []).find((x) => (x.agent_id || x.id) === id);
      const box = document.getElementById("hoodDetail");
      if (!box) return;
      if (!a) { box.hidden = true; return; }
      box.hidden = false;
      box.innerHTML =
        "<h4>" + esc(a.name) + "</h4>" +
        '<div class="hood-row"><span class="k">state</span><span class="v">' + esc(a.state || "--") + "</span></div>" +
        '<div class="hood-row"><span class="k">model</span><span class="v">' + esc(a.model || "--") + "</span></div>" +
        '<div class="hood-row"><span class="k">tasks done</span><span class="v">' + esc(String(a.stats ? a.stats.tasks_done : "--")) + "</span></div>" +
        '<div class="bar">' +
        '<button data-hood-act="pause" data-agent="' + esc(id) + '">pause</button>' +
        '<button data-hood-act="resume" data-agent="' + esc(id) + '">resume</button>' +
        '<button data-hood-act="cancel" data-agent="' + esc(id) + '">cancel task</button>' +
        '<button data-hood-act="face" data-agent="' + esc(id) + '">view in face</button>' +
        "</div>";
    }).catch(() => {});
  }

  function runAgentAct(act, id) {
    if (act === "pause" && window.pauseAgent) pauseAgent(id);
    if (act === "resume" && window.resumeAgent) resumeAgent(id);
    if (act === "cancel" && window.cancelAgentTask) cancelAgentTask(id);
    if (act === "face") { closeHood(); if (window.selectTab) selectTab("agents"); return; }
    setTimeout(() => { refreshHood(); if (detailAgent) showAgentDetail(detailAgent); }, 400);
  }

  // ---------------------------------------------------------------- boot wiring
  const openBtn = document.getElementById("hoodOpenBtn");
  if (openBtn) openBtn.addEventListener("click", openHood);
  requestAnimationFrame(orbitLoop);
})();
