const $ = (s) => document.querySelector(s);
// The gateway token is installed by the document bootstrap (an inline script
// that runs before this file). Do NOT redeclare `tokenFromUrl`/`gatewayToken`
// here: a duplicate top-level lexical declaration across classic scripts is a
// SyntaxError that aborts this entire module, leaving every panel dead.
const GW_TOKEN = window.__HERMUS_GATEWAY_TOKEN || "";
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

/* kpi(v,l) — one stat chip */
function kpi(v,l){ return `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`; }

/* Last-resort payload rendering: when an endpoint has no markdown formatter,
 * build a definition list of its scalar fields instead of dumping raw JSON. */
function valueLabel(v){
  if (v === true) return "yes";
  if (v === false) return "no";
  if (v == null || v === "") return "—";
  if (typeof v === "number") {
    // epoch seconds/ms and ISO stamps read as clock time, not raw floats
    if (v > 1e12) return new Date(v).toLocaleTimeString();
    if (v > 1e9) return new Date(v * 1000).toLocaleTimeString();
    return String(v);
  }
  const s = String(v);
  if (/^\d{4}-\d{2}-\d{2}T[\d:]/.test(s)) { const d = new Date(s); if (!isNaN(d)) return d.toLocaleTimeString(); }
  return s.length > 80 ? s.slice(0, 77) + "…" : s;
}
/* Ages read as ages: a queue row should say "2m ago", not a raw ISO stamp
 * or an epoch float. The exact timestamp moves into the tooltip so
 * correlation with the gateway log is one hover away. Unparseable values
 * pass through untouched. */
function parseStamp(v){
  const s = String(v == null ? "" : v).trim();
  if (!s) return NaN;
  let t = Date.parse(s);
  if (!t || isNaN(t)) {
    // owners are free to store epoch seconds (the job queue does) or epoch ms
    if (/^\d+(\.\d+)?$/.test(s)) {
      const n = Number(s);
      if (n > 1e12) t = n;
      else if (n > 1e9) t = n * 1000;
    }
  }
  return t;
}
function relTime(v){
  const s = String(v == null ? "" : v).trim();
  if (!s) return "—";
  const t = parseStamp(s);
  if (!t || isNaN(t)) return s.length > 24 ? s.slice(0, 21) + "…" : s;
  const sec = Math.max(0, (Date.now() - t) / 1000);
  if (sec < 60) return Math.floor(sec) + "s ago";
  if (sec < 3600) return Math.floor(sec / 60) + "m ago";
  if (sec < 86400) return Math.floor(sec / 3600) + "h ago";
  return Math.floor(sec / 86400) + "d ago";
}
function relCell(v){
  const s = String(v == null ? "" : v).trim();
  if (!s) return "—";
  const t = parseStamp(s);
  if (!t || isNaN(t)) return esc(s);
  return '<span title="' + esc(new Date(t).toLocaleString()) + '">' + esc(relTime(s)) + '</span>';
}
function humanSummary(d){
  if (d == null) return "";
  if (typeof d === "string") return esc(d);
  if (typeof d !== "object") return esc(String(d));
  const skip = new Set(["success", "trace_id", "request_id"]);
  const rows = Object.entries(d).filter(([, v]) => v != null && typeof v !== "object")
    .map(([k, v]) => `<div class="kv"><span class="k">${esc(k.replace(/_/g, " "))}</span><span class="v" title="${esc(String(v)).slice(0, 200)}">${esc(valueLabel(v))}</span></div>`);
  const extra = Object.entries(d).filter(([k, v]) => !skip.has(k) && v && typeof v === "object").length;
  return rows.length
    ? rows.join("") + (extra ? `<div class="note">${extra} detail group${extra > 1 ? "s" : ""} in the response — ask for a report for the full text.</div>` : "")
    : '<div class="note">done</div>';
}

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
  if (name === "jobs") { refreshJobs(); refreshRemote(); }
  else if (name === "missions") refreshMissions();
  else if (name === "telemetry") refreshTelemetry(true);
  else if (name === "computer") refreshComputer();
  else if (name === "remote") refreshRemote();
  else if (name === "safety") refreshSafety();
  else if (name === "approvals") { refreshSafety(); refreshRemote(); }
  else if (name === "capabilities") { refreshCaps(); refreshSafety(); }
  else if (name === "memory") refreshMemory();
  else if (name === "systems") { if (window.HermusConsole) window.HermusConsole.refresh(); }
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
async function refreshCaps(){
  try { const { j } = await getJSON("/api/v1/system/capabilities"); renderCaps(j); }
  catch(e){ $("#caps").innerHTML = '<div class="kpi"><div class="v">—</div><div class="l">capability summary unavailable</div></div>'; }
}

// ---------- MEMORY (real numbers, real recall, real skills) ----------
// The tab used to promise "skill memory" while actually hosting the
// capability stats — the capability summary now lives on its own tab and the
// Memory tab reads /memory2/stats: totals by kind, index health, the skill
// forge's registry, and a recall box over the live store.
function renderMemoryStats(data){
  const m = (data && data.memory) || {};
  const kinds = m.by_kind || {};
  let html;
  if (m.error) {
    html = kpi("—", "memory");
    $("#memoryIndexNote").textContent = "memory stats unavailable: " + m.error;
  } else {
    html = kpi(String(m.total ?? 0), "memories")
      + kpi(String(kinds.episodic ?? 0), "episodic")
      + kpi(String(kinds.semantic ?? 0), "semantic")
      + kpi(String(kinds.procedural ?? 0), "procedural")
      + kpi(String(m.access_events ?? 0), "recall hits");
    const idx = m.recall_index || {};
    $("#memoryIndexNote").textContent = idx.available
      ? "Recall index live: FTS5 " + (idx.fts5 ? "on" : "off") + " · vectors via " + (idx.vector_backend || "—") + " · " + String(idx.vectors_indexed ?? 0) + " indexed"
      : "Recall index not built yet — recall falls back to the typed store.";
  }
  $("#memoryKpis").innerHTML = html;
  const s = (data && data.skills) || {};
  const host = $("#skillList");
  if (s.error) { host.innerHTML = '<li class="note">skill forge unavailable: ' + esc(s.error) + '</li>'; return; }
  const rows = s.skills || [];
  host.innerHTML = rows.length
    ? rows.map((sk) => '<li><div><strong>' + esc(sk.name) + '</strong>'
        + (sk.summary ? '<div class="note">' + esc(sk.summary) + '</div>' : '')
        + '</div></li>').join("")
    : '<li class="note">No skills distilled yet — after a run proves itself repeatable, its skill appears here.</li>';
}
async function refreshMemory(){
  try { const { j } = await getJSON("/memory2/stats"); renderMemoryStats(j); }
  catch(e){
    $("#memoryKpis").innerHTML = stateHtml({ error: e.envelope || { message: e.message } }, { label: "memory stats", onRetryId: "memory" });
  }
}
async function recallMemory(){
  const input = $("#memoryRecallQuery");
  const out = $("#memoryRecallOut");
  const q = (input && input.value || "").trim();
  if (!q) { out.innerHTML = '<div class="note">Type what to look for first.</div>'; return; }
  out.innerHTML = '<div class="note">searching memories…</div>';
  // requestJSON (not getJSON) — recall is a POST and getJSON sends GET only.
  const res = await requestJSON("/memory2/recall", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify({ query: q, limit: 8 }),
  });
  if (!res.ok) { out.innerHTML = '<div class="note">recall failed: ' + esc(res.message || ("HTTP " + res.status)) + '</div>'; return; }
  const rows = (res.data || {}).results || [];
  out.innerHTML = rows.length
    ? rows.map((r) => {
        const kind = esc(String(r.kind || r.type || "memory"));
        const when = r.created_at || r.at || "";
        const text = esc(String(r.content || r.text || "").slice(0, 400));
        return '<div><span class="tag">' + kind + '</span> <span class="e">' + (when ? esc(presenceTime(when)) : "") + '</span><div class="note">' + text + '</div></div>';
      }).join("")
    : '<div class="note">Nothing matched “' + esc(q) + '”. Memories appear as turns run; try another wording.</div>';
}
async function refreshOverview(){
  // Readiness first: the connection pill must not wait behind the slowest
  // capability probe. bootstrap.doctor() can take seconds on a cold machine.
  try { await refreshReadiness(); } catch(e){}
  try { const { j, ms } = await getJSON("/api/v1/system/health"); renderHealth(j); rtt.textContent = ms + "ms"; }
  catch(e){ $("#health").innerHTML = stateHtml({ error: e.envelope || { message: e.message } },
    { label: "system health", onRetryId: "overview" }); }
  // The capability summary KPIs (#caps) now live on the Capabilities tab and
  // refresh when that tab opens or via its Refresh button — not on every
  // overview poll.
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
/* A job row is a door, not a dead end: clicking it expands the payload
 * (the user's own words, for runtime.turn jobs), the run linkage, error and
 * timing, plus the canonical result fetched from /jobs/{id}/result. Columns
 * match what a human scans: job, kind, state, tries, run, age.
 *
 * The queue poller rebuilds the whole tbody every few seconds; without
 * special care that collapsed (i.e. slammed shut) any detail the user was
 * reading mid-read. EXPANDED_JOBS remembers which rows are open so a poll
 * refresh re-opens exactly those — the reader keeps their place, the detail
 * just gets fresher. */
const EXPANDED_JOBS = new Set();
function jobRowHtml(x){
  const status = x.status || "unknown";
  const ask = x.payload && typeof x.payload === "object"
    ? String(x.payload.text || x.payload.task || x.payload.goal || "") : "";
  const preview = ask ? '<small class="cell-sub" title="' + esc(ask.slice(0, 200)) + '">' + esc(ask.slice(0, 80)) + '</small>' : "";
  return '<tr class="job-row" data-job="' + esc(x.id) + '" tabindex="0" role="button" aria-expanded="false" '
    + 'title="click to inspect this job">'
    + '<td><code>' + esc(x.id) + '</code>' + preview + '</td>'
    + '<td>' + esc(x.kind) + '</td>'
    + '<td><span class="status s-' + esc(status) + '">' + esc(status) + '</span></td>'
    + '<td>' + esc(x.attempts) + '/' + esc(x.max_attempts) + '</td>'
    + '<td><code>' + esc(x.run_id || "") + '</code></td>'
    + '<td>' + relCell(x.created_at) + '</td></tr>'
    + '<tr class="job-detail" hidden><td colspan="6"><div class="note">loading job…</div></td></tr>';
}
function jobDetailHtml(x, resultEnvelope){
  const ask = x.payload && typeof x.payload === "object"
    ? String(x.payload.text || x.payload.task || x.payload.goal || "") : "";
  const bits = [];
  bits.push('<div class="kv"><span class="k">job</span><span class="v"><code>' + esc(x.id) + '</code></span></div>');
  if (ask) bits.push('<div class="kv"><span class="k">request</span><span class="v">' + esc(ask.slice(0, 400)) + '</span></div>');
  if (x.error) bits.push('<div class="kv"><span class="k">error</span><span class="v">' + esc(String(x.error).slice(0, 400)) + '</span></div>');
  if (x.wait_ms != null) bits.push('<div class="kv"><span class="k">waited</span><span class="v">' + esc(Math.round(Number(x.wait_ms) / 100) / 10) + 's</span></div>');
  if (x.duration_ms != null) bits.push('<div class="kv"><span class="k">ran for</span><span class="v">' + esc(Math.round(Number(x.duration_ms) / 100) / 10) + 's</span></div>');
  const result = resultEnvelope && (resultEnvelope.result ?? resultEnvelope);
  if (result != null) {
    const text = typeof result === "object" ? (result.response || result.text || result.output || "") : result;
    if (text) bits.push('<div class="kv"><span class="k">result</span><span class="v">' + esc(String(text).slice(0, 600)) + '</span></div>');
    else if (typeof result === "object") bits.push('<div class="detail-block">' + humanSummary(result) + '</div>');
  } else if (x.has_result === false) {
    bits.push('<div class="note">no stored result — the event ledger has the full timeline</div>');
  }
  return bits.join("") || '<div class="note">no details recorded</div>';
}
async function toggleJobDetail(row, force){
  const detail = row.nextElementSibling;
  if (!detail) return;
  const opening = force === true ? true : detail.hidden;
  detail.hidden = !opening;
  row.setAttribute("aria-expanded", opening ? "true" : "false");
  const jobId = String(row.dataset.job || "");
  if (opening) EXPANDED_JOBS.add(jobId); else EXPANDED_JOBS.delete(jobId);
  if (!opening) return;
  detail.innerHTML = '<td colspan="6"><div class="note">loading job…</div></td>';
  try {
    const all = await getJSON("/jobs");
    const list = (Array.isArray(all.j) ? all.j : all.j.jobs || []);
    const x = list.find((y) => String(y.id) === String(jobId)) || {};
    let envelope = null;
    try { envelope = (await getJSON("/jobs/" + encodeURIComponent(jobId) + "/result")).j; }
    catch (err) { envelope = null; }
    detail.innerHTML = '<td colspan="6">' + jobDetailHtml(x, envelope) + '</td>';
  } catch (e) {
    detail.innerHTML = '<td colspan="6"><div class="note">job details unavailable: ' + esc(e.message) + '</div></td>';
  }
}
async function refreshJobs(){
  const body = $("#jobs tbody");
  try {
    const { j } = await getJSON("/jobs");
    const rows = (Array.isArray(j) ? j : j.jobs || []).slice(0, 60);
    if (!rows.length) { body.innerHTML = stateRowHtml({ empty: "queue idle — no jobs yet" }, 6); }
    else body.innerHTML = rows.map(jobRowHtml).join("");
    body.querySelectorAll("tr.job-row").forEach((tr) => {
      tr.addEventListener("click", () => toggleJobDetail(tr));
      tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggleJobDetail(tr); } });
      // A poll refresh must not slam shut the detail row the user is reading.
      if (EXPANDED_JOBS.has(String(tr.dataset.job || ""))) toggleJobDetail(tr, true);
    });
    const n = Array.isArray(j) ? j.length : (j.jobs || []).length;
    $("#jobcount").textContent = n + " jobs";
    const { j: st } = await getJSON("/queue/status");
    const q = (st && st.queue) || st || {};
    const bs = q.by_status || {};
    const parts = Object.entries(bs).map(([k,v])=>v+" "+k).join(", ");
    $("#qstatus").textContent = "backend " + (q.backend || "?") + " · " + (q.workers || "?") + " workers" + (parts ? " · " + parts : "");
  } catch(e){
    body.innerHTML = stateRowHtml({ error: e.envelope || { message: e.message } }, 6,
      { label: "queue", onRetryId: "jobs" });
  }
}
RETRY_ACTIONS.jobs = refreshJobs;

// ---------- MISSIONS ----------
/* The state filter groups the twelve lifecycle states into the buckets a
 * human actually scans for: blocked, failed, active, terminal. The bucket
 * lives next to the renderer so the dropdown and the table can never
 * disagree about what "active" means. */
const MISSION_ACTIVE_STATES = new Set(["pending","planning","executing","observing","verifying","diagnosing","repairing","continuing"]);
const MISSION_TERMINAL_STATES = new Set(["completed","cancelled"]);
const MISSION_RESUMABLE_STATES = new Set(["blocked","failed"]);
function missionBucket(state){
  const s = String(state || "").toLowerCase();
  if (MISSION_RESUMABLE_STATES.has(s)) return s;
  if (MISSION_TERMINAL_STATES.has(s)) return "terminal";
  if (MISSION_ACTIVE_STATES.has(s)) return "active";
  return "other";
}
async function refreshMissions(){
  const body = $("#missions tbody");
  try {
    const { j } = await getJSON("/missions");
    const rows = j.missions || [];
    const filter = $("#missionStateFilter")?.value || "all";
    const shown = filter === "all" ? rows : rows.filter((m) => missionBucket(m.state) === filter);
    const blockedN = rows.filter((m) => String(m.state).toLowerCase() === "blocked" || m.approval_request).length;
    setPill("#blockedMissionCount", "blocked missions: " + blockedN, blockedN ? "warn" : "ok");
    updateSafetyCore();
    const count = $("#missionCount");
    if (count) count.textContent = rows.length ? ("showing " + shown.length + " of " + rows.length) : "";
    const bulk = $("#missionBulkResume");
    if (bulk) bulk.disabled = !rows.some((m) => MISSION_RESUMABLE_STATES.has(String(m.state).toLowerCase()));
    if (!rows.length) { body.innerHTML = stateRowHtml({ empty: "no missions yet" }, 5); return; }
    if (!shown.length) { body.innerHTML = stateRowHtml({ empty: "no missions in this state — switch the filter to see the other " + rows.length }, 5); return; }
    body.innerHTML = shown.slice(0, 80).map((m) => {
      const ap = m.approval_request || {};
      const failure = m.failure || {};
      const createPrompts = m.create_prompts_action || {};
      const blockerRaw = String(m.blocker_reason || failure.reason || "");
      const blocker = ap.id ? ('approval <code>' + esc(ap.id) + '</code>') : esc(blockerRaw);
      // "Configure a model before running missions" must be a door, not a
      // dead end: when the blocker is the model backend, offer the jump.
      const modelBtn = /configure a model|no model backend|ollama|api key/i.test(blockerRaw)
        ? ' <button class="ghost" data-goto-tab="systems">connect a model →</button>' : '';
      const approveBtn = ap.id ? `<button class="ghost" data-mission-approve="${esc(m.mission_id)}" data-approval-id="${esc(ap.id)}">approve+retry</button>` : '';
      const promptsBtn = createPrompts.endpoint ? `<button class="ghost" data-mission-prompts="${esc(m.mission_id)}">create prompts</button>` : '';
      const resumeBtn = MISSION_RESUMABLE_STATES.has(String(m.state).toLowerCase()) || ["executing","pending","continuing"].includes(String(m.state)) ? `<button class="ghost" data-mission-resume="${esc(m.mission_id)}">resume</button>` : '';
      return `<tr><td><code>${esc(m.mission_id)}</code></td><td><span class="status s-${esc(m.state)}">${esc(m.state)}</span></td><td>${esc(m.progress_pct ?? 0)}%</td><td>${blocker}${modelBtn}</td><td>${approveBtn} ${promptsBtn} ${resumeBtn}</td></tr>`;
    }).join("");
    body.querySelectorAll("button[data-mission-approve]").forEach((b) => b.addEventListener("click", () => approveMission(b.dataset.missionApprove, b.dataset.approvalId)));
    body.querySelectorAll("button[data-mission-prompts]").forEach((b) => b.addEventListener("click", () => createMissionPrompts(b.dataset.missionPrompts)));
    body.querySelectorAll("button[data-mission-resume]").forEach((b) => b.addEventListener("click", () => resumeMission(b.dataset.missionResume)));
    body.querySelectorAll("button[data-goto-tab]").forEach((b) => b.addEventListener("click", () => { if (window.selectTab) selectTab(b.dataset.gotoTab); }));
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
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = mdLite(d.markdown) || humanSummary(d); refreshSafetyEvents(); })
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
/* Bulk resume: one click for the common "everything is blocked after a
 * restart" case. Each mission gets its own honest per-row result — a bulk
 * button that only said "done" would hide which resumes actually took. */
async function bulkResumeMissions(){
  const out = $("#missionOut");
  const btn = $("#missionBulkResume");
  try {
    const { j } = await getJSON("/missions");
    const targets = (j.missions || []).filter((m) => MISSION_RESUMABLE_STATES.has(String(m.state).toLowerCase()));
    if (!targets.length) { out.innerHTML = '<div class="note">no blocked or failed missions to resume</div>'; refreshMissions(); return; }
    if (btn) { btn.disabled = true; btn.textContent = "resuming…"; }
    out.innerHTML = '<div class="note">resuming ' + targets.length + ' mission' + (targets.length > 1 ? 's' : '') + '…</div>';
    const results = [];
    for (const m of targets) {
      let ok = false, state = "?";
      try {
        const r = await fetch("/missions/" + encodeURIComponent(m.mission_id) + "/resume", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ extra_steps: 8 }) });
        const d = await r.json();
        state = d.state || d.status || d.success || d.error || "?";
        /* HTTP 200 only means the request was handled; the honest success
         * signal is the mission actually leaving the blocked/failed bucket.
         * A mission that re-blocks on the same missing model counts as NOT
         * resumed, whatever the transport status said. */
        ok = r.ok && !MISSION_RESUMABLE_STATES.has(String(state).toLowerCase());
      } catch(e){ state = e.message; }
      results.push({ id: m.mission_id, ok, state });
    }
    out.innerHTML = results.map((x) => '<div>resume <code>' + esc(x.id) + '</code>: <span class="' + (x.ok ? 'e' : 'note') + '">' + esc(String(x.state)) + '</span></div>').join("")
      + '<div class="note">' + results.filter((x) => x.ok).length + '/' + results.length + ' resumed</div>';
    refreshMissions();
  } catch(e){
    out.innerHTML = '<div class="note">bulk resume failed: ' + esc(e.message) + '</div>';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "resume blocked"; }
  }
}

// ---------- TELEMETRY ----------
let tmWs = null;
/* Raw event types are for the ledger, not for eyes: "harness.turn_prepared"
 * reads as "turn prepared", with the raw type and timestamp kept in the
 * row title for anyone diagnosing. */
const TM_QUIET_NAMESPACES = new Set(["harness", "dashboard", "nexus", "kernel", "runtime"]);
function tmHumanType(t){
  const s = String(t || "");
  const dot = s.indexOf(".");
  if (dot < 0) return s.replace(/_/g, " ");
  const head = s.slice(0, dot), rest = s.slice(dot + 1).replace(/_/g, " ");
  return TM_QUIET_NAMESPACES.has(head) ? rest : head + " " + rest;
}
function tmAppend(e){
  const feed = $("#tmFeed");
  if (!feed) return;
  const t = (e && e.type) || "";
  const human = tmHumanType(t);
  const f = $("#tmSearch").value.trim().toLowerCase();
  if (f && !(t.toLowerCase().includes(f) || human.toLowerCase().includes(f))) return;
  const el = document.createElement("div");
  const when = relCell(e.ts || e.timestamp || "");
  el.innerHTML = `<span class="t">${when}</span> <span class="e">${esc(human || t)}</span> ${esc(e.command || e.status || "")}`;
  el.title = String(t || "") + (e.ts || e.timestamp ? " · " + String(e.ts || e.timestamp) : "");
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
    const wsToken = GW_TOKEN ? "?token=" + encodeURIComponent(GW_TOKEN) : "";
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
    body.innerHTML = tasks.slice(0, 30).map((t) => `<tr><td><code>${esc(t.task_id || t.id || "")}</code></td><td><span class="status s-${esc(t.status)}">${esc(t.status)}</span></td><td>${relCell(t.updated_at)}</td><td>${esc(t.goal || t.task || t.summary || "")}</td></tr>`).join("")
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
    $("#remoteStatus").innerHTML = kpi(((st.approval?.enabled ?? st.enabled ?? false)) ? "on" : "off", "approval gate")
      + kpi(((st.control?.active ?? st.active ?? false)) ? "on" : "off", "active")
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
    feed.innerHTML = mdLite(j.markdown) || humanSummary(j);
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
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = mdLite(d.markdown) || humanSummary(d); })
    .catch((e)=>{ out.innerHTML = '<div class="note">proposal failed: ' + esc(e.message) + '</div>'; });
}
function runPreflight(){
  const goal = $("#preflightGoal").value.trim();
  const out = $("#preflightOut");
  if (!goal) { out.innerHTML = '<div class="note">goal required</div>'; return; }
  out.innerHTML = '<div class="note">running pre-flight…</div>';
  fetch("/safety/preflight", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ goal, format:"markdown" }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = mdLite(d.markdown) || humanSummary(d); refreshSafetyEvents(); })
    .catch((e)=>{ out.innerHTML = '<div class="note">pre-flight failed: ' + esc(e.message) + '</div>'; });
}
function runDownloadsScan(){
  const out = $("#preflightOut");
  out.innerHTML = '<div class="note">running approved Downloads defensive scan…</div>';
  fetch("/local-defense/scan", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ path:"~/Downloads", purpose:"malware", max_files:500, save_report:true }) })
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = mdLite(d.markdown) || humanSummary(d); refreshSafetyEvents(); })
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
    .then((r)=>r.json()).then((d)=>{ out.innerHTML = '<div>draft prompts created: <span class="e">' + esc((d.created||[]).length) + '</span></div>' + (d.errors?.length ? '<div class="note">errors: ' + esc(d.errors.map((x) => x.message || x.error || String(x)).join("; ")) + '</div>' : ''); refreshSafety(); refreshSafetyEvents(); })
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
$("#missionStateFilter").addEventListener("change", refreshMissions);
$("#missionBulkResume").addEventListener("click", bulkResumeMissions);
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
$("#capsRefresh").addEventListener("click", refreshCaps);
$("#memoryStatsRefresh").addEventListener("click", refreshMemory);
$("#memoryRecallBtn").addEventListener("click", recallMemory);
$("#memoryRecallQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") recallMemory(); });
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
  // Every panel loads independently. Serialising them meant a slow probe (the
  // doctor-backed health card) delayed readiness, presence and jobs behind it.
  await Promise.allSettled([refreshOverview(), refreshPresence(), refreshJobs(), refreshMissions()]);
  refreshTelemetry(true);
  openTmStream();
}
boot();
setInterval(() => { refreshOverview(); refreshPresence(); refreshJobs(); try { refreshMissions(); } catch(e){} try { refreshTelemetry(false); } catch(e){} }, 8000);
RETRY_ACTIONS.overview = refreshOverview;
