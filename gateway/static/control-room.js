const $ = (s) => document.querySelector(s);
// NOTE: the gateway token bootstrap lives ONLY in the inline <script> in
// control.html (it must run before this deferred file so the fetch wrapper is
// installed before any API call). It declares `gatewayToken`/`tokenFromUrl` at
// top level; redeclaring them here would throw SyntaxError and abort the whole
// script chain (requestJSON/toast/console boot would never be defined).
const conn = $("#conn");
const rtt = $("#rtt");
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
function healthy(){ conn.textContent="live"; conn.className="pill ok"; }
function down(){ conn.textContent="offline (preview)"; conn.className="pill err"; }

/* The connection pill reports the gateway's OWN readiness probe (/readyz)
 * rather than inferring health from whichever snapshot happened to succeed:
 *   200        -> ready (taking traffic)
 *   503        -> up, but draining or not started: honest and actionable
 *   unreachable-> offline
 * Readiness is derived server-side from real state, so the pill cannot claim
 * "live" over a gateway that is refusing work. */
async function refreshReadiness(){
  if (!conn) return;
  const res = await requestJSON("/readyz");
  if (res.ok) {
    conn.textContent = "live"; conn.className = "pill ok";
    conn.title = "gateway ready — accepting traffic";
    return;
  }
  if (res.status === 503) {
    // Prefer the envelope's own reasons; fall back to the raw body's reasons
    // (a 503 from something other than /readyz may not be enveloped) and
    // finally to the message, so the pill never hides *why* it is not ready.
    const d = res.details || {};
    const raw = res.data || {};
    const reasons = (d.reasons && d.reasons.length) ? d.reasons.join("; ")
      : ((raw.reasons && raw.reasons.length) ? raw.reasons.join("; ") : res.message);
    conn.textContent = "not ready"; conn.className = "pill warn";
    conn.title = "gateway is up but not accepting traffic: " + reasons;
    return;
  }
  conn.textContent = res.status ? ("gateway " + res.status) : "offline";
  conn.className = "pill err";
  conn.title = res.message || "gateway unreachable";
}
function setPill(id, text, cls){ const el = $(id); if (el) { el.textContent = text; el.className = "pill " + (cls || ""); } }

/* ===========================================================================
 * Canonical API layer
 *
 * Every gateway response — success or failure — follows one contract
 * (gateway/envelope.py). Failures carry {success:false, error, code, message,
 * retryable, details} and every response carries X-Request-ID. requestJSON()
 * understands that contract, so a panel can show the server's own message and
 * offer a retry when `retryable` is true, instead of the old "url -> 500".
 * ======================================================================== */
function requestJSON(url, opts) {
  const options = Object.assign({ headers: { "Accept": "application/json" } }, opts || {});
  const t0 = Date.now();
  return fetch(url, options).then((r) => {
    const ms = Date.now() - t0;
    const requestId = r.headers ? r.headers.get("x-request-id") : null;
    return r.text().then((text) => {
      let body = null;
      try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
      if (r.ok) {
        return { ok: true, data: body, ms: ms, status: r.status, requestId: requestId };
      }
      // Canonical envelope (ErrorEnvelopeMiddleware guarantees these keys on
      // every 4xx/5xx JSON response), with a fallback for non-JSON errors.
      const env = (body && typeof body === "object") ? body : {};
      return {
        ok: false, data: null, ms: ms, status: r.status, requestId: requestId,
        code: env.code || env.error || ("http_" + r.status),
        message: env.message || env.detail || env.error || ("Request failed (" + r.status + ")"),
        retryable: env.retryable === true,
        details: env.details || {},
      };
    });
  }).catch((e) => ({
    // Network-level failure: no response, so no envelope. Always retryable —
    // the gateway may simply be restarting.
    ok: false, data: null, ms: Date.now() - t0, status: 0, requestId: null,
    code: "network_error", message: (e && e.message) ? e.message : "Network error",
    retryable: true, details: {},
  }));
}

function postJSON(url, body) {
  return requestJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
/* getJSON keeps its historical {j, ms} shape so the ~15 existing call sites
 * keep working, but the error it throws now carries the parsed envelope. */
function getJSON(url){
  return requestJSON(url).then((res) => {
    if (!res.ok) {
      const err = new Error(res.message || (url + " -> " + res.status));
      err.envelope = res; err.status = res.status; err.retryable = res.retryable;
      err.requestId = res.requestId;
      throw err;
    }
    return { j: res.data, ms: res.ms };
  });
}

/* Toasts: one place for action feedback. `retryable` failures get a Retry
 * button, because the envelope tells us whether resending can succeed. */
function toast(message, kind, opts) {
  const o = opts || {};
  const host = $("#toasts");
  if (!host) return null;
  const el = document.createElement("div");
  el.className = "toast " + (kind || "info");
  const text = document.createElement("span");
  text.className = "toast-msg";
  text.textContent = message;
  el.appendChild(text);
  if (o.requestId) {
    const rid = document.createElement("code");
    rid.className = "toast-rid";
    rid.textContent = o.requestId;
    rid.title = "X-Request-ID — quote this when reporting a problem";
    el.appendChild(rid);
  }
  if (o.retry && o.onRetry) {
    const btn = document.createElement("button");
    btn.className = "toast-retry ghost";
    btn.textContent = "Retry";
    btn.addEventListener("click", () => { dismiss(); o.onRetry(); });
    el.appendChild(btn);
  }
  const close = document.createElement("button");
  close.className = "toast-close";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "×";
  close.addEventListener("click", dismiss);
  el.appendChild(close);
  host.appendChild(el);
  const ttl = o.retry ? 12000 : (kind === "error" ? 9000 : 4000);
  const timer = setTimeout(dismiss, ttl);
  function dismiss() {
    clearTimeout(timer);
    if (el.parentNode) el.parentNode.removeChild(el);
  }
  return dismiss;
}

/* Report a failed request once, consistently: toast with the server's own
 * message, a Retry button when the envelope says retryable, and the
 * X-Request-ID for correlation with the gateway log. */
function reportFailure(what, err, onRetry) {
  const env = err && err.envelope ? err.envelope : null;
  const code = (env && env.code) || (err && err.code) || "error";
  const message = (env && env.message) || (err && err.message) || String(err);
  const retryable = env ? env.retryable === true : (err ? err.retryable === true : true);
  console.error("[control-room] " + what + " failed", {
    code: code, message: message, status: (env && env.status) || 0,
    requestId: (env && env.requestId) || null,
  });
  toast(what + ": " + message, "error", {
    requestId: (env && env.requestId) || null,
    retry: retryable, onRetry: onRetry,
  });
}

/* Panel states: loading / empty / error, rendered the same everywhere so an
 * unconfigured backend never looks like a working one that happens to be
 * blank. Returned as HTML strings (not nodes) so they can be dropped into a
 * <tbody> without producing invalid markup. */
function stateHtml(state, opts) {
  const o = opts || {};
  if (state.loading) {
    return '<div class="skeleton"></div><div class="skeleton" style="width:70%"></div>'
      + '<div class="skeleton" style="width:85%"></div>';
  }
  if (state.empty) {
    return '<div class="panel-state">' + esc(state.empty) + '</div>';
  }
  const e = state.error || {};
  const label = o.label ? esc(o.label) + ": " : "";
  const code = e.code ? ' <span class="tag">(' + esc(e.code) + ')</span>' : "";
  const rid = e.requestId ? ' <code>' + esc(e.requestId) + '</code>' : "";
  const retry = (e.retryable && o.onRetryId)
    ? '<div class="panel-retry"><button class="ghost" data-retry="' + esc(o.onRetryId) + '">Retry</button></div>'
    : "";
  return '<div class="panel-state error">' + label + esc(e.message || "Request failed") + code + rid + retry + '</div>';
}

/* Same, wrapped in a table row (for <tbody> panels such as the job queue). */
function stateRowHtml(state, colspan, opts) {
  return '<tr><td colspan="' + colspan + '">' + stateHtml(state, opts) + "</td></tr>";
}

/* Retry buttons rendered by stateHtml() are delegated here: each panel
 * registers the function that should re-run. */
const RETRY_ACTIONS = {};
document.addEventListener("click", (ev) => {
  const btn = ev.target && ev.target.closest ? ev.target.closest("[data-retry]") : null;
  if (!btn) return;
  const fn = RETRY_ACTIONS[btn.getAttribute("data-retry")];
  if (fn) fn();
});
function updateSafetyCore(){
  const brake = $("#emergencyState")?.textContent || "brake: —";
  const pending = $("#pendingCount")?.textContent || "approvals: —";
  const blocked = $("#blockedMissionCount")?.textContent || "blocked missions: —";
  const activeBrake = brake.includes("ACTIVE");
  const pendingN = parseInt((pending.match(/\d+/)||["0"])[0],10) || 0;
  const blockedN = parseInt((blocked.match(/\d+/)||["0"])[0],10) || 0;
  const label = activeBrake ? "safety core: BRAKE" : (pendingN || blockedN ? "safety core: needs review" : "safety core: clear");
  setPill("#safetyCore", label, activeBrake ? "err" : (pendingN || blockedN ? "warn" : "ok"));
  const core = $("#jarvisSafetyCore");
  if (core) core.innerHTML = kpi(activeBrake ? "ACTIVE" : "clear", "emergency brake") + kpi(String(pendingN), "pending approvals") + kpi(String(blockedN), "blocked missions") + kpi("visible", "capability ledger");
}

function kpi(v,l){ return `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`; }

// ---------- tabs ----------
/* Tabs are a real ARIA tablist (see control.html), not just styled buttons:
 * aria-selected tracks the visible panel, only the selected tab is tabbable
 * (roving tabindex), and the arrow / Home / End keys move between tabs the way
 * a native tab strip does. */
function selectTab(name, opts) {
  const o = opts || {};
  document.querySelectorAll("nav [role=tab]").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.id === "tab-" + name;
    t.classList.toggle("on", on);
    if (on) t.removeAttribute("aria-hidden");
    else t.setAttribute("aria-hidden", "true");
  });
  if (!o.noRefresh) refreshTab(name);
}

document.querySelectorAll("nav [role=tab]").forEach((b) => {
  b.addEventListener("click", () => selectTab(b.dataset.tab));
  b.addEventListener("keydown", (ev) => {
    const tabs = Array.prototype.slice.call(document.querySelectorAll("nav [role=tab]"));
    const i = tabs.indexOf(b);
    let next = -1;
    if (ev.key === "ArrowRight") next = (i + 1) % tabs.length;
    else if (ev.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    else if (ev.key === "Home") next = 0;
    else if (ev.key === "End") next = tabs.length - 1;
    if (next < 0) return;
    ev.preventDefault();
    tabs[next].focus();
    selectTab(tabs[next].dataset.tab);
  });
});

// Mark the initially hidden panels without triggering a refresh on load.
(function initTabs(){
  const active = document.querySelector("nav [role=tab].on");
  selectTab(active ? active.dataset.tab : "chat", { noRefresh: true });
})();
function refreshTab(name){
  if (name === "jobs") refreshJobs();
  else if (name === "missions") refreshMissions();
  else if (name === "computer") refreshComputer();
  else if (name === "remote") refreshRemote();
  else if (name === "safety") refreshSafety();
  else if (name === "systems") { if (window.HermusConsole) window.HermusConsole.refresh(); }
  else if (name === "presence") refreshPresence();
  else if (name === "agents") refreshAgents();
  else if (name === "settings") refreshSettings();
}

// ---------- PRESENCE / CONTINUITY ----------
function presenceTime(value){
  if (!value) return "—";
  try { return new Date(value).toLocaleString(); } catch(e){ return String(value); }
}
function renderPresence(data){
  const identity = data.identity || {};
  const p = data.presence || {};
  const goals = data.goals || [];
  const moments = data.moments || [];
  const due = data.check_ins_due || [];
  const state = String(p.state || "idle");
  const stateEl = $("#presenceState");
  if (stateEl) { stateEl.textContent = state.replace("_", " "); stateEl.className = "presence-state"; }
  const pill = $("#presencePill");
  if (pill) { pill.textContent = "presence: " + state.replace("_", " "); pill.className = "pill " + (state === "error" ? "err" : (state === "idle" ? "ok" : "warn")); }
  const orb = $("#presenceOrb");
  if (orb) orb.className = "presence-orb " + state;
  if ($("#presenceName")) $("#presenceName").textContent = identity.name || "Hermus";
  if ($("#presenceRole")) $("#presenceRole").textContent = identity.role || "AI partner";
  if ($("#presenceDetail")) $("#presenceDetail").textContent = (p.detail || "ready") + (p.last_error ? " · " + p.last_error : "");
  if ($("#presenceMeta")) $("#presenceMeta").textContent =
    "heartbeat " + String(data.heartbeat?.count ?? p.heartbeat_count ?? 0) +
    " · active goals " + String(data.active_goal_count ?? goals.filter((g)=>g.status === "active").length) +
    " · last seen " + presenceTime(p.last_seen);
  if ($("#identityName")) $("#identityName").value = identity.name || "";
  if ($("#identityRole")) $("#identityRole").value = identity.role || "";
  if ($("#identityTone")) $("#identityTone").value = identity.tone || "";
  if ($("#identityValues")) $("#identityValues").value = (identity.values || []).join(", ");
  if ($("#identityGreeting")) $("#identityGreeting").value = identity.greeting || "";

  const goalsEl = $("#presenceGoals");
  if (goalsEl) {
    const active = goals.filter((g)=>g.status === "active");
    goalsEl.innerHTML = active.map((g) => `<div class="goal-card">
      <div class="goal-title"><strong>${esc(g.title)}</strong><span class="goal-age">priority ${esc(g.priority)}${due.some((d)=>d.id===g.id) ? " · check-in due" : ""}</span></div>
      <button class="ghost" data-complete-goal="${esc(g.id)}">done</button>
    </div>`).join("") || '<div class="hint">No ongoing goals.</div>';
    goalsEl.querySelectorAll("button[data-complete-goal]").forEach((b)=>b.addEventListener("click", ()=>completePresenceGoal(b.dataset.completeGoal)));
  }
  const momentsEl = $("#presenceMoments");
  if (momentsEl) {
    momentsEl.innerHTML = moments.slice().reverse().map((m)=>`<div><span class="t">${esc(presenceTime(m.at))}</span> <span class="e">${esc(m.kind)}</span> ${esc(m.summary)}</div>`).join("") || '<div class="note">No moments yet.</div>';
  }
  const out = $("#presenceOut");
  if (out && due.length) out.innerHTML = '<div class="note">' + due.length + ' check-in(s) due: ' + due.map((g)=>esc(g.title)).join(" · ") + '</div>';
  else if (out && !out.dataset.message) out.innerHTML = '<div class="note">No check-ins due. Heartbeat is healthy.</div>';
}
async function refreshPresence(){
  try {
    const { j } = await getJSON("/presence");
    renderPresence(j);
  } catch(e){
    const out = $("#presenceOut"); if (out) out.innerHTML = '<div class="note">presence unavailable: ' + esc(e.message) + '</div>';
  }
}
function savePresenceIdentity(){
  const out = $("#identityOut");
  const payload = {
    name: $("#identityName").value.trim(), role: $("#identityRole").value.trim(),
    tone: $("#identityTone").value.trim(), values: splitCsv($("#identityValues").value),
    greeting: $("#identityGreeting").value.trim(),
  };
  fetch("/presence/identity", { method:"PUT", headers:{ "Content-Type":"application/json" }, body:JSON.stringify(payload) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>identity saved: <span class="e">' + esc(d.success) + '</span></div>'; refreshPresence(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">identity save failed: ' + esc(e.message) + '</div>'; });
}
function addPresenceGoal(){
  const title = $("#presenceGoal").value.trim();
  if (!title) return;
  fetch("/presence/goals", { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify({ title }) })
    .then((r)=>r.json()).then((d)=>{ $("#presenceGoal").value = ""; $("#presenceOut").dataset.message = "1"; $("#presenceOut").innerHTML = '<div>goal added: <code>' + esc(d.goal?.id || d.error) + '</code></div>'; refreshPresence(); })
    .catch((e)=>{ $("#presenceOut").innerHTML = '<div class="note">goal failed: ' + esc(e.message) + '</div>'; });
}
function completePresenceGoal(id){
  fetch("/presence/goals/" + encodeURIComponent(id) + "/complete", { method:"POST", headers:{ "Content-Type":"application/json" }, body:"{}" })
    .then((r)=>r.json()).then(()=>refreshPresence()).catch((e)=>{ $("#presenceOut").innerHTML = '<div class="note">complete failed: ' + esc(e.message) + '</div>'; });
}
function sendPresenceHeartbeat(){
  fetch("/presence/heartbeat", { method:"POST" }).then((r)=>r.json()).then((d)=>{ renderPresence(d); $("#presenceOut").dataset.message = "1"; $("#presenceOut").innerHTML = '<div>heartbeat recorded at <span class="e">' + esc(presenceTime(d.presence?.last_heartbeat)) + '</span></div>'; }).catch((e)=>{ $("#presenceOut").innerHTML = '<div class="note">heartbeat failed: ' + esc(e.message) + '</div>'; });
}
function requestPresenceCheckIn(){
  const out = $("#presenceOut"); out.innerHTML = '<div class="note">queueing a read-only continuity check-in…</div>';
  fetch("/presence/check-in", { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify({ platform:"dashboard", user_id:"default" }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = d.queued
      ? '<div>check-in queued: <code>' + esc(d.job_id) + '</code> · follow the live job stream</div>'
      : '<div class="note">' + esc(d.reason || d.error || "check-in not queued") + '</div>'; refreshPresence(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">check-in failed: ' + esc(e.message) + '</div>'; });
}

// ---------- OVERVIEW ----------
function renderHealth(h){
  const caps = h.capabilities || {};
  let html = kpi(h.ok ? "ok" : "degraded", "health");
  html += kpi(esc(h.python || "-"), "python");
  html += kpi(esc(h.venv || "-"), "venv");
  Object.keys(caps).slice(0, 5).forEach((k) =>
    html += kpi(esc(caps[k].status || caps[k].present), k.replace(/^system\./, "")));
  $("#health").innerHTML = html || '<div class="kpi"><div class="v">—</div><div class="l">no data</div></div>';
}
function renderCaps(c){
  const providers = c.providers || [];
  const circ = c.circuit || {};
  const speech = c.speech || {};
  const avatar = c.avatar || {};
  const tx = c.transcription || {};
  let html = kpi(String(c.tool_count ?? "?"), "tools");
  html += kpi(String(providers.length), "providers");
  html += kpi(String(Object.keys(circ).length), "circuit entries");
  html += kpi(esc(speech.backend || (speech.available ? "ready" : "off")), "speech");
  html += kpi(String(tx.discovered_count ?? 0), "local stt models");
  html += kpi(avatar.available ? "ready" : "optional", "avatar");
  $("#caps").innerHTML = html;
}
async function refreshOverview(){
  try { const { j, ms } = await getJSON("/api/v1/system/health"); renderHealth(j); rtt.textContent = ms + "ms"; }
  catch(e){ $("#health").innerHTML = stateHtml({ error: e.envelope || { message: e.message } },
    { label: "system health", onRetryId: "chat" }); }
  try { await refreshReadiness(); } catch(e){}
  try { const { j } = await getJSON("/api/v1/system/capabilities"); renderCaps(j); } catch(e){}
  try { await refreshEmergency(); } catch(e){}
}
async function refreshEmergency(){
  const { j } = await getJSON("/emergency/status");
  const st = j.emergency_stop || {};
  const pill = $("#emergencyState");
  pill.textContent = st.active ? "brake: ACTIVE" : "brake: clear";
  pill.className = st.active ? "pill err" : "pill ok";
  $("#emergencyBtn").textContent = st.active ? "Resume" : "Emergency stop";
  updateSafetyCore();
}
function toggleEmergency(){
  getJSON("/emergency/status").then(({j}) => {
    const active = !!(j.emergency_stop || {}).active;
    const url = active ? "/emergency/resume" : "/emergency/stop";
    const reason = active ? "dashboard resume" : "dashboard emergency stop";
    return fetch(url, { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ reason, set_by:"control" }) });
  }).then(()=>refreshEmergency()).catch(()=>refreshEmergency());
}

function replayTimeline(runId){
  const log = $("#log");
  if (!runId) { log.innerHTML = '<div class="note">enter a run/mission id</div>'; return; }
  log.innerHTML = '<div class="note">replaying ' + esc(runId) + '…</div>';
  getJSON("/api/v1/runs/" + encodeURIComponent(runId) + "/timeline")
    .then(({ j }) => {
      const ev = (j.timeline || []).slice(-60);
      if (!ev.length) { log.innerHTML = '<div class="note">no canonical events for ' + esc(runId) + '</div>'; return; }
      log.innerHTML = ev.map((e) => `<div><span class="t">${esc(e.ts || e.timestamp || "")}</span> <span class="e">${esc(e.type)}</span> ${esc(e.command || e.status || e.session_id || "")}</div>`).join("")
        + `<div class="note">${ev.length} events (durable ledger)</div>`;
    })
    .catch((e) => { log.innerHTML = '<div class="note">replay failed: ' + esc(e.message) + '</div>'; });
}

function postCommand(){
  const out = $("#cmdOut");
  const cmd = $("#cmdName").value.trim();
  let args = {};
  try { args = JSON.parse($("#cmdArgs").value || "{}"); }
  catch(e){ out.innerHTML = '<div class="note">invalid args JSON: ' + esc(e.message) + '</div>'; return; }
  out.innerHTML = '<div class="note">sending ' + esc(cmd) + '…</div>';
  fetch("/api/v1/commands", { method:"POST", headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ command: cmd, args, source: "control" }) })
    .then((r) => r.json()).then((d) => {
      out.innerHTML = '<div>ok: <span class="e">' + esc(d.ok) + '</span> trace <code>' + esc(d.trace_id) + '</code> command <code>' + esc(d.command_id) + '</code></div>';
    })
    .catch((e) => { out.innerHTML = '<div class="note">command failed: ' + esc(e.message) + '</div>'; });
}

// ---------- JOBS ----------
async function refreshJobs(){
  const body = $("#jobs tbody");
  try {
    const { j } = await getJSON("/jobs");
    const rows = (Array.isArray(j) ? j : j.jobs || []).slice(0, 60);
    if (!rows.length) { body.innerHTML = stateRowHtml({ empty: "queue idle — no jobs yet" }, 6); }
    else body.innerHTML = rows.map((x) => `<tr><td><code>${esc(x.id)}</code></td><td>${esc(x.kind)}</td><td><span class="status s-${esc(x.status)}">${esc(x.status)}</span></td><td>${esc(x.attempts)}/${esc(x.max_attempts)}</td><td>${esc(x.run_id || "")}</td><td>${esc(x.created_at || "")}</td></tr>`).join("");
    const n = Array.isArray(j) ? j.length : (j.jobs || []).length;
    $("#jobcount").textContent = n + " jobs";
    const { j: st } = await getJSON("/queue/status");
    const bs = st.by_status || {};
    $("#qstatus").textContent = "backend=" + (st.backend || "?") + " · workers=" + (st.workers || "?") + " · " + Object.entries(bs).map(([k,v])=>v+" "+k).join(", ");
  } catch(e){
    body.innerHTML = stateRowHtml({ error: e.envelope || { message: e.message } }, 6,
      { label: "queue", onRetryId: "jobs" });
  }
}
RETRY_ACTIONS.jobs = refreshJobs;

// ---------- MISSIONS ----------
async function refreshMissions(){
  const body = $("#missions tbody");
  try {
    const { j } = await getJSON("/missions");
    const rows = (j.missions || []).slice(0, 80);
    const blockedN = rows.filter((m) => String(m.state).toLowerCase() === "blocked" || m.approval_request).length;
    setPill("#blockedMissionCount", "blocked missions: " + blockedN, blockedN ? "warn" : "ok");
    updateSafetyCore();
    if (!rows.length) { body.innerHTML = stateRowHtml({ empty: "no missions yet" }, 5); return; }
    body.innerHTML = rows.map((m) => {
      const ap = m.approval_request || {};
      const failure = m.failure || {};
      const createPrompts = m.create_prompts_action || {};
      const blocker = ap.id ? ('approval <code>' + esc(ap.id) + '</code>') : esc(m.blocker_reason || failure.reason || '');
      const approveBtn = ap.id ? `<button class="ghost" data-mission-approve="${esc(m.mission_id)}" data-approval-id="${esc(ap.id)}">approve+retry</button>` : '';
      const promptsBtn = createPrompts.endpoint ? `<button class="ghost" data-mission-prompts="${esc(m.mission_id)}">create prompts</button>` : '';
      const resumeBtn = ['blocked','failed','executing','pending','continuing'].includes(String(m.state)) ? `<button class="ghost" data-mission-resume="${esc(m.mission_id)}">resume</button>` : '';
      return `<tr><td><code>${esc(m.mission_id)}</code></td><td><span class="status s-${esc(m.state)}">${esc(m.state)}</span></td><td>${esc(m.progress_pct ?? 0)}%</td><td>${blocker}</td><td>${approveBtn} ${promptsBtn} ${resumeBtn}</td></tr>`;
    }).join("");
    body.querySelectorAll("button[data-mission-approve]").forEach((b) => b.addEventListener("click", () => approveMission(b.dataset.missionApprove, b.dataset.approvalId)));
    body.querySelectorAll("button[data-mission-prompts]").forEach((b) => b.addEventListener("click", () => createMissionPrompts(b.dataset.missionPrompts)));
    body.querySelectorAll("button[data-mission-resume]").forEach((b) => b.addEventListener("click", () => resumeMission(b.dataset.missionResume)));
  } catch(e){ setPill("#blockedMissionCount", "blocked missions: ?", "warn"); updateSafetyCore();
    body.innerHTML = stateRowHtml({ error: e.envelope || { message: e.message } }, 5,
      { label: "missions", onRetryId: "missions" });
  }
}
RETRY_ACTIONS.missions = refreshMissions;
function missionPreflight(){
  const goal = $("#missionGoal").value.trim();
  const out = $("#missionPreflightOut");
  if (!goal) { out.innerHTML = '<div class="note">mission goal required</div>'; return; }
  out.innerHTML = '<div class="note">running mission pre-flight…</div>';
  fetch("/safety/preflight", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ goal, format:"markdown" }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = mdLite(d.markdown || JSON.stringify(d, null, 2)); refreshSafetyEvents(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">mission pre-flight failed: ' + esc(e.message) + '</div>'; });
}
function startMission(allowPlanningBlocked){
  const goal = $("#missionGoal").value.trim();
  const out = $("#missionPreflightOut");
  if (!goal) { out.innerHTML = '<div class="note">mission goal required</div>'; return; }
  out.innerHTML = '<div class="note">starting mission with pre-flight…</div>';
  fetch("/missions", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ goal, preflight:true, allow_preflight_planning: !!allowPlanningBlocked }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>mission <code>' + esc(d.mission_id || '') + '</code>: <span class="e">' + esc(d.state || d.status || d.error) + '</span></div>' + (d.preflight ? '<div>pre-flight: <span class="e">' + esc(d.preflight.status) + '</span></div>' : ''); refreshMissions(); refreshSafetyEvents(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">mission start failed: ' + esc(e.message) + '</div>'; });
}
function createMissionPrompts(missionId){
  const out = $("#missionOut");
  out.innerHTML = '<div class="note">creating draft approval prompts for <code>' + esc(missionId) + '</code>…</div>';
  fetch("/missions/" + encodeURIComponent(missionId) + "/preflight/approvals", { method:"POST", headers:{ "Content-Type":"application/json" }, body:"{}" })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>created prompts for <code>' + esc(missionId) + '</code>: <span class="e">' + esc((d.created||[]).length) + '</span></div>'; refreshSafety(); refreshMissions(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">create prompts failed: ' + esc(e.message) + '</div>'; });
}
function approveMission(missionId, approvalId){
  const out = $("#missionOut");
  out.innerHTML = '<div class="note">approving ' + esc(approvalId) + ' and retrying…</div>';
  fetch("/permissions/pending/resolve", { method:"POST", headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ id: approvalId, decision:"approve", ttl_minutes:30, max_uses:1, retry:true }) })
    .then((r)=>r.json()).then((d)=>{
      out.innerHTML = '<div>approval <code>' + esc(approvalId) + '</code>: <span class="e">' + esc(d.success) + '</span>; resuming mission…</div>';
      return fetch("/missions/" + encodeURIComponent(missionId) + "/resume", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ extra_steps: 8 }) });
    })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML += '<div>resume <code>' + esc(missionId) + '</code>: <span class="e">' + esc(d.state || d.status || d.success || d.error) + '</span></div>'; refreshMissions(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">approve/resume failed: ' + esc(e.message) + '</div>'; });
}
function resumeMission(missionId){
  const out = $("#missionOut");
  fetch("/missions/" + encodeURIComponent(missionId) + "/resume", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ extra_steps: 8 }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>resume <code>' + esc(missionId) + '</code>: <span class="e">' + esc(d.state || d.status || d.success || d.error) + '</span></div>'; refreshMissions(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">resume failed: ' + esc(e.message) + '</div>'; });
}

// ---------- TELEMETRY ----------
let tmWs = null;
function tmAppend(e){
  const feed = $("#tmFeed");
  const f = $("#tmSearch").value.trim();
  const t = (e && e.type) || "";
  if (f && !t.includes(f)) return;
  const el = document.createElement("div");
  el.innerHTML = `<span class="t">${esc(e.ts || e.timestamp || "")}</span> <span class="e">${esc(t)}</span> ${esc(e.command || e.status || "")}`;
  feed.prepend(el);
  while (feed.children.length > 200) feed.removeChild(feed.lastChild);
}
async function refreshTelemetry(force){
  try {
    const { j } = await getJSON("/events/recent?limit=50");
    const feed = $("#tmFeed");
    if (force || !feed.dataset.ready) {
      feed.innerHTML = "";
      (j.events || []).slice().reverse().forEach(tmAppend);
      feed.dataset.ready = "1";
    }
  } catch(e){
    const feed = $("#tmFeed");
    if (feed && !feed.childElementCount) {
      feed.innerHTML = stateHtml({ error: e.envelope || { message: e.message } },
        { label: "telemetry", onRetryId: "telemetry" });
    }
  }
}
RETRY_ACTIONS.telemetry = () => refreshTelemetry(true);
RETRY_ACTIONS.safetyEvents = refreshSafetyEvents;
RETRY_ACTIONS.safetyReport = refreshSafetyReport;

function openTmStream(){
  try {
    const wsToken = gatewayToken ? "?token=" + encodeURIComponent(gatewayToken) : "";
    tmWs = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/dashboard/events" + wsToken);
    tmWs.onmessage = (ev) => {
      try { const msg = JSON.parse(ev.data); if (msg && msg.kind === "snapshot") msg.events.forEach(tmAppend); else if (msg && msg.id) tmAppend(msg); } catch(e){}
    };
  } catch(e){ /* poll fallback below */ }
  // poll fallback in case the WS is unavailable (e.g. static preview / no token)
  setInterval(() => { try { refreshTelemetry(false); } catch(e){} }, 5000);
}

// ---------- CHAT (the face) ----------
/* One honest path: the composer drives POST /stream/command, which submits the
 * canonical `runtime.turn` job and streams its run events back as SSE.
 * `llm_delta` paints the bubble live and `agent_response` is the authoritative
 * final text. If no stream can be opened, the turn falls back to one
 * synchronous /command call through the same runtime. A turn that fails is
 * rendered as a failure — the thread never receives an invented reply. */
const CHAT = { busy: false, ctrl: null };

function chatScroll(){
  const t = $("#chatThread");
  if (t) t.scrollTop = t.scrollHeight;
}

function chatRow(kind, label, text){
  const thread = $("#chatThread");
  if (!thread) return null;
  const seed = thread.querySelector(".chat-seed");
  if (seed) seed.remove();
  const row = document.createElement("div");
  row.className = "chat-row chat-" + kind;
  const who = document.createElement("span");
  who.className = "chat-who";
  who.textContent = label;
  const body = document.createElement("div");
  body.className = "chat-body";
  body.textContent = text || "";
  row.appendChild(who);
  row.appendChild(body);
  thread.appendChild(row);
  chatScroll();
  return body;
}

function chatStatus(text, cls){
  const el = $("#chatStatus");
  if (el) { el.textContent = text; el.className = "pill " + (cls || "muted"); }
}

function chatBusy(on){
  CHAT.busy = on;
  const send = $("#chatSend"), stop = $("#chatStop");
  if (send) send.disabled = on;
  if (stop) stop.hidden = !on;
}

/* An SSE frame is `id:` / `event:` / `data:` lines terminated by a blank line;
 * the data payload carries the whole run event, so only it is parsed. */
function sseFrames(buf, emit){
  let i;
  while ((i = buf.indexOf("\n\n")) >= 0) {
    const frame = buf.slice(0, i);
    buf = buf.slice(i + 2);
    let data = null;
    frame.split("\n").forEach((line) => {
      if (!line.startsWith("data:")) return;
      const raw = line.slice(5).trim();
      if (raw) { try { data = JSON.parse(raw); } catch(e) { data = null; } }
    });
    if (data && data.type) emit(data);
  }
  return buf;
}

async function chatStream(text, body){
  const ctrl = new AbortController();
  CHAT.ctrl = ctrl;
  const res = await fetch("/stream/command", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
    body: JSON.stringify({ text: text, platform: "dashboard", user_id: "control-room", stream: true }),
    signal: ctrl.signal,
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    throw new Error("stream " + res.status + (detail ? ": " + detail.slice(0, 180) : ""));
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "", painted = "", finalText = "";
  const emit = (ev) => {
    const d = ev.data || {};
    if (ev.type === "llm_delta") {
      painted += String(d.text || d.delta || "");
      body.textContent = painted;
      chatScroll();
    } else if (ev.type === "agent_response") {
      finalText = String(d.text || "");
      if (finalText) { body.textContent = finalText; chatScroll(); }
    } else if (ev.type === "tool_call") {
      chatRow("sys", "tool", String(d.name || d.tool || "call"));
    } else if (ev.type === "run_error" || ev.type === "agent_failed" || ev.type === "stream_timeout") {
      throw new Error(String(d.message || d.error || d.status || ev.type));
    }
  };
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buf = sseFrames(buf + dec.decode(chunk.value, { stream: true }), emit);
  }
  const answer = finalText || painted;
  if (answer) return answer;
  throw new Error("the run closed without an agent_response");
}

function chatFail(body, msg){
  const row = body && body.parentElement;
  if (row) {
    row.classList.add("is-error");
    body.classList.remove("pending");
    body.textContent = "turn failed: " + msg + " — nothing invented.";
  }
  chatStatus("failed", "err");
}

async function sendChat(){
  const input = $("#chatInput");
  const text = ((input && input.value) || "").trim();
  if (!text || CHAT.busy) return;
  chatBusy(true);
  chatRow("you", "you", text);
  const body = chatRow("hermus", "hermus", "");
  if (body) body.classList.add("pending");
  if (input) input.value = "";
  chatStatus("thinking", "warn");
  try {
    await chatStream(text, body || {});
    chatStatus("reply", "ok");
  } catch (e) {
    if (e && e.name === "AbortError") {
      if (body) { body.classList.remove("pending"); if (!body.textContent) body.textContent = "(stopped)"; }
      chatStatus("stopped", "muted");
    } else if (typeof sendCommand === "function") {
      chatStatus("stream down, inline retry", "warn");
      try {
        const r = await sendCommand({ text: text });
        if (r && r.ok) {
          if (body) { body.classList.remove("pending"); body.textContent = r.response || "(empty reply)"; }
          chatStatus("reply", "ok");
        } else {
          chatFail(body, (r && r.error) || "no reply from /command");
        }
      } catch (e2) {
        chatFail(body, e2.message || String(e2));
      }
    } else {
      chatFail(body, (e && e.message) || String(e));
    }
  } finally {
    if (body) body.classList.remove("pending");
    CHAT.ctrl = null;
    chatBusy(false);
  }
}

const chatForm = $("#chatForm");
if (chatForm) chatForm.addEventListener("submit", (ev) => { ev.preventDefault(); sendChat(); });
const chatInput = $("#chatInput");
if (chatInput) chatInput.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendChat(); }
});
const chatStopBtn = $("#chatStop");
if (chatStopBtn) chatStopBtn.addEventListener("click", () => { if (CHAT.ctrl) CHAT.ctrl.abort(); });

// ---------- COMPUTER ----------
async function refreshComputer(){
  try {
    const { j: st } = await getJSON("/computer/status");
    const cells = $("#compStatus");
    cells.innerHTML = kpi(st.active ? "active" : "halted", "control") + kpi(String(st.stats?.running ?? st.running ?? 0), "running")
      + kpi(String(st.stats?.total ?? 0), "total tasks") + kpi(String((st.recorder||{}).running ? "on" : "off"), "recorder");
    const body = $("#compTasks tbody");
    const tasks = st.tasks || st.list || [];
    body.innerHTML = tasks.slice(0, 30).map((t) => `<tr><td><code>${esc(t.task_id || t.id || "")}</code></td><td><span class="status s-${esc(t.status)}">${esc(t.status)}</span></td><td>${esc(t.updated_at || "")}</td><td>${esc(t.goal || t.task || t.summary || "")}</td></tr>`).join("")
      || stateRowHtml({ empty: "no computer tasks" }, 4);
  } catch(e){
    $("#compStatus").innerHTML = stateHtml({ error: e.envelope || { message: e.message } },
      { label: "computer", onRetryId: "computer" });
  }
}
RETRY_ACTIONS.computer = refreshComputer;
function compRun(){
  const out = $("#compOut");
  const task = $("#compTask").value.trim();
  if (!task) { out.innerHTML = '<div class="note">enter an objective first</div>'; return; }
  out.innerHTML = '<div class="note">submitting ' + esc(task) + '…</div>';
  fetch("/computer/run", { method:"POST", headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ task: task }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>ok: <span class="e">' + esc(d.ok ?? d.success) + '</span> task <code>' + esc(d.task_id || d.run_id || "") + '</code></div>'; refreshComputer(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">run failed: ' + esc(e.message) + '</div>'; });
}
function compStop(){
  const out = $("#compOut");
  fetch("/computer/control/emergency-stop", { method:"POST", headers:{ "Content-Type":"application/json" }, body:"{}" })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>emergency stop: <span class="e">' + esc(d.ok ?? d.halted) + '</span></div>'; refreshComputer(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">stop failed: ' + esc(e.message) + '</div>'; });
}

// ---------- REMOTE ----------
async function refreshRemote(){
  try {
    const { j: st } = await getJSON("/remote/status");
    $("#remoteStatus").innerHTML = kpi(esc(String(st.approval?.enabled ?? st.enabled ?? false)), "approval gate")
      + kpi(esc(String(st.control?.active ?? st.active ?? false)), "active")
      + kpi(esc(String((st.control?.halted ?? st.halted) ? "halted" : "round")), "state");
    const { j: ap } = await getJSON("/remote/approvals");
    const hist = ap.history || [];
    const pending = (hist || []).filter((x) => x && x.status === "pending");
    const ul = $("#approvals");
    if (!pending.length) { ul.innerHTML = '<li class="hint">no pending approvals</li>'; return; }
    ul.innerHTML = pending.map((p) => `<li class="row">
      <span><code>${esc(p.prompt_id || p.id || "")}</code> · ${esc(p.summary || p.action || p.description || p.kind || "")}</span>
      <span><button class="ghost" data-act="approve" data-id="` + esc(p.prompt_id || p.id || "") + `">approve</button>
      <button class="ghost" data-act="reject" data-id="` + esc(p.prompt_id || p.id || "") + `">reject</button></span>
    </li>`).join("");
    ul.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => remoteAct(b.dataset.act, b.dataset.id)));
  } catch(e){
    $("#remoteStatus").innerHTML = stateHtml({ error: e.envelope || { message: e.message } },
      { label: "remote", onRetryId: "remote" });
  }
}
RETRY_ACTIONS.remote = refreshRemote;
function remoteAct(act, id){
  const out = $("#remoteOut");
  fetch("/remote/" + act, { method:"POST", headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ prompt_id: id, by: "control" }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>' + esc(act) + ' ' + esc(id) + ': <span class="e">' + esc(d.ok ?? d.success ?? d.result ?? "done") + '</span></div>'; refreshRemote(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">' + esc(act) + ' failed: ' + esc(e.message) + '</div>'; });
}

// ---------- SAFETY ----------
function splitCsv(v){ return String(v || "").split(",").map((x)=>x.trim()).filter(Boolean); }
function mdLite(md){
  return esc(md || "")
    .replace(/^### (.*)$/gm, "<b>$1</b>")
    .replace(/^## (.*)$/gm, "<b class='e'>$1</b>")
    .replace(/^# (.*)$/gm, "<b class='e'>$1</b>")
    .replace(/\n/g, "<br>");
}
function safetyEventLabel(e){
  const cmd = e.command || e.type || "event";
  const args = e.args_redacted || {};
  const parts = [];
  if (args.id) parts.push(args.id);
  if (args.tool) parts.push(args.tool);
  if (args.power) parts.push(args.power);
  if (args.state) parts.push(args.state);
  if (e.mission_id) parts.push("mission " + e.mission_id);
  if (e.error_code) parts.push(e.error_code);
  return parts.length ? cmd + " · " + parts.join(" · ") : cmd;
}
async function refreshSafetyEvents(){
  const feed = $("#safetyEvents");
  if (!feed) return;
  try {
    const { j } = await getJSON("/safety/events?limit=80");
    const rows = j.events || [];
    feed.innerHTML = rows.slice().reverse().map((e) => `<div><span class="t">${esc(e.timestamp || "")}</span> <span class="e">${esc(safetyEventLabel(e))}</span> <span class="tag">${esc(e.status || "")}</span></div>`).join("")
      || '<div class="note">no safety events recorded yet</div>';
  } catch(e){ feed.innerHTML = stateHtml({ error: e.envelope || { message: e.message } },
    { label: "safety events", onRetryId: "safetyEvents" }); }
}
async function refreshSafetyReport(){
  const feed = $("#safetyReport");
  if (!feed) return;
  feed.innerHTML = '<div class="note">generating safety report…</div>';
  try {
    const { j } = await getJSON("/safety/report?format=markdown");
    feed.innerHTML = mdLite(j.markdown || JSON.stringify(j, null, 2));
  } catch(e){ feed.innerHTML = stateHtml({ error: e.envelope || { message: e.message } },
    { label: "safety report", onRetryId: "safetyReport" }); }
}
async function refreshSafety(){
  try {
    const { j: policy } = await getJSON("/red-lines/policy");
    const rules = policy.rules || [];
    $("#safetySummary").innerHTML = kpi(rules.length, "red lines") + kpi("green", "safe actions") + kpi("yellow", "scoped approvals") + kpi("red", "blocked");
    const body = $("#redLines tbody");
    body.innerHTML = rules.map((r) => `<tr><td>${esc(r.id)}</td><td>${esc(r.title)}</td><td>${esc(r.rule)}</td></tr>`).join("")
      || '<tr><td colspan="3" class="hint">policy unavailable</td></tr>';
  } catch(e){ $("#safetySummary").innerHTML = '<div class="kpi"><div class="v">—</div><div class="l">red-line policy unavailable</div></div>'; }
  try {
    const { j: bundles } = await getJSON("/permissions/bundles");
    const rows = bundles.bundles || [];
    const body = $("#approvalBundles tbody");
    body.innerHTML = rows.map((b) => `<tr>
      <td><code>${esc(b.id)}</code></td><td>${esc(b.mission_id || "")}</td><td>${esc((b.request_ids||[]).length)}</td><td>${esc(b.title || "")}</td>
      <td><button class="ghost" data-bundle-act="approve" data-bundle-id="${esc(b.id)}">approve all</button>
      <button class="ghost" data-bundle-act="deny" data-bundle-id="${esc(b.id)}">deny all</button></td>
    </tr>`).join("") || '<tr><td colspan="5" class="hint">no pending approval bundles</td></tr>';
    body.querySelectorAll("button[data-bundle-id]").forEach((b) => b.addEventListener("click", () => resolveBundle(b.dataset.bundleId, b.dataset.bundleAct)));
  } catch(e){ $("#approvalBundles tbody").innerHTML = `<tr><td colspan="5" class="hint">approval bundles unavailable: ${esc(e.message)}</td></tr>`; }
  try {
    const { j: pending } = await getJSON("/permissions/pending");
    const rows = pending.pending || [];
    const body = $("#pendingApprovals tbody");
    setPill("#pendingCount", "approvals: " + rows.length, rows.length ? "warn" : "ok");
    updateSafetyCore();
    body.innerHTML = rows.map((p) => `<tr>
      <td><code>${esc(p.id)}</code></td><td>${esc(p.tool)}</td><td>${esc((p.safety?.red_lines||[]).join(","))}</td>
      <td>${esc((p.suggested_resources||[]).join(", "))}</td><td>${esc(p.suggested_purpose || "")}</td><td>${esc(p.created_at || "")}</td>
      <td><button class="ghost" data-pending-act="approve" data-pending-id="${esc(p.id)}">approve</button>
      <button class="ghost" data-pending-act="deny" data-pending-id="${esc(p.id)}">deny</button></td>
    </tr>`).join("") || '<tr><td colspan="7" class="hint">no pending yellow actions</td></tr>';
    body.querySelectorAll("button[data-pending-id]").forEach((b) => b.addEventListener("click", () => resolvePending(b.dataset.pendingId, b.dataset.pendingAct)));
  } catch(e){ setPill("#pendingCount", "approvals: ?", "warn"); updateSafetyCore(); $("#pendingApprovals tbody").innerHTML = `<tr><td colspan="7" class="hint">pending prompts unavailable: ${esc(e.message)}</td></tr>`; }
  try {
    const { j: grants } = await getJSON("/permissions/approvals");
    const rows = grants.approvals || [];
    const body = $("#approvalGrants tbody");
    body.innerHTML = rows.map((g) => `<tr>
      <td><code>${esc(g.id)}</code></td><td>${esc(g.tool)}</td><td>${esc((g.red_lines||[]).join(","))}</td>
      <td>${esc((g.resources||[]).join(", "))}</td><td>${esc(g.purpose || "")}</td>
      <td>${esc(g.uses)}/${esc(g.max_uses ?? "∞")}</td><td>${esc(g.expires_at || "never")}</td>
      <td><button class="ghost" data-revoke="${esc(g.id)}">revoke</button></td>
    </tr>`).join("") || '<tr><td colspan="8" class="hint">no active scoped grants</td></tr>';
    body.querySelectorAll("button[data-revoke]").forEach((b) => b.addEventListener("click", () => revokeGrant(b.dataset.revoke)));
  } catch(e){ $("#approvalGrants tbody").innerHTML = `<tr><td colspan="8" class="hint">approvals unavailable: ${esc(e.message)}</td></tr>`; }
  try {
    const { j: ledger } = await getJSON("/capabilities/ledger");
    $("#capLedger").innerHTML = mdLite(ledger.markdown || "");
  } catch(e){ $("#capLedger").innerHTML = '<div class="note">ledger unavailable: ' + esc(e.message) + '</div>'; }
  try { await refreshCapabilityRegistry(); } catch(e){}
  try { await refreshSafetyEvents(); } catch(e){}
}
async function refreshCapabilityRegistry(){
  const feed = $("#capRegistry");
  if (!feed) return;
  try {
    const { j } = await getJSON("/capabilities/registry");
    const rows = j.capabilities || [];
    feed.innerHTML = rows.map((r) => `<div><span class="status s-${esc(r.status)}">${esc(r.status)}</span> <span class="e">${esc(r.name)}</span> ${esc(r.category || '')} ${r.activation_request_id ? 'activation <code>' + esc(r.activation_request_id) + '</code>' : ''}</div>`).join("") || '<div class="note">no registered capabilities yet</div>';
  } catch(e){ feed.innerHTML = '<div class="note">capability registry unavailable: ' + esc(e.message) + '</div>'; }
}
function setupCapability(){
  const power = $("#powerName").value.trim();
  const out = $("#capRegistry");
  if (!power) { out.innerHTML = '<div class="note">power name required</div>'; return; }
  fetch("/capabilities/registry/setup", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ name: power, write_proposal: true }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>setup proposal: <span class="e">' + esc(d.success) + '</span> status=' + esc(d.record?.status || '') + '</div><div><code>' + esc(d.record?.planning_command || '') + '</code></div>'; refreshCapabilityRegistry(); refreshSafetyEvents(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">setup failed: ' + esc(e.message) + '</div>'; });
}
function requestCapabilityActivation(){
  const power = $("#powerName").value.trim();
  const out = $("#capRegistry");
  if (!power) { out.innerHTML = '<div class="note">power name required</div>'; return; }
  fetch("/capabilities/registry/request-activation", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ name: power, reason: "dashboard activation request" }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>activation request: <code>' + esc(d.request?.id || '') + '</code> <span class="e">' + esc(d.success) + '</span></div>'; refreshSafety(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">activation request failed: ' + esc(e.message) + '</div>'; });
}
function createGrant(){
  const out = $("#grantOut");
  const title = $("#grantTitle").value.trim();
  if (!title) { out.innerHTML = '<div class="note">title required</div>'; return; }
  const lines = splitCsv($("#grantLines").value).map((x)=>parseInt(x,10)).filter((x)=>Number.isFinite(x));
  const payload = {
    title,
    tool: $("#grantTool").value.trim() || "*",
    red_lines: lines,
    resources: splitCsv($("#grantResources").value),
    purpose: $("#grantPurpose").value.trim(),
    ttl_minutes: parseInt($("#grantTtl").value || "0", 10) || null,
    max_uses: parseInt($("#grantUses").value || "0", 10) || null,
  };
  fetch("/permissions/approve", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(payload) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>grant: <code>' + esc(d.grant?.id || "") + '</code> <span class="e">' + esc(d.success) + '</span></div>'; refreshSafety(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">approval failed: ' + esc(e.message) + '</div>'; });
}
function revokeGrant(id){
  fetch("/permissions/revoke", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ id }) })
    .then((r)=>r.json()).then(()=>refreshSafety()).catch(()=>refreshSafety());
}
function addPower(){
  const out = $("#grantOut");
  const power = $("#powerName").value.trim();
  if (!power) { out.innerHTML = '<div class="note">power name required</div>'; return; }
  fetch("/capabilities/ledger/discover", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({
    power,
    use: $("#powerUse").value.trim(),
    risk: $("#powerRisk").value.trim(),
    needed_approval_setup: $("#powerApproval").value.trim(),
    status: "not_granted",
    source: "control",
  }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>recorded power <span class="e">' + esc(d.success) + '</span>' + (d.deduped ? ' already listed' : '') + '</div>'; refreshSafety(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">record power failed: ' + esc(e.message) + '</div>'; });
}
function proposePower(){
  const power = $("#powerName").value.trim();
  const out = $("#capLedger");
  if (!power) { out.innerHTML = '<div class="note">power name required</div>'; return; }
  out.innerHTML = '<div class="note">generating setup proposal…</div>';
  fetch("/capabilities/ledger/propose", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ power }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = mdLite(d.markdown || JSON.stringify(d, null, 2)); })
    .catch((e)=>{ out.innerHTML = '<div class="note">proposal failed: ' + esc(e.message) + '</div>'; });
}
function runPreflight(){
  const goal = $("#preflightGoal").value.trim();
  const out = $("#preflightOut");
  if (!goal) { out.innerHTML = '<div class="note">goal required</div>'; return; }
  out.innerHTML = '<div class="note">running pre-flight…</div>';
  fetch("/safety/preflight", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ goal, format:"markdown" }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = mdLite(d.markdown || JSON.stringify(d, null, 2)); refreshSafetyEvents(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">pre-flight failed: ' + esc(e.message) + '</div>'; });
}
function runDownloadsScan(){
  const out = $("#preflightOut");
  out.innerHTML = '<div class="note">running approved Downloads defensive scan…</div>';
  fetch("/local-defense/scan", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ path:"~/Downloads", purpose:"malware", max_files:500, save_report:true }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = mdLite(d.markdown || JSON.stringify(d, null, 2)); refreshSafetyEvents(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">scan failed: ' + esc(e.message) + '</div>'; });
}
function startDownloadsScanMission(){
  const out = $("#preflightOut");
  out.innerHTML = '<div class="note">creating gated Downloads scan mission…</div>';
  fetch("/local-defense/missions", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ path:"~/Downloads", purpose:"malware", max_files:500 }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>scan mission <code>' + esc(d.mission_id || '') + '</code>: <span class="e">' + esc(d.state || d.error) + '</span></div>'; refreshMissions(); refreshSafetyEvents(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">scan mission failed: ' + esc(e.message) + '</div>'; });
}
async function listScanReports(){
  const out = $("#preflightOut");
  out.innerHTML = '<div class="note">loading scan reports…</div>';
  try {
    const { j } = await getJSON("/local-defense/reports?limit=20");
    const rows = j.reports || [];
    out.innerHTML = rows.map((r) => `<div><a href="${esc(r.url)}" target="_blank"><code>${esc(r.name)}</code></a> ${esc(r.updated_at || '')} ${esc(r.size_bytes || '')} bytes</div>`).join("") || '<div class="note">no local defense scan reports yet</div>';
  } catch(e){ out.innerHTML = '<div class="note">scan reports unavailable: ' + esc(e.message) + '</div>'; }
}
function createPreflightApprovals(){
  const goal = $("#preflightGoal").value.trim();
  const out = $("#preflightOut");
  if (!goal) { out.innerHTML = '<div class="note">goal required</div>'; return; }
  out.innerHTML = '<div class="note">creating draft approval prompts…</div>';
  fetch("/safety/preflight/approvals", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ goal }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>draft prompts created: <span class="e">' + esc((d.created||[]).length) + '</span></div>' + (d.errors?.length ? '<div class="note">errors: ' + esc(JSON.stringify(d.errors)) + '</div>' : ''); refreshSafety(); refreshSafetyEvents(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">approval prompt creation failed: ' + esc(e.message) + '</div>'; });
}
function applySafetyExample(kind){
  const out = $("#grantOut");
  if (kind === "downloads_scan") {
    $("#grantTitle").value = "Allow Downloads malware scan";
    $("#grantTool").value = "local_folder_defensive_scan";
    $("#grantLines").value = "3";
    $("#grantResources").value = "~/Downloads";
    $("#grantPurpose").value = "malware";
    $("#grantTtl").value = "30";
    $("#grantUses").value = "3";
    out.innerHTML = '<div class="note">Prefilled scoped grant. Review, then click Approve scope if correct.</div>';
  } else if (kind === "local_network_scan") {
    $("#grantTitle").value = "Allow local network incident scan";
    $("#grantTool").value = "*";
    $("#grantLines").value = "4,8";
    $("#grantResources").value = "192.168.0.0/16,10.0.0.0/8";
    $("#grantPurpose").value = "incident_response";
    $("#grantTtl").value = "30";
    $("#grantUses").value = "5";
    out.innerHTML = '<div class="note">Prefilled local-network defensive scope. Narrow it before approving if needed.</div>';
  } else if (kind === "gmail_proposal") {
    $("#powerName").value = "Gmail delegated send";
    $("#preflightGoal").value = "Use Gmail delegated send to reply to approved emails";
    proposePower();
  } else if (kind === "wallet_proposal") {
    $("#powerName").value = "Agent wallet trading";
    $("#preflightGoal").value = "Trade from the isolated agent wallet under risk limits";
    proposePower();
  }
}
function resolveBundle(id, decision){
  fetch("/permissions/bundles/resolve", { method:"POST", headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ id, decision, ttl_minutes: decision === "approve" ? 30 : null, max_uses: decision === "approve" ? 1 : null, resume: decision === "approve", extra_steps: 8 }) })
    .then((r)=>r.json()).then((d)=>{ $("#grantOut").innerHTML = '<div>' + esc(decision) + ' bundle <code>' + esc(id) + '</code> <span class="e">' + esc(d.success) + '</span>' + (d.resume ? ' resume <span class="e">' + esc(d.resume.state || d.resume.error) + '</span>' : '') + '</div>'; refreshSafety(); refreshMissions(); })
    .catch((e)=>{ $("#grantOut").innerHTML = '<div class="note">resolve bundle failed: ' + esc(e.message) + '</div>'; });
}
function resolvePending(id, decision){
  fetch("/permissions/pending/resolve", { method:"POST", headers:{ "Content-Type":"application/json" },
    body: JSON.stringify({ id, decision, ttl_minutes: decision === "approve" ? 30 : null, max_uses: decision === "approve" ? 1 : null, retry: decision === "approve" }) })
    .then((r)=>r.json()).then((d)=>{ $("#grantOut").innerHTML = '<div>' + esc(decision) + ' pending <code>' + esc(id) + '</code> ' + (d.grant ? 'grant <code>' + esc(d.grant.id) + '</code>' : '') + (d.retry ? ' retry <span class="e">' + esc(d.retry.success) + '</span>' : '') + '</div>'; refreshSafety(); })
    .catch((e)=>{ $("#grantOut").innerHTML = '<div class="note">resolve failed: ' + esc(e.message) + '</div>'; });
}

// ---------- DOCTOR (generated panel) ----------
/* Doctor is no longer its own tab: it is the `doctor` panel of the generated
 * Systems console (core/console.py declares /doctor/status and /doctor/run).
 * console.js registers one retry action per panel, including this one — the
 * fallback below keeps the contract when that script has not loaded yet. */
function refreshDoctor(){
  if (window.HermusConsole) { window.HermusConsole.refreshPanel("doctor"); return; }
  const host = $("#consolePanels");
  if (host) host.innerHTML = '<div class="note">open the Systems tab to read the doctor panel.</div>';
}
RETRY_ACTIONS.doctor = refreshDoctor;

// ---------- SETTINGS ----------
/* The write surface: what an operator may change, read live from the gateway.
 *
 * A stored secret never comes back to the browser — /keys/list hands over the
 * server's own redacted preview, and that preview is the only key material this
 * panel can display, so "copy the key out of the UI" is impossible by omission.
 * Removal addresses an entry by provider + label, never by its value.
 *
 * Installing an update deliberately stays in the Systems console: one confirm
 * path per destructive action, rather than one per screen that mentions it. */
function envelopeError(res){
  const err = new Error((res && res.message) || ("http_" + ((res && res.status) || 0)));
  err.envelope = res;
  return err;
}

function healthPill(entry){
  // Custom APIs carry no health record at all; saying so beats a green pill.
  if (entry.custom) return '<span class="pill muted">no health record</span>';
  const status = entry.health_status || (entry.healthy ? "healthy" : "untested");
  const cls = entry.healthy ? "ok" : (entry.quarantined || /fail|error/.test(status) ? "err" : "muted");
  return `<span class="pill ${cls}">${esc(status)}</span>`;
}

async function refreshSettingsKeys(){
  const cells = $("#settingsKeysSummary");
  const body = document.querySelector("#settingsKeys tbody");
  try {
    const { j } = await getJSON("/keys/list");
    const pooled = j.llm_keys || {};
    const keys = Object.keys(pooled).reduce(
      (acc, provider) => acc.concat((pooled[provider] || []).map((k) => Object.assign({ provider }, k))), []);
    const apis = (j.custom_apis || []).map((a) => Object.assign({ provider: "custom", custom: true }, a));
    const rows = keys.concat(apis);
    const healthy = rows.filter((r) => r.healthy).length;
    const failing = rows.filter((r) => r.quarantined || /fail|error/.test(String(r.health_status || ""))).length;
    cells.innerHTML = kpi(String(j.total_llm_keys != null ? j.total_llm_keys : keys.length), "pooled keys")
      + kpi(String(healthy), "healthy") + kpi(String(failing), "failing")
      + kpi(String(j.total_custom_apis != null ? j.total_custom_apis : apis.length), "custom APIs");
    body.innerHTML = rows.map((k) => `<tr>
      <td>${esc(k.provider)}</td>
      <td>${esc(k.name || "")}</td>
      <td><code>${esc(k.preview == null ? "—" : k.preview)}</code></td>
      <td>${healthPill(k)}</td>
      <td>${esc(k.models_count != null ? k.models_count : "—")}</td>
      <td>${esc(k.rpm_limit != null ? k.rpm_limit : "—")}</td>
      <td><button class="ghost" data-credential-kind="${k.custom ? "api" : "key"}" data-credential-provider="${esc(k.provider)}" data-credential-ref="${esc(k.name || k.id || "")}">remove</button></td>
    </tr>`).join("") || stateRowHtml({ empty: "nothing in the pool yet" }, 7);
    body.querySelectorAll("button[data-credential-kind]").forEach((b) => b.addEventListener("click", () => removeCredential(b.dataset.credentialKind, b.dataset.credentialProvider, b.dataset.credentialRef)));
  } catch(e){
    body.innerHTML = stateRowHtml({ error: e.envelope || { message: e.message } }, 7, { label: "key pool", onRetryId: "settings" });
  }
}

function removeCredential(kind, provider, ref){
  const out = $("#settingsAddOut");
  if (!ref) { out.innerHTML = '<div class="note">that entry has no label or id, so there is nothing to address</div>'; return; }
  // A key is removed by its stored label; the value never travels to the browser.
  const url = kind === "api" ? "/custom-apis/remove" : "/keys/remove";
  const payload = kind === "api" ? { id: ref, name: ref } : { provider, name: ref, key: ref };
  out.innerHTML = '<div class="note">removing ' + esc(ref) + '…</div>';
  postJSON(url, payload).then((res) => {
    if (!res.ok) { out.innerHTML = ""; reportFailure("remove " + (kind === "api" ? "API" : "key"), envelopeError(res), () => removeCredential(kind, provider, ref)); return; }
    const d = res.data || {};
    if (d.success === false) { out.innerHTML = '<div class="note">refused: ' + esc(d.error || d.message || "the gateway said no") + '</div>'; return; }
    out.innerHTML = '<div>removed <code>' + esc(ref) + '</code></div>';
    refreshSettingsKeys();
  });
}

/* One POST path for the settings writes, so a refusal always surfaces the
 * gateway's own reason instead of a form that quietly kept its values. */
function postWithFeedback(what, payload, url, out, done){
  out.innerHTML = '<div class="note">sending ' + esc(what) + '…</div>';
  postJSON(url, payload).then((res) => {
    if (!res.ok) { out.innerHTML = ""; reportFailure(what, envelopeError(res), () => postWithFeedback(what, payload, url, out, done)); return; }
    const d = res.data || {};
    if (d.success === false || d.error) { out.innerHTML = '<div class="note">' + esc(what) + ' refused: ' + esc(d.error || d.message || "the gateway said no") + '</div>'; return; }
    out.innerHTML = '<div>' + esc(what) + ' accepted.</div>';
    done();
  });
}

function addSettingKey(){
  const provider = $("#addKeyProvider").value.trim();
  const key = $("#addKeyValue").value.trim();
  const label = $("#addKeyName").value.trim();
  const out = $("#settingsAddOut");
  if (!provider || !key) { out.innerHTML = '<div class="note">provider and key are both required</div>'; return; }
  const payload = { provider, key };
  if (label) payload.name = label;
  postWithFeedback("key", payload, "/keys/add", out, () => {
    // The typed secret leaves the field as soon as the gateway has it.
    $("#addKeyValue").value = "";
    refreshSettingsKeys();
  });
}

function addSettingApi(){
  const name = $("#addApiName").value.trim();
  const url = $("#addApiUrl").value.trim();
  const description = $("#addApiDescription").value.trim();
  const out = $("#settingsAddOut");
  if (!name || !url || !description) { out.innerHTML = '<div class="note">name, url and description are all required by the gateway</div>'; return; }
  postWithFeedback("custom API", { name, url, description }, "/custom-apis/add", out, refreshSettingsKeys);
}

async function refreshSettingsPool(){
  try {
    const { j } = await getJSON("/api/v1/agents/pool/config");
    const c = j.config || {};
    const put = (id, value) => { const el = $(id); if (el && value != null) el.value = String(value); };
    put("#poolMaxAgents", c.max_agents);
    put("#poolMaxConcurrent", c.max_concurrent);
    put("#poolIdleTimeout", c.idle_timeout);
    put("#poolCleanupInterval", c.cleanup_interval);
    setPill("#settingsPoolState", "pool: live", "ok");
  } catch(e){
    setPill("#settingsPoolState", "pool: unavailable", "err");
  }
}

function saveSettingsPool(){
  const out = $("#settingsAddOut");
  const limits = {
    max_agents: $("#poolMaxAgents").value.trim(),
    max_concurrent: $("#poolMaxConcurrent").value.trim(),
    idle_timeout: $("#poolIdleTimeout").value.trim(),
    cleanup_interval: $("#poolCleanupInterval").value.trim(),
  };
  const payload = {};
  const bad = [];
  Object.keys(limits).forEach((field) => {
    const raw = limits[field];
    if (!raw) return; // the route applies only the fields present, so blanks stay untouched
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 0) bad.push(field); else payload[field] = n;
  });
  if (bad.length) { out.innerHTML = '<div class="note">not a number: ' + esc(bad.join(", ")) + '</div>'; return; }
  if (!Object.keys(payload).length) { out.innerHTML = '<div class="note">nothing to save — every field is blank</div>'; return; }
  postWithFeedback("pool limits", payload, "/api/v1/agents/pool/config", out, () => {
    toast("Pool limits saved", "ok");
    refreshSettingsPool();
  });
}

async function refreshSettingsUpdate(){
  const cells = $("#settingsUpdate");
  const feed = $("#settingsUpdateFeed");
  feed.innerHTML = '<div class="note">checking the remote…</div>';
  try {
    const { j } = await getJSON("/update/check");
    const local = j.local || {};
    const remote = j.remote || {};
    // A failed check must not read as "up to date": the envelope for these
    // routes returns {"error": ...} with a 200, so the error is the state.
    if (j.error) {
      cells.innerHTML = kpi("unknown", "update");
      feed.innerHTML = '<div class="note">the gateway could not check: ' + esc(j.error) + '</div>';
      return;
    }
    const state = j.update_available ? "available" : (j.up_to_date ? "current" : "unknown");
    cells.innerHTML = kpi(state, "update") + kpi(local.short || "—", "local") + kpi(remote.short || "—", "remote");
    feed.innerHTML = '<div>local <code>' + esc(local.short || "?") + '</code> ' + esc(local.message || "") + ' (' + esc(local.date || "?") + ')</div>'
      + '<div>remote <code>' + esc(remote.short || "?") + '</code> ' + esc(remote.message || "") + ' · ' + esc(remote.author || "?") + '</div>'
      + (j.update_available ? '<div class="note">Install from the Systems console update panel.</div>' : "");
  } catch(e){
    cells.innerHTML = kpi("unknown", "update");
    feed.innerHTML = '<div class="note">update check failed: ' + esc(e.message) + '</div>';
  }
}

function refreshSettings(){
  refreshSettingsKeys();
  refreshSettingsPool();
  refreshSettingsUpdate();
}
RETRY_ACTIONS.settings = refreshSettings;

// ---------- wiring ----------
$("#replayBtn").addEventListener("click", () => replayTimeline($("#runId").value.trim()));
$("#cmdBtn").addEventListener("click", postCommand);
$("#identitySave").addEventListener("click", savePresenceIdentity);
$("#presenceGoalAdd").addEventListener("click", addPresenceGoal);
$("#presenceGoal").addEventListener("keydown", (e) => { if (e.key === "Enter") addPresenceGoal(); });
$("#presenceHeartbeat").addEventListener("click", sendPresenceHeartbeat);
$("#presenceCheckIn").addEventListener("click", requestPresenceCheckIn);
$("#missionPreflight").addEventListener("click", missionPreflight);
$("#missionStart").addEventListener("click", () => startMission(false));
$("#missionPlanBlocked").addEventListener("click", () => startMission(true));
$("#runId").addEventListener("keydown", (e) => { if (e.key === "Enter") replayTimeline($("#runId").value.trim()); });
$("#cmdName").addEventListener("keydown", (e) => { if (e.key === "Enter") postCommand(); });
$("#tmClear").addEventListener("click", () => { const f = $("#tmFeed"); f.innerHTML = ""; f.dataset.ready = ""; refreshTelemetry(true); });
$("#compRun").addEventListener("click", compRun);
$("#compStop").addEventListener("click", compStop);
$("#emergencyBtn").addEventListener("click", toggleEmergency);
$("#grantCreate").addEventListener("click", createGrant);
$("#powerAdd").addEventListener("click", addPower);
$("#powerPropose").addEventListener("click", proposePower);
$("#capRegistryRefresh").addEventListener("click", refreshCapabilityRegistry);
$("#capSetup").addEventListener("click", setupCapability);
$("#capActivationRequest").addEventListener("click", requestCapabilityActivation);
$("#safetyEventsRefresh").addEventListener("click", refreshSafetyEvents);
$("#safetyReportBtn").addEventListener("click", refreshSafetyReport);
$("#preflightRun").addEventListener("click", runPreflight);
$("#preflightApprovals").addEventListener("click", createPreflightApprovals);
$("#settingsKeyAdd").addEventListener("click", addSettingKey);
$("#settingsApiAdd").addEventListener("click", addSettingApi);
$("#settingsPoolSave").addEventListener("click", saveSettingsPool);
$("#settingsUpdateCheck").addEventListener("click", refreshSettingsUpdate);
$("#addKeyValue").addEventListener("keydown", (e) => { if (e.key === "Enter") addSettingKey(); });
$("#addApiUrl").addEventListener("keydown", (e) => { if (e.key === "Enter") addSettingApi(); });
$("#runDownloadsScan").addEventListener("click", runDownloadsScan);
$("#startDownloadsScanMission").addEventListener("click", startDownloadsScanMission);
$("#refreshScanReports").addEventListener("click", listScanReports);
document.querySelectorAll("button[data-safety-example]").forEach((b) => b.addEventListener("click", () => applySafetyExample(b.dataset.safetyExample)));

// ---------- voice-first (Jarvis) mode ----------
function vlog(kind, text){
  const feed = $("#voiceLog"); if (!feed) return;
  const note = feed.querySelector(".note"); if (note) note.remove();
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `<span class="tag">${esc(kind)}</span> ${esc(text)}`;
  feed.prepend(row);
  while (feed.children.length > 60) feed.lastChild.remove();
}
function vstate(label, cls){ const el = $("#voiceState"); if (el) { el.textContent = label; el.className = "tag " + (cls || ""); } }

async function refreshVoiceStatus(){
  try {
    const { j } = await getJSON("/voice/status");
    const caps = $("#voiceCaps");
    if (caps) caps.innerHTML =
      kpi(j.enabled ? "on" : "off", "voice mode") +
      kpi(j.ack_mode, "ack source") +
      kpi((j.tts && (j.tts.backend || j.tts.selected)) || "none", "tts backend") +
      kpi((j.stt && (j.stt.model || (j.stt.models && j.stt.models[0]))) || "none", "stt model") +
      kpi(j.queue_ready ? "ready" : "not started", "job queue");
    if (!j.enabled) vlog("voice", "voice is disabled on this server");
    _hfCfg = j.handsfree || null;
    const hf = _hfCfg || {};
    const wake = $("#hfWake");
    if (wake) {
      wake.textContent = hf.enabled
        ? (hf.wake_required ? "wake word: " + (hf.wake_word || "(none)") : "wake word: not required")
        : "hands-free: off";
    }
    const arm = $("#hfArm");
    if (arm) arm.disabled = !hf.enabled;
    if (!hf.enabled) hfMic("disabled");
  } catch(e){ vlog("voice", "status failed: " + e.message); }
}

let _hfCfg = null;
let _hf = null;
let _hfStream = null;
let _hfRunId = null;

function hfMic(label, cls){ const el = $("#hfMic"); if (el) { el.textContent = label; el.className = "tag " + (cls || ""); } }
function hfLevel(v){ const el = $("#hfLevel"); if (el) el.value = Math.min(1, (v || 0) * 8); }

async function armHandsFree(){
  if (_hf) { disarmHandsFree(); return; }
  const HC = window.HermusClient;
  if (!HC) { vlog("voice", "client script not loaded"); return; }
  if (!_hfCfg || !_hfCfg.enabled) {
    vlog("voice", "hands-free is off on this server");
    return;
  }
  const cfg = _hfCfg;
  let mic = null;
  const loop = HC.createVoiceLoop({
    onEvent: (type, data) => {
      const d = data || {};
      if (type === "level") { hfLevel(d.level); return; }
      if (type === "state") {
        vstate(d.state, d.state === "listening" ? "ok" : "warn");
        if (d.state === "listening") hfMic("listening", "ok");
        else if (d.state === "capturing") hfMic("recording", "warn");
        else if (d.state === "thinking") hfMic("transcribing", "warn");
        else if (d.state === "speaking") hfMic("answering", "warn");
        return;
      }
      if (type === "speech_start") vlog("mic", "speech detected");
      else if (type === "transcript") vlog("heard", d.text || "(nothing)");
      else if (type === "discarded") {
        vlog("ignored", d.reason === "wake_word"
          ? "not addressed to me: " + (d.text || "") : d.reason);
      }
      else if (type === "command") vlog("you", d.text);
      else if (type === "answer") vlog("hermus", d.answer || (d.ok ? "(done)" : "(failed)"));
      else if (type === "barge_in") vlog("barge-in", "you interrupted me");
      else if (type === "superseded") vlog("barge-in", "dropped a stale answer");
      else if (type === "error") vlog("error", (d.stage || "") + ": " + d.message);
    },
    capture: () => HC.startRecording({ stream: _hfStream }),
    transcribe: (blob) => HC.transcribeBlob(blob, { model: cfg.stt_model }),
    run: async (text) => {
      const res = await HC.sendCommand({ text });
      _hfRunId = (res && res.raw && (res.raw.run_id || res.raw.runId)) || null;
      return res;
    },
    speak: (text) => HC.speakText(text),
    stopSpeaking: () => HC.stopSpeaking(),
    // Best effort: the run id only exists once /command has started.
    cancel: () => {
      if (_hfRunId) { try { fetch("/run/cancel/" + encodeURIComponent(_hfRunId), { method: "POST" }); } catch(e){} }
    },
  }, {
    wakeWord: cfg.wake_word, wakeAliases: cfg.wake_aliases || [],
    wakeRequired: cfg.wake_required,
    silenceMs: cfg.silence_ms, speechMs: cfg.speech_ms,
    minUtteranceMs: cfg.min_utterance_ms, maxUtteranceMs: cfg.max_utterance_ms,
    bargeIn: cfg.barge_in,
  });

  try { mic = await HC.attachMic(loop); }
  catch(e){ vlog("mic", "microphone unavailable: " + e.message); hfMic("denied", "err"); return; }
  _hfStream = mic.stream;
  _hf = { loop, mic };
  loop.start();
  const btn = $("#hfArm");
  if (btn) btn.textContent = "Hands-free: ON — click to stop";
  hfMic("listening", "ok");
  vlog("hands-free", cfg.wake_required
    ? "armed — start with “" + cfg.wake_word + "”"
    : "armed — no wake word, everything you say is acted on");
}

function disarmHandsFree(){
  if (!_hf) return;
  try { _hf.loop.stop(); } catch(e){}
  try { _hf.mic.stop(); } catch(e){}
  _hf = null; _hfStream = null; _hfRunId = null;
  const btn = $("#hfArm");
  if (btn) btn.textContent = "Hands-free: off";
  hfMic("mic off");
  hfLevel(0);
  vstate("idle");
  vlog("hands-free", "disarmed — microphone closed");
}

let _rec = null;
async function startTalk(){
  if (!window.HermusClient) { vlog("voice", "client script not loaded"); return; }
  const HC = window.HermusClient;
  HC.stopSpeaking();
  try {
    _rec = HC.startRecording();
  } catch(e){ vlog("voice", "mic unavailable: " + e.message); return; }
  vstate("listening", "warn");
  $("#voiceMic").disabled = true; $("#voiceStop").disabled = false;
  vlog("mic", "listening…");
}
async function endTalk(){
  if (!_rec) return;
  const rec = _rec; _rec = null;
  rec.stop();
  $("#voiceMic").disabled = false; $("#voiceStop").disabled = true;
  vstate("transcribing");
  let blob;
  try { blob = await rec.promise; }
  catch(e){ vstate("idle"); vlog("mic", "recording failed: " + e.message); return; }
  await runVoice(() => window.HermusClient.postVoiceBlob(blob, voiceHandlers()));
}

function voiceHandlers(){
  const HC = window.HermusClient;
  return {
    onTranscript: (t) => { vlog("you", t || "(nothing heard)"); },
    onAck: (ack) => {
      vlog("hermus", (ack && ack.text) || "(no acknowledgment)");
      if (ack && !ack.spoken) vlog("tts", "no local clip — using browser voice (" + (ack.error || "unavailable") + ")");
      vstate("working");
    },
    onEvent: (type, data) => {
      if (type === "tool_call") vlog("tool", (data && data.name) || type);
      else if (type === "step_started") vlog("step", (data && (data.state || data.name)) || type);
    },
    onAnswer: (data) => {
      const text = (data && (data.text || data.answer)) || "";
      vlog("answer", text ? text.slice(0, 400) : "(empty)");
      vstate("speaking");
      setTimeout(() => vstate("idle"), 1200);
    },
    onError: (err) => { vlog("error", String((err && err.message) || err)); vstate("idle", "err"); },
  };
}

async function runVoice(fn){
  try { await fn(); }
  catch(e){ vlog("error", String((e && e.message) || e)); vstate("idle", "err"); }
}

function initVoice(){
  const mic = $("#voiceMic"), stop = $("#voiceStop"), send = $("#voiceSend"), box = $("#voiceText");
  if (!mic) return;
  mic.addEventListener("mousedown", startTalk);
  mic.addEventListener("touchstart", (e) => { e.preventDefault(); startTalk(); }, { passive: false });
  ["mouseup", "mouseleave"].forEach((ev) => mic.addEventListener(ev, () => { if (_rec) endTalk(); }));
  mic.addEventListener("touchend", (e) => { e.preventDefault(); if (_rec) endTalk(); }, { passive: false });
  stop.addEventListener("click", () => { if (_rec) endTalk(); });
  const typed = async () => {
    const text = (box.value || "").trim(); if (!text) return;
    box.value = "";
    vlog("you", text);
    vstate("transcribing");
    await runVoice(() => window.HermusClient.sayText(text, voiceHandlers()));
  };
  send.addEventListener("click", typed);
  box.addEventListener("keydown", (e) => { if (e.key === "Enter") typed(); });
  const arm = $("#hfArm");
  if (arm) arm.addEventListener("click", armHandsFree);
  refreshVoiceStatus();
}

// ---------- FLEET ROSTER ----------
async function refreshAgents() {
  try {
    const { j } = await getJSON("/api/fleet/agents");
    const agents = j.agents || [];
    const totalAgents = agents.length;
    const live = agents.filter((a) => ["idle", "working", "thinking", "blocked", "paused"].includes(a.state));
    const done = agents.reduce((s, a) => s + ((a.stats && a.stats.tasks_done) || 0), 0);
    const toks = agents.reduce((s, a) => s + ((a.stats && a.stats.tokens) || 0), 0);
    const strip = $("#fleetSummary");
    if (strip) {
      strip.innerHTML = kpi(live.length, "live agents") + kpi(totalAgents, "total agents") + kpi(done, "tasks done") + kpi(toks, "tokens");
    }
    const grid = $("#agentRosterGrid");
    if (!grid) return;
    if (!agents.length) {
      grid.innerHTML = "";
      const empty = $("#rosterEmpty");
      if (empty) empty.hidden = false;
      return;
    }
    const empty = $("#rosterEmpty");
    if (empty) empty.hidden = true;
    const search = ($("#rosterSearch") && $("#rosterSearch").value || "").toLowerCase();
    const stateF = ($("#rosterStateFilter") && $("#rosterStateFilter").value) || "";
    const rows = agents.filter((a) => {
      if (search && (a.name || "").toLowerCase().indexOf(search) < 0) return false;
      if (stateF && a.state !== stateF) return false;
      return true;
    });
    const cls = { idle: "ok", working: "working", thinking: "thinking", blocked: "blocked", paused: "paused", sleeping: "sleeping", error: "err", destroyed: "err" };
    grid.innerHTML = rows.map((a) => {
      const st = a.stats || {};
      const last = a.last_activity ? new Date(a.last_activity).toLocaleString() : "\u2014";
      return "<article class=\"agent-card glass\" data-agent-id=\"" + esc(a.id || a.agent_id) + "\" role=\"listitem\">"
        + "<header class=\"agent-header\"><span class=\"agent-name\">" + esc(a.name)
        + "</span><span class=\"state-pill " + (cls[a.state] || "") + "\">" + esc(_fleetState(a.state)) + "</span></header>"
        + "<div class=\"agent-meta\">"
        + "<div class=\"agent-meta-row\"><span class=\"label\">Model</span><span class=\"value\">" + esc(a.provider) + " / " + esc(a.model) + "</span></div>"
        + "<div class=\"agent-meta-row\"><span class=\"label\">Key</span><span class=\"value\">" + esc(a.key_name || "auto") + "</span></div>"
        + "<div class=\"agent-meta-row\"><span class=\"label\">Skills</span><span class=\"value\">" + esc((a.skills || []).join(", ")) + "</span></div>"
        + "<div class=\"agent-meta-row\"><span class=\"label\">Last Active</span><span class=\"value\">" + esc(last) + "</span></div>"
        + "</div>"
        + "<div class=\"agent-stats\">"
        + "<span class=\"stat\"><span class=\"v\">" + (st.tasks_done || 0) + "</span><span class=\"l\">done</span></span>"
        + "<span class=\"stat\"><span class=\"v\">" + (st.tasks_failed || 0) + "</span><span class=\"l\">failed</span></span>"
        + "<span class=\"stat\"><span class=\"v\">" + (st.tokens || 0) + "</span><span class=\"l\">tokens</span></span>"
        + "</div>"
        + fleetAgentActions(a) + "</article>";
    }).join("");
    grid.querySelectorAll("button[data-run-agent]").forEach((b) => b.addEventListener("click", () => runAgentTask(b.dataset.runAgent)));
    grid.querySelectorAll("button[data-pause-agent]").forEach((b) => b.addEventListener("click", () => pauseAgent(b.dataset.pauseAgent)));
    grid.querySelectorAll("button[data-resume-agent]").forEach((b) => b.addEventListener("click", () => resumeAgent(b.dataset.resumeAgent)));
    grid.querySelectorAll("button[data-cancel-agent]").forEach((b) => b.addEventListener("click", () => cancelAgentTask(b.dataset.cancelAgent)));
    grid.querySelectorAll("button[data-dismiss-agent]").forEach((b) => b.addEventListener("click", () => dismissAgent(b.dataset.dismissAgent)));
  } catch (e) {
    const grid = $("#agentRosterGrid");
    if (grid) grid.innerHTML = stateHtml({ error: (e.envelope || { message: e.message }) }, { label: "agents", onRetryId: "agents" });
  }
}
function _fleetState(s) {
  const labels = { idle: "idle", working: "working", thinking: "thinking", blocked: "blocked", paused: "paused", sleeping: "sleeping", error: "error", destroyed: "destroyed", spawning: "spawning" };
  return labels[s] || (s || "?");
}
function fleetAgentActions(a) {
  const id = esc(a.id || a.agent_id);
  let btns = "";
  if (a.state === "working" || a.state === "thinking") {
    btns = "<button class=\"btn ghost\" data-pause-agent=\"" + id + "\" title=\"Pause\">&#9208;</button>"
      + "<button class=\"btn ghost\" data-cancel-agent=\"" + id + "\" title=\"Cancel Task\">&#9209;</button>";
  } else if (a.state === "paused") {
    btns = "<button class=\"btn primary\" data-resume-agent=\"" + id + "\" title=\"Resume\">&#9654; Resume</button>";
  } else if (a.state !== "destroyed") {
    btns = "<button class=\"btn primary\" data-run-agent=\"" + id + "\" title=\"Run Task\">&#9654; Task</button>"
      + "<button class=\"btn ghost\" data-pause-agent=\"" + id + "\" title=\"Pause\">&#9208;</button>";
  }
  if (a.state !== "destroyed") {
    btns += "<button class=\"btn ghost danger\" data-dismiss-agent=\"" + id + "\" title=\"Dismiss\">&#128465;</button>";
  }
  return "<div class=\"agent-actions\">" + btns + "</div>";
}
RETRY_ACTIONS.agents = refreshAgents;

// ---------- AGENT ACTIONS ----------
async function runAgentTask(agentId) {
  const task = prompt("Enter task for agent:");
  if (!task) return;
  try {
    await fetch(`/api/fleet/agents/${agentId}/task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task })
    }).then(r => r.json()).then(d => {
      toast(`Task assigned to agent ${d.agent_id}: ${d.state}`, d.ok ? "success" : "error");
      refreshAgents();
    });
  } catch(e) {
    toast("Failed to run task: " + e.message, "error");
  }
}

async function pauseAgent(agentId) {
  try {
    await fetch(`/api/fleet/agents/${agentId}/pause`, { method: "POST" })
      .then(r => r.json()).then(d => {
        toast(`Agent ${agentId} paused`, d.ok ? "success" : "error");
        refreshAgents();
      });
  } catch(e) {
    toast("Failed to pause agent: " + e.message, "error");
  }
}

async function resumeAgent(agentId) {
  try {
    await fetch(`/api/fleet/agents/${agentId}/resume`, { method: "POST" })
      .then(r => r.json()).then(d => {
        toast(`Agent ${agentId} resumed`, d.ok ? "success" : "error");
        refreshAgents();
      });
  } catch(e) {
    toast("Failed to resume agent: " + e.message, "error");
  }
}

async function cancelAgentTask(agentId) {
  try {
    await fetch(`/api/fleet/agents/${agentId}/task`, { method: "DELETE" })
      .then(r => r.json()).then(d => {
        toast(`Task cancelled for agent ${agentId}`, d.ok ? "success" : "error");
        refreshAgents();
      });
  } catch(e) {
    toast("Failed to cancel task: " + e.message, "error");
  }
}

async function dismissAgent(agentId) {
  const confirmed = confirm("Dismiss agent? This will permanently delete the agent and its memory.");
  if (!confirmed) return;
  try {
    await fetch(`/api/fleet/agents/${agentId}`, { method: "DELETE", body: JSON.stringify({ confirm: true }) })
      .then(r => r.json()).then(d => {
        toast(`Agent dismissed`, d.ok ? "success" : "error");
        refreshAgents();
      });
  } catch(e) {
    toast("Failed to dismiss agent: " + e.message, "error");
  }
}

// Broadcast to all agents
async function broadcastToAgents() {
  const message = prompt("Broadcast message to all agents:");
  if (!message) return;
  try {
    await fetch("/api/fleet/broadcast", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: message })
    }).then(r => r.json()).then(d => {
      toast("Broadcast sent to all agents", "success");
    });
  } catch(e) {
    toast("Failed to broadcast: " + e.message, "error");
  }
}

// Orchestrate a multi-agent goal
async function orchestrateAgents() {
  const goal = prompt("Enter goal for multi-agent orchestration:");
  if (!goal) return;
  try {
    await fetch("/api/fleet/orchestrate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal })
    }).then(r => r.json()).then(d => {
      toast("Orchestration started", "success");
      refreshAgents();
    });
  } catch(e) {
    toast("Failed to orchestrate: " + e.message, "error");
  }
}

// Broadcast button handler
function broadcastToAll() {
  broadcastToAgents();
}

// Orchestrate button handler
function orchestrate() {
  orchestrateAgents();
}
initVoice();

// ---------- FLEET WS LIVE FEED ----------
let fleetWs = null;
let fleetWsReconnectTimer = null;
function fleetWsUrl() {
  const t = gatewayToken ? "?token=" + encodeURIComponent(gatewayToken) : "";
  return ((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/fleet/ws/fleet") + t;
}

function initFleetWs() {
  if (fleetWs) { try { fleetWs.close(); } catch(e){} fleetWs = null; }
  try {
    fleetWs = new WebSocket(fleetWsUrl());
    fleetWs.onopen = () => { if ($("#fleetWsStatus")) $("#fleetWsStatus").textContent = "live"; };
    fleetWs.onclose = () => {
      fleetWs = null;
      if ($("#fleetWsStatus")) $("#fleetWsStatus").textContent = "disconnected";
      if (!fleetWsReconnectTimer) fleetWsReconnectTimer = setTimeout(initFleetWs, 5000);
    };
    fleetWs.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (!msg || !msg.type) return;
        // Let HUD listeners (and other surfaces) react without polling.
        try { document.dispatchEvent(new CustomEvent("hermes:fleet-event", { detail: msg })); } catch(e) {}
        if (msg.type === "snapshot") { applyFleetSnapshot(msg.data); return; }
        if (msg.type === "fleet.state_changed") handleFleetStateChange(msg);
        else if (msg.type === "fleet.broadcast") handleFleetBroadcast(msg);
        else if (msg.type === "fleet.result") handleFleetResult(msg);
        else if (msg.type === "fleet.agent_updated") handleFleetAgentUpdated(msg);
        else if (msg.type === "fleet.mission_opened") handleFleetMissionOpened(msg);
        else if (msg.type === "fleet.mission_terminated") handleFleetMissionTerminated(msg);
      } catch(e) {}
    };
  } catch(e) {}
}

function applyFleetSnapshot(data) {
  if (data && data.agents) {
    const agents = data.agents;
    const totalAgents = agents.length;
    const live = agents.filter(a => ["idle","working","thinking","blocked","paused"].includes(a.state));
    const done = agents.reduce((s, a) => s + ((a.stats && a.stats.tasks_done) || 0), 0);
    const toks = agents.reduce((s, a) => s + ((a.stats && a.stats.tokens) || 0), 0);
    const strip = $("#fleetSummary");
    if (strip) strip.innerHTML = kpi(live.length, "live agents") + kpi(totalAgents, "total agents")
      + kpi(done, "tasks done") + kpi(toks, "tokens");
    const ac = $("#agentCount");
    if (ac) ac.textContent = "agents: " + totalAgents;
  }
}

function handleFleetStateChange(msg) {
  const grid = $("#agentRosterGrid");
  if (!grid) return;
  const card = grid.querySelector('[data-agent-id="' + esc(msg.agent_id || "") + '"]');
  if (card) {
    const pill = card.querySelector(".state-pill");
    if (pill) {
      const st = msg.new_state || msg.state;
      pill.textContent = _fleetState(st);
      pill.className = "state-pill " + ({"idle":"ok","working":"working","thinking":"thinking","blocked":"blocked","paused":"paused","sleeping":"sleeping","error":"err","destroyed":"err"}[st] || "");
    }
    const nameEl = card.querySelector(".agent-name");
    if (nameEl && msg.agent_name) nameEl.textContent = esc(msg.agent_name);
  }
}

function handleFleetBroadcast(msg) {
  const feed = $("#busFeed");
  if (!feed) return;
  const row = document.createElement("div");
  row.className = "bus-row";
  row.innerHTML = '<span class="bus-ts">' + esc(msg.ts || "") + '</span>'
    + '<span class="bus-kind">broadcast</span>'
    + '<span class="bus-content">' + esc(msg.content || "") + '</span>';
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
  while (feed.children.length > 200) feed.removeChild(feed.firstChild);
}

function handleFleetResult(msg) {
  if (msg.needs_approval) {
    const grid = $("#agentRosterGrid");
    if (grid) {
      const card = grid.querySelector('[data-agent-id="' + esc(msg.agent_id || "") + '"]');
      if (card) { card.classList.add("approval-pending"); setTimeout(() => card.classList.remove("approval-pending"), 8000); }
    }
  }
  const feed = $("#busFeed");
  if (feed) {
    const row = document.createElement("div");
    row.className = "bus-row";
    row.innerHTML = '<span class="bus-ts">' + esc(msg.ts || "") + '</span>'
      + '<span class="bus-kind">result</span>'
      + '<span class="bus-content">task ' + esc(msg.task_id || "") + " → " + (msg.executed ? "done" : "blocked") + '</span>';
    feed.appendChild(row);
    feed.scrollTop = feed.scrollHeight;
    while (feed.children.length > 200) feed.removeChild(feed.firstChild);
  }
}

function handleFleetAgentUpdated(msg) {
  try { refreshAgents(); } catch(e) {}
}

function handleFleetMissionOpened(msg) {
  const feed = $("#busFeed");
  if (feed) {
    const row = document.createElement("div");
    row.className = "bus-row";
    row.innerHTML = '<span class="bus-ts">' + esc(msg.ts || "") + '</span>'
      + '<span class="bus-kind">mission</span>'
      + '<span class="bus-content">opened: ' + esc(msg.goal || msg.mission_id || "") + '</span>';
    feed.appendChild(row);
    feed.scrollTop = feed.scrollHeight;
    while (feed.children.length > 200) feed.removeChild(feed.firstChild);
  }
  try { refreshMissions(); } catch(e) {}
}

function handleFleetMissionTerminated(msg) {
  const feed = $("#busFeed");
  if (feed) {
    const row = document.createElement("div");
    row.className = "bus-row";
    row.innerHTML = '<span class="bus-ts">' + esc(msg.ts || "") + '</span>'
      + '<span class="bus-kind">mission</span>'
      + '<span class="bus-content">terminated: ' + esc(msg.mission_id || "") + ' (' + esc(msg.state || "") + ')</span>';
    feed.appendChild(row);
    feed.scrollTop = feed.scrollHeight;
    while (feed.children.length > 200) feed.removeChild(feed.firstChild);
  }
  try { refreshMissions(); } catch(e) {}
}


// ---------- SCREEN PANEL ----------
function screenWsUrl() {
  const t = gatewayToken ? "?token=" + encodeURIComponent(gatewayToken) : "";
  return ((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/fleet/ws/fleet/screen") + t;
}

function initScreenPanel() {
  const sel = $("#screenAgentSelect");
  if (sel && $("#agentRosterGrid")) {
    const cards = $("#agentRosterGrid").querySelectorAll("[data-agent-id]");
    const agents = [];
    cards.forEach(c => {
      const id = c.getAttribute("data-agent-id");
      const name = (c.querySelector(".agent-name") || c).textContent.trim();
      if (id && name) agents.push({ id, name });
    });
    if (agents.length) {
      sel.innerHTML = '<option value="">Select agent...</option>' + agents.map(a =>
        '<option value="' + esc(a.id) + '">' + esc(a.name) + '</option>').join("");
    }
  }
  const statusEl = $("#screenStatus");
  const placeholder = $("#screenPlaceholder");
  const canvas = $("#screenCanvas");
  const overlay = $("#screenOverlay");

  function connectScreen() {
    if (screenWs) { try { screenWs.close(); } catch(e){} screenWs = null; }
    const agentId = sel && sel.value || "";
    try {
      screenWs = new WebSocket(screenWsUrl());
      screenWs.onopen = () => {
        if (statusEl) { statusEl.textContent = "connecting..."; statusEl.className = "screen-status"; }
      };
      screenWs.onclose = () => {
        screenWs = null;
        if (statusEl) { statusEl.textContent = "disconnected"; statusEl.className = "screen-status"; }
        if (!screenWsReconnectTimer && agentId) screenWsReconnectTimer = setTimeout(connectScreen, 10000);
      };
      screenWs.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (!msg || !msg.type) return;
          if (msg.type === "frame") {
            if (msg.data) {
              if (canvas) {
                const img = new Image();
                img.onload = () => { canvas.width = img.width; canvas.height = img.height; canvas.getContext("2d").drawImage(img, 0, 0); };
                img.src = "data:image/jpeg;base64," + msg.data;
              }
              if (placeholder) placeholder.style.display = "none";
              if (statusEl) { statusEl.textContent = "live"; statusEl.className = "screen-status ok"; }
              if (overlay) overlay.style.display = "";
            } else {
              if (placeholder) { placeholder.style.display = ""; placeholder.querySelector("p").textContent = msg.message || "Screen capture not yet available"; }
              if (statusEl) { statusEl.textContent = "unavailable"; statusEl.className = "screen-status warn"; }
              if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
              if (overlay) overlay.style.display = "none";
            }
          }
        } catch(e) {}
      };
    } catch(e) {}
  }
  if (sel) sel.onchange = connectScreen;
  if (sel && sel.value) connectScreen();
}


// ---------- APPROVAL ATTENTION PANEL ----------
async function refreshApprovalsPanel() {
  const list = $("#approvalList");
  const badge = $("#approvalBadge");
  const panel = $("#approvalAttention");
  if (!list) return;
  try {
    const { j } = await getJSON("/permissions/pending");
    const pending = (j && j.pending) ? j.pending : [];
    const active = pending.filter(a => a.status === "pending" || a.status === "asked");
    if (badge) { badge.textContent = String(active.length); badge.className = "badge " + (active.length ? "warning" : ""); }
    if (panel) panel.hidden = active.length === 0;
    if (!active.length) { list.innerHTML = '<div class="approval-empty">No pending approvals — all clear.</div>'; return; }
    list.innerHTML = active.map(a => {
      const created = a.created_at ? new Date(a.created_at).toLocaleString() : "";
      return '<div class="approval-item glass">'
        + '<div class="approval-item-header">'
        + '<span class="approval-item-title">' + esc(a.title || "Approval required") + '</span>'
        + '<span class="approval-item-id">' + esc(a.id) + '</span>'
        + '<span class="approval-item-age">' + esc(created) + '</span>'
        + '</div>'
        + '<div class="approval-item-body">'
        + '<div class="approval-item-tool">Tool: ' + esc(a.tool || "") + '</div>'
        + (a.suggested_purpose ? '<div class="approval-item-purpose">Purpose: ' + esc(a.suggested_purpose) + '</div>' : "")
        + (a.suggested_resources && a.suggested_resources.length ? '<div class="approval-item-resources">Resources: ' + esc(a.suggested_resources.join(", ")) + '</div>' : "")
        + '</div>'
        + '<div class="approval-item-actions">'
        + '<button class="btn primary" data-approve-approval="' + esc(a.id) + '">Approve</button>'
        + '<button class="btn ghost" data-deny-approval="' + esc(a.id) + '">Deny</button>'
        + '</div>'
        + '</div>';
    }).join("");
    list.querySelectorAll("[data-approve-approval]").forEach(b => b.addEventListener("click", () => resolveApproval(b.getAttribute("data-approve-approval"), "approve")));
    list.querySelectorAll("[data-deny-approval]").forEach(b => b.addEventListener("click", () => resolveApproval(b.getAttribute("data-deny-approval"), "deny")));
  } catch(e) { list.innerHTML = '<div class="approval-empty">Unable to load pending approvals.</div>'; }
}

function resolveApproval(id, decision) {
  const list = $("#approvalList");
  if (list) list.innerHTML = '<div class="approval-empty">Resolving...</div>';
  try {
    const { j } = postJSON("/permissions/pending/resolve", { id, decision, ttl_minutes: 30 });
    if (j && j.success) {
      toast("Approval " + decision + ": " + esc(id), "success");
      refreshApprovalsPanel();
      try { refreshMissions(); } catch(e) {}
    } else {
      toast("Failed to " + decision + " approval: " + esc(j.message || j.error || ""), "error");
      refreshApprovalsPanel();
    }
  } catch(e) { toast("Error resolving approval: " + e.message, "error"); refreshApprovalsPanel(); }
}


// ---------- ONBOARDING WIZARD (skeleton) ----------
const ONBOARDING_STEPS = [
  {
    title: "Welcome to HERMUS Fleet",
    render: () => '<div class="wizard-step-text">'
      + '<p class="wizard-intro">Set up your first agent fleet in a few steps. You can skip any step — nothing here writes secrets.</p>'
      + '<div class="wizard-features">'
      + '<div class="wizard-feature"><span class="wizard-feature-icon">&#9889;</span><div><strong>Agent Pool</strong><br>Spawn persistent agents with provider/model presets.</div></div>'
      + '<div class="wizard-feature"><span class="wizard-feature-icon">&#128222;</span><div><strong>API Keys</strong><br>Add keys via the dashboard or CLI — never stored in the browser.</div></div>'
      + '<div class="wizard-feature"><span class="wizard-feature-icon">&#128279;</span><div><strong>Live Feed</strong><br>Watch fleet state changes in real time via WebSocket.</div></div>'
      + '</div>'
      + '<p class="wizard-note">This wizard does not write any secrets. API keys are added separately via POST /api/fleet/keys or the CLI.</p>'
      + '</div>'
  },
  {
    title: "Choose a Provider & Model",
    render: () => '<div class="wizard-field"><label>Provider</label><select id="onbProvider"><option value="">Loading...</option></select></div>'
      + '<div class="wizard-field" id="onbModelGroup" style="display:none"><label>Model</label><select id="onbModel"><option>Loading...</option></select></div>'
      + '<div class="wizard-note">Add API keys separately — this step does not touch your keys.</div>',
    afterRender: async () => {
      try {
        const { j } = await getJSON("/api/fleet/providers");
        const sel = document.getElementById("onbProvider");
        if (sel) sel.innerHTML = '<option value="">Select provider...</option>' + j.providers.map(p => '<option value="' + esc(p) + '">' + esc(p) + '</option>').join("");
      } catch(e) { const sel = document.getElementById("onbProvider"); if (sel) sel.innerHTML = '<option value="">— unavailable —</option>'; }
      document.getElementById("onbProvider").onchange = onbProviderChange;
    }
  },
  {
    title: "API Key (optional)",
    render: () => '<div class="wizard-field">'
      + '<p class="wizard-note">Add an API key for your provider. Optional — add keys later via POST /api/fleet/keys or the CLI.</p>'
      + '<div class="wizard-key-form">'
      + '<input type="text" id="onbKeyName" placeholder="Key name (e.g. groq-prod)" />'
      + '<input type="text" id="onbKeyProvider" placeholder="Provider (e.g. groq)" />'
      + '<input type="password" id="onbKeyValue" placeholder="API key — sent to server, never stored in browser" />'
      + '</div>'
      + '<p class="wizard-note">The key is sent to the gateway vault and is not persisted in the browser.</p>'
      + '</div>'
  },
  {
    title: "Ready to go",
    render: () => '<div class="wizard-step-text">'
      + '<p>Your fleet is ready. You can now:</p>'
      + '<ul class="wizard-ready-list">'
      + '<li>Spawn an agent from the <strong>Agents</strong> tab or <strong>+ Create Agent</strong>.</li>'
      + '<li>Watch live state changes in the <strong>Live Bus Feed</strong> panel.</li>'
      + '<li>Approve or deny pending actions from the <strong>Approvals</strong> panel.</li>'
      + '<li>Add more API keys anytime from <strong>Fleet &rarr; Keys</strong>.</li>'
      + '</ul>'
      + '<p class="wizard-note">Tip: run <strong>Spawn Demo Team</strong> to create a pre-configured team.</p>'
      + '<div class="wizard-actions">'
      + '<button class="btn primary" id="onbSpawnDemo">&#9889; Spawn Demo Team</button>'
      + '<button class="btn ghost" id="onbSkipDemo">Skip</button>'
      + '</div>'
      + '</div>',
    afterRender: () => {
      const spawnBtn = document.getElementById("onbSpawnDemo");
      if (spawnBtn) spawnBtn.onclick = spawnDemoTeam;
      const skipBtn = document.getElementById("onbSkipDemo");
      if (skipBtn) skipBtn.onclick = () => { closeOnboarding(); toast("Onboarding complete — your fleet is ready", "success"); };
    }
  }
];

let onbStep = 0;

function startOnboarding() {
  onbStep = 0;
  const wizard = $("#onboardingWizard");
  if (!wizard) return;
  wizard.hidden = false;
  renderOnboardingStep();
}

function closeOnboarding() {
  const wizard = $("#onboardingWizard");
  if (wizard) wizard.hidden = true;
  onbStep = 0;
}

function renderOnboardingStep() {
  const wizard = $("#onboardingWizard");
  if (!wizard || onbStep < 0 || onbStep >= ONBOARDING_STEPS.length) { closeOnboarding(); return; }
  const step = ONBOARDING_STEPS[onbStep];
  $("#wizardStep").textContent = "Step " + (onbStep + 1) + " of " + ONBOARDING_STEPS.length;
  $("#wizardContent").innerHTML = step.render();
  $("#wizardPrev").disabled = onbStep === 0;
  $("#wizardNext").textContent = onbStep === ONBOARDING_STEPS.length - 1 ? "Done" : "Next";
  if (step.afterRender) step.afterRender();
}

function onbProviderChange() {
  const sel = document.getElementById("onbProvider");
  const prov = sel && sel.value || "";
  const modelGroup = document.getElementById("onbModelGroup");
  const modelSel = document.getElementById("onbModel");
  if (!modelGroup || !modelSel) return;
  if (!prov) { modelGroup.style.display = "none"; return; }
  modelGroup.style.display = "";
  modelSel.innerHTML = '<option value="">Loading...</option>';
  fetch("/api/fleet/models?provider=" + encodeURIComponent(prov))
    .then(r => r.json())
    .then(d => { modelSel.innerHTML = '<option value="">Select model...</option>' + (d.models || []).map(m => '<option value="' + esc(m) + '">' + esc(m) + '</option>').join(""); })
    .catch(() => { modelSel.innerHTML = '<option value="">— unavailable —</option>'; });
}

function onbNext() {
  if (onbStep < ONBOARDING_STEPS.length - 1) { onbStep++; renderOnboardingStep(); }
  else { closeOnboarding(); toast("Onboarding complete — your fleet is ready", "success"); }
}

function onbPrev() { if (onbStep > 0) { onbStep--; renderOnboardingStep(); } }

function onbAddKey() {
  const name = (document.getElementById("onbKeyName") || {}).value.trim() || "";
  const provider = (document.getElementById("onbKeyProvider") || {}).value.trim() || "";
  const key = (document.getElementById("onbKeyValue") || {}).value || "";
  if (!name || !provider || !key) { toast("Key name, provider, and key are required", "error"); return; }
  postJSON("/api/fleet/keys", { name, provider, key })
    .then(d => { if (d.ok) { toast("Key '" + esc(name) + "' added to vault", "success"); closeOnboarding(); }
      else toast("Failed to add key: " + esc(d.message || d.error || ""), "error"); })
    .catch(e => toast("Error adding key: " + e.message, "error"));
}

async function spawnDemoTeam() {
  const btn = document.getElementById("onbSpawnDemo");
  if (btn) { btn.disabled = true; btn.textContent = "Spawning..."; }
  try {
    // Spawn a few demo agents with different personas
    const agents = [
      { name: "Researcher", persona: "You are a research specialist. Provide thorough, well-cited answers.", provider: "groq", model: "llama-3.3-70b" },
      { name: "Coder", persona: "You are a software engineer. Write clean, tested code with clear explanations.", provider: "groq", model: "llama-3.3-70b" },
      { name: "Planner", persona: "You are a strategic planner. Break down goals into actionable steps with clear priorities.", provider: "groq", model: "llama-3.3-70b" },
    ];
    for (const agent of agents) {
      await postJSON("/api/fleet/agents", agent);
    }
    toast("Demo team spawned — 3 agents created", "success");
    closeOnboarding();
    refreshAgents();
  } catch(e) {
    toast("Failed to spawn demo team: " + e.message, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "&#9889; Spawn Demo Team"; }
  }
}

function wireOnboardingButtons() {
  const prev = $("#wizardPrev");
  const next = $("#wizardNext");
  if (prev) prev.onclick = onbPrev;
  if (next) next.onclick = onbNext;
  document.addEventListener("click", (e) => {
    if (e.target && e.target.id === "onbAddKey") onbAddKey();
  });
}


// VRAM gauge — reads the real /api/v1/agents/vram/monitor probe. The UI never
// simulates success: when the host has no measurable VRAM (no torch/CUDA) the
// gauge says "n/a" instead of inventing a number.
function updateVRAMMonitor(){
  const fill = $("#vramFill");
  const percent = $("#vramPercent");
  const used = $("#vramUsed");
  const pill = $("#vramUsage");
  if (!fill || !percent || !used) return;
  requestJSON("/api/v1/agents/vram/monitor").then((res) => {
    const data = res.ok ? res.data : null;
    if (!data || data.available !== true || data.vram_percent == null) {
      const reason = (data && (data.reason || data.recommendation)) || (res.ok ? "unavailable" : "probe failed");
      fill.style.width = "0%";
      percent.textContent = "n/a";
      used.textContent = reason;
      setPill("#vramUsage", "VRAM: n/a", "muted");
      return;
    }
    const pct = data.vram_percent;
    fill.style.width = Math.min(100, pct) + "%";
    percent.textContent = pct + "%";
    used.textContent = (data.vram_used_gb != null ? data.vram_used_gb.toFixed(1) : "--") + "GB"
      + (data.vram_total_gb != null ? " / " + data.vram_total_gb.toFixed(0) + "GB" : "");
    const usedTxt = data.vram_used_gb != null ? data.vram_used_gb.toFixed(1) : "--";
    setPill("#vramUsage", "VRAM: " + usedTxt + "GB/" + pct + "%", pct > 80 ? "err" : pct > 60 ? "warn" : "ok");
  }).catch(() => {
    percent.textContent = "n/a";
    used.textContent = "probe failed";
    setPill("#vramUsage", "VRAM: offline", "err");
  });
}

// ---------------------------------------------------------------------------
// Sentinel: the dashboard watches itself so a human only looks when needed.
// Link health with honest backoff messaging, attention routing to the tabs
// that hold pending work, and a live readout of the fleet watchdog.
// ---------------------------------------------------------------------------
const SENTINEL = { linkFails: 0, attn: {} };

function setAttn(tab, on) {
  const btn = document.querySelector('nav [role=tab][data-tab="' + tab + '"]');
  if (!btn) return;
  btn.classList.toggle("attn", !!on);
  if (on && !SENTINEL.attn[tab]) toast("Attention needed in " + tab, "warn");
  if (!on && SENTINEL.attn[tab]) toast(tab + " attention cleared", "success");
  SENTINEL.attn[tab] = !!on;
}

function renderWatchdogPill(j) {
  const pill = $("#watchdogPill");
  if (!pill) return;
  if (!j || j.ok === false) {
    pill.textContent = "watchdog: " + ((j && j.reason) || "n/a");
    pill.className = "pill muted";
    return;
  }
  const acts = j.counters ? (j.counters.recovered || 0) + (j.counters.driven || 0) : 0;
  pill.textContent = "watchdog: " + (j.enabled ? "on" : "off") + " · " + acts + " acts";
  pill.className = "pill " + (j.enabled ? "ok" : "muted");
}

async function sentinelTick() {
  try {
    await getJSON("/api/v1/system/health");
    if (SENTINEL.linkFails >= 2) toast("link restored", "success");
    SENTINEL.linkFails = 0;
  } catch (e) {
    SENTINEL.linkFails++;
    if (SENTINEL.linkFails === 2) toast("link degraded — sentinel retrying with backoff", "error");
  }
  try {
    const { j } = await getJSON("/permissions/pending");
    const list = Array.isArray(j) ? j : (j && (j.pending || j.approvals)) || [];
    setAttn("safety", list.length > 0);
  } catch (e) {}
  try {
    const { j } = await getJSON("/missions");
    const ms = (j && j.missions) || (Array.isArray(j) ? j : []);
    setAttn("missions", ms.some((m) => m.state === "blocked" || m.state === "suspended"));
  } catch (e) {}
  try {
    const { j } = await getJSON("/api/fleet/watchdog");
    renderWatchdogPill(j);
  } catch (e) { renderWatchdogPill(null); }
}

function wireStatusTray() {
  const tray = $("#statusTray");
  if (!tray) return;
  document.addEventListener("click", (e) => {
    if (tray.open && !tray.contains(e.target)) tray.open = false;
  });
}

async function boot(){
  await refreshOverview();
  await refreshPresence();
  await refreshJobs();
  try { await refreshMissions(); } catch(e){}
  await refreshAgents();
  refreshTelemetry(true);
  openTmStream();
  initFleetWs();
  initScreenPanel();
  refreshApprovalsPanel();
  wireOnboardingButtons();
  wireStatusTray();
  sentinelTick();
  setInterval(sentinelTick, 8000);
  updateVRAMMonitor();
  setInterval(updateVRAMMonitor, 4000);
  try {
    const { j } = await getJSON("/api/fleet/agents");
    if (j && (!j.agents || !j.agents.length)) startOnboarding();
  } catch(e) {}
}
boot();
setInterval(() => { refreshOverview(); refreshPresence(); refreshJobs(); try { refreshMissions(); } catch(e){} try { refreshAgents(); } catch(e){} try { refreshTelemetry(false); } catch(e){} refreshApprovalsPanel(); }, 8000);
RETRY_ACTIONS.chat = refreshOverview;
RETRY_ACTIONS.agents = refreshAgents;
