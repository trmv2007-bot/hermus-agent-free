const $ = (s) => document.querySelector(s);
// When gateway auth is enabled, open /control?token=... once (or store it as
// hermus_gateway_token). Browser fetch cannot read the server-side environment,
// so attach that token to every API request without putting it into ordinary
// fetch URLs. SSE/WS helpers add it separately because those browser APIs do
// not support custom headers.
const tokenFromUrl = new URLSearchParams(location.search).get("token") || "";
let gatewayToken = tokenFromUrl;
if (!gatewayToken) {
  try { gatewayToken = localStorage.getItem("hermus_gateway_token") || ""; } catch(e) {};
}
if (gatewayToken) {
  window.__HERMUS_GATEWAY_TOKEN = gatewayToken;
  // Do not leave a query credential in the address bar/history after capturing it.
  if (tokenFromUrl) {
    try { history.replaceState(null, "", location.pathname + location.hash); } catch(e) {}
  }
}
if (gatewayToken && window.fetch) {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const opts = Object.assign({}, init || {});
    const headers = new Headers(input && input.headers ? input.headers : undefined);
    new Headers(opts.headers || {}).forEach((value, key) => headers.set(key, value));
    headers.set("X-Hermus-Token", gatewayToken);
    opts.headers = headers;
    return nativeFetch(input, opts);
  };
}
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

function getJSON(url){
  const t0 = Date.now();
  return fetch(url, { headers: { "Accept": "application/json" } }).then((r) => {
    const ms = Date.now() - t0;
    if (!r.ok) throw new Error(url + " -> " + r.status);
    return r.json().then((j) => ({ j, ms }));
  });
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
  selectTab(active ? active.dataset.tab : "overview", { noRefresh: true });
})();
function refreshTab(name){
  if (name === "jobs") refreshJobs();
  else if (name === "missions") refreshMissions();
  else if (name === "telemetry") refreshTelemetry(true);
  else if (name === "computer") refreshComputer();
  else if (name === "remote") refreshRemote();
  else if (name === "safety") refreshSafety();
  else if (name === "doctor") refreshDoctor();
  else if (name === "presence") refreshPresence();
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
    { label: "system health", onRetryId: "overview" }); }
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

// ---------- DOCTOR ----------
async function refreshDoctor(){
  try {
    const { j: d } = await getJSON("/doctor/status");
    $("#doctor").innerHTML = kpi(esc(String(d.engine_status || d.status || "-")), "engine")
      + kpi(esc(String(d.worst_severity || "-")), "worst") + kpi(String(d.finding_count ?? d.counts?.total ?? 0), "findings")
      + kpi(String(d.stuck?.length ?? 0), "stuck");
  } catch(e){
    $("#doctor").innerHTML = stateHtml({ error: e.envelope || { message: e.message } },
      { label: "doctor", onRetryId: "doctor" });
  }
}
RETRY_ACTIONS.doctor = refreshDoctor;
function docRun(){
  const out = $("#docOut");
  out.innerHTML = '<div class="note">running doctor…</div>';
  fetch("/doctor/run", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ use_llm:false, ask_internet:false }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>status: <span class="e">' + esc(d.status) + '</span> findings: <span class="e">' + esc(d.findings?.length ?? 0) + '</span></div>'; refreshDoctor(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">doctor failed: ' + esc(e.message) + '</div>'; });
}

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
$("#docRun").addEventListener("click", docRun);
$("#safetyEventsRefresh").addEventListener("click", refreshSafetyEvents);
$("#safetyReportBtn").addEventListener("click", refreshSafetyReport);
$("#preflightRun").addEventListener("click", runPreflight);
$("#preflightApprovals").addEventListener("click", createPreflightApprovals);
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
    if (!j.enabled) vlog("voice", "disabled — set HERMUS_VOICE_ENABLED=1");
    _hfCfg = j.handsfree || null;
    const hf = _hfCfg || {};
    const wake = $("#hfWake");
    if (wake) {
      wake.textContent = hf.enabled
        ? (hf.wake_required ? "wake word: " + (hf.wake_word || "(none)") : "wake word: not required")
        : "hands-free: needs HERMUS_VOICE_HANDSFREE=1";
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
    vlog("voice", "hands-free is off server-side — set HERMUS_VOICE_HANDSFREE=1");
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
initVoice();

async function boot(){
  await refreshOverview();
  await refreshPresence();
  await refreshJobs();
  try { await refreshMissions(); } catch(e){}
  refreshTelemetry(true);
  openTmStream();
}
boot();
setInterval(() => { refreshOverview(); refreshPresence(); refreshJobs(); try { refreshMissions(); } catch(e){} try { refreshTelemetry(false); } catch(e){} }, 8000);
RETRY_ACTIONS.overview = refreshOverview;
