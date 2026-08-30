/* State/render layer for JARVIS. Depends only on the shared HermusClient. */
(function (global) {
  'use strict';
  const STORAGE = 'hermus.jarvis.state.v3';
  const listeners = new Set();
  const defaults = {
    connection: 'checking', backendHealth: null, queueHealth: null, activeRuns: {},
    selectedRun: null, selectedJob: null, selectedMission: null, agents: [], tools: [],
    models: [], channels: {}, keys: [], artifacts: [], lastResponse: '', lastError: '',
    streamState: 'idle', commandHistory: [], selectedPanel: null, mode: 'normal'
  };
  let persisted = {};
  try { persisted = JSON.parse(localStorage.getItem(STORAGE) || '{}'); } catch (_) {}
  const state = Object.assign({}, defaults, persisted, { activeRuns: persisted.activeRuns || {} });
  function persist() {
    const safe = { selectedRun: state.selectedRun, selectedJob: state.selectedJob,
      selectedMission: state.selectedMission, lastResponse: state.lastResponse,
      lastError: state.lastError, commandHistory: state.commandHistory.slice(-50),
      selectedPanel: state.selectedPanel, mode: state.mode, activeRuns: state.activeRuns };
    try { localStorage.setItem(STORAGE, JSON.stringify(safe)); } catch (_) {}
  }
  function set(patch) { Object.assign(state, patch); persist(); listeners.forEach((fn) => { try { fn(state, patch); } catch (_) {} }); }
  function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
  const esc = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const fmtBytes = (value) => value == null ? 'Not available' : `${(Number(value) / 1048576).toFixed(1)} MiB`;
  const empty = (text) => `<div class="jarvis-empty">${esc(text)}</div>`;
  const error = (text) => `<div class="jarvis-error" role="alert">${esc(text)}</div>`;
  const row = (a, b) => `<div class="jarvis-row"><b>${esc(a)}</b><span>${esc(b)}</span></div>`;

  async function refresh() {
    set({ connection: 'checking', lastError: '' });
    try {
      const [status, tools, agents, keys, artifacts, missions] = await Promise.all([
        HermusClient.status(), HermusClient.jget('/tools'), HermusClient.jget('/agents'),
        HermusClient.jget('/keys/list'), HermusClient.jget('/artifacts'), HermusClient.missions.list()
      ]);
      const keyRows = [];
      Object.entries(keys.llm_keys || {}).forEach(([provider, entries]) => (entries || []).forEach((item) => keyRows.push(Object.assign({ provider }, item))));
      set({ connection: 'online', backendHealth: status.gateway, queueHealth: status.queue,
        activeRuns: Object.fromEntries((status.runs || []).map((run) => [run.run_id, run])),
        agents: agents.agents || [], tools: tools.tools || [], toolSources: tools.sources || {},
        keys: keyRows, artifacts: artifacts.artifacts || [], channels: status.channels || {},
        models: (status.model ? [status.model] : []).concat(keyRows.flatMap((key) => (key.models_sample || []).map((name) => ({ model: `${key.provider}/${name}`, provider: key.provider, present: key.healthy === true ? true : null, reachable: key.healthy, capabilities: { tools: 'unknown', vision: 'unknown', structured_output: 'unknown', streaming: 'unknown', computer_control: 'unknown' }, notes: ['Discovered for this configured key; capabilities have not been negotiated for this model.'] })))), providers: status.providers || [],
        telemetry: status.telemetry || {}, jobs: status.jobs || [], missions: missions.missions || [] });
      return status;
    } catch (cause) {
      set({ connection: 'offline', lastError: cause.message || String(cause) });
      throw cause;
    }
  }

  function trackRun(runId, patch) {
    const activeRuns = Object.assign({}, state.activeRuns);
    activeRuns[runId] = Object.assign({}, activeRuns[runId] || { run_id: runId, events: [] }, patch || {});
    set({ activeRuns, selectedRun: runId });
  }
  function event(runId, type, data) {
    const current = state.activeRuns[runId] || { run_id: runId, events: [] };
    const events = (current.events || []).concat([{ type, data, at: new Date().toISOString() }]).slice(-100);
    const patch = { events, status: /error|failed/.test(type) ? 'failed' : current.status };
    if (data && data.mission_id) patch.mission_id = data.mission_id;
    if (type === 'mission_state') patch.stage = data.state;
    if (type === 'node_started') patch.stage = data.stage || data.node_id || 'executing';
    trackRun(runId, patch);
  }

  function overview() {
    const h = state.backendHealth || {};
    const q = state.queueHealth || {};
    const jobs = state.jobs || [];
    return `<div class="jarvis-actions"><button data-j-action="refresh">Refresh live state</button><a href="/dashboard">Open full Classic Dashboard</a></div>
      ${row('Gateway', state.connection === 'online' ? 'Reachable' : state.connection)}
      ${row('Version', h.version || 'Not available')}${row('Uptime', h.uptime_seconds == null ? 'Not available' : `${h.uptime_seconds}s`)}
      ${row('Queue', q.enabled === false ? 'Disabled' : q.started ? `Running (${q.backend || 'unknown'})` : 'Not running')}
      ${row('Active jobs', jobs.filter((j) => ['queued','running'].includes(j.status)).length)}
      ${row('Active runs', Object.values(state.activeRuns).filter((r) => ['queued','running'].includes(r.status)).length)}
      ${row('Registered tools', state.tools.length)}${row('Background agents', state.agents.length)}
      ${row('Configured keys', state.keys.length)}${row('Artifacts', state.artifacts.length)}`;
  }
  function runs() {
    const rows = state.jobs || [];
    if (!rows.length) return empty('No durable jobs have been observed by this gateway.');
    return rows.map((job) => `<div class="jarvis-card"><b>${esc(job.kind || 'job')}</b> <code>${esc(job.id)}</code>
      <div>Status: ${esc(job.status || 'unknown')} · Run: ${esc(job.run_id || 'Not available')}</div>
      ${['queued','running'].includes(job.status) ? `<button data-j-cancel-job="${esc(job.id)}">Cancel job</button>` : ''}</div>`).join('');
  }
  function tools() {
    if (!state.tools.length) return empty('No tools are registered.');
    return state.tools.map((name) => `<div class="jarvis-card"><b>${esc(name)}</b><div>Source: ${esc((state.toolSources || {})[name] || 'Not reported')}</div><div>Availability: registered</div></div>`).join('');
  }
  function agents() {
    if (!state.agents.length) return empty('No persistent background agents are configured.');
    return state.agents.map((a) => `<div class="jarvis-card"><b>${esc(a.name || a.id || 'agent')}</b>${row('Role', a.role || 'Not reported')}${row('Status', a.status || 'Unknown')}${row('Current task', a.current_task || a.task || 'None reported')}${row('Run ID', a.run_id || 'Not reported')}${a.error ? error(a.error) : ''}</div>`).join('');
  }
  function keys() {
    if (!state.keys.length) return empty('No API keys configured.');
    return state.keys.map((k) => `<div class="jarvis-card"><b>${esc(k.provider)} / ${esc(k.name || 'unnamed')}</b>
      ${row('Model', k.default_model || 'Not configured')}${row('Configured', 'Yes')}
      ${row('Health', k.healthy === true ? 'Verified healthy' : k.healthy === false ? `Unhealthy: ${k.health_status || 'reason unavailable'}` : 'Not verified')}
      ${row('Last tested', k.last_tested || 'Never')}</div>`).join('');
  }
  function models() {
    if (!state.models.length) return empty('No model is selected/configured.');
    return state.models.map((m) => `<div class="jarvis-card"><b>${esc(m.model || 'Unconfigured model')}</b>
      ${row('Provider', m.provider || 'Not configured')}${row('Provider reachable', m.reachable === true ? 'Yes' : m.reachable === false ? 'No' : 'Not verified')}
      ${row('Model present', m.present === true ? 'Yes' : m.present === false ? 'No' : 'Not verified')}
      ${Object.entries(m.capabilities || {}).map(([k,v]) => row(k.replace(/_/g,' '), v)).join('')}
      ${(m.notes || []).map((n) => `<div class="jarvis-note">${esc(n)}</div>`).join('')}</div>`).join('');
  }
  function channels() {
    const c = state.channels || {}; const runtime = c.runtime || {};
    return `${row('Telegram configured', c.telegram_configured ? 'Yes' : 'No')}${row('Discord configured', c.discord_configured ? 'Yes' : 'No')}
      ${Object.keys(runtime).length ? Object.entries(runtime).map(([name,value]) => `<div class="jarvis-card"><b>${esc(name)}</b><pre>${esc(JSON.stringify(value, null, 2))}</pre></div>`).join('') : empty('No channel runtime state reported.')}`;
  }
  function artifacts() {
    if (!state.artifacts.length) return empty('No runtime artifacts recorded.');
    return state.artifacts.map((a) => `<div class="jarvis-card"><b>${esc(a.name || a.id || a.artifact_id)}</b>${row('Type', a.artifact_type || a.type || 'Not reported')}${row('Mission', a.mission_id || 'None')}${row('Path', a.path || 'Not reported')}</div>`).join('');
  }
  function missions() {
    const list = state.missions || [];
    const live = Object.values(state.activeRuns).filter((run) => run.mission_id || (run.events || []).some((e) => e.type.startsWith('mission_') || e.type.startsWith('node_')));
    const liveHtml = live.map((run) => `<div class="jarvis-card"><b>LIVE · ${esc(run.mission_id || run.run_id)}</b>${row('Run ID', run.run_id)}${row('State', run.status || 'running')}${row('Stage', run.stage || 'Planning / not reported')}<div class="jarvis-events">${(run.events || []).filter((e) => e.type.startsWith('mission_') || e.type.startsWith('node_') || e.type === 'verification').map((e) => `<div><b>${esc(e.type)}</b> ${esc(JSON.stringify(e.data))}</div>`).join('') || 'Waiting for mission events…'}</div></div>`).join('');
    if (!list.length && !live.length) return empty('No missions recorded. Start an autonomous request from Command Center.');
    return liveHtml + list.map((m) => { const id = m.mission_id || m.id; return `<div class="jarvis-card"><b>${esc(id)}</b>${row('Goal', m.goal || m.task || 'Not reported')}${row('State', m.state || m.status || 'Unknown')}${row('Stage', m.current_stage || m.stage || 'Not reported')}${m.failure ? error(typeof m.failure === 'string' ? m.failure : JSON.stringify(m.failure)) : ''}<div class="jarvis-actions"><button data-j-mission="resume" data-id="${esc(id)}">Resume</button><button data-j-mission="extend" data-id="${esc(id)}">Extend budget</button></div></div>`; }).join('');
  }
  function telemetry() {
    const t = state.telemetry || {};
    return `<div class="jarvis-note">Live process telemetry sampled by the gateway. The orb and spectrum are decorative visualizers.</div>
      ${row('Sample time', t.ts || 'Not available')}${row('Process CPU', t.cpu_percent == null ? 'Not available' : `${t.cpu_percent}%`)}
      ${row('System CPU', t.system_cpu_percent == null ? 'Not available' : `${t.system_cpu_percent}%`)}${row('Process memory', fmtBytes(t.memory_bytes))}
      ${row('Threads', t.threads == null ? 'Not available' : t.threads)}${row('Disk used', t.disk && t.disk.used_percent != null ? `${t.disk.used_percent}%` : 'Not available')}`;
  }
  function memory() {
    return `<div class="jarvis-note">Live memory summary. This panel does not invent semantic relationships.</div><div data-j-memory>Loading memory statistics…</div><button data-j-action="memory">Refresh memory stats</button>`;
  }
  function settings() {
    return `<div class="jarvis-note">Execution modes shown by the visual shell are display preferences only; they do not alter backend resource policy.</div>
      ${row('Selected visual mode', state.mode)}${row('Connection', state.connection)}<div class="jarvis-actions"><button data-j-action="clear-history">Clear local command history</button><a href="/dashboard">Provider and channel settings</a></div>`;
  }
  function swe() {
    const run = state.activeRuns[state.selectedRun] || {};
    const events = run.events || [];
    return `<div class="jarvis-note">Actual selected-run events only. Tests are “Not run” unless emitted by the runtime.</div>
      ${row('Run', state.selectedRun || 'No run selected')}${row('Task status', run.status || 'Not started')}${row('Current stage', run.stage || 'Not reported')}
      ${row('Tests', events.some((e) => /test|verification/.test(e.type)) ? 'See event log' : 'Not run')}
      <div class="jarvis-events">${events.length ? events.map((e) => `<div><b>${esc(e.type)}</b> ${esc(JSON.stringify(e.data))}</div>`).join('') : empty('No SWE/runtime events observed.')}</div>`;
  }
  const renderers = { overview, status: overview, runtime: runs, runs, tools, agents, keys, models, channels, artifacts, missions, telemetry, memory, settings, swe };

  async function mount(element, panel) {
    if (!element) return;
    element.dataset.jarvisPanel = panel;
    state.selectedPanel = panel; persist();
    element.innerHTML = '<div class="jarvis-loading">Loading live backend state…</div>';
    try { await refresh(); element.innerHTML = (renderers[panel] || overview)(); }
    catch (_) { element.innerHTML = error(state.lastError || 'Gateway unavailable'); }
    bind(element, panel);
  }
  function bind(element, panel) {
    element.querySelectorAll('[data-j-action="refresh"]').forEach((b) => b.onclick = () => mount(element, panel));
    element.querySelectorAll('[data-j-action="clear-history"]').forEach((b) => b.onclick = () => set({ commandHistory: [] }));
    element.querySelectorAll('[data-j-cancel-job]').forEach((b) => b.onclick = async () => { b.disabled = true; try { await HermusClient.cancelJob(b.dataset.jCancelJob); await mount(element, panel); } catch (e) { element.insertAdjacentHTML('afterbegin', error(e.message)); } });
    element.querySelectorAll('[data-j-mission]').forEach((b) => b.onclick = async () => { b.disabled = true; try { if (b.dataset.jMission === 'resume') await HermusClient.missions.resume(b.dataset.id, true); else await HermusClient.missions.extend(b.dataset.id, 10, false); await mount(element, panel); } catch (e) { element.insertAdjacentHTML('afterbegin', error(e.message)); b.disabled = false; } });
    element.querySelectorAll('[data-j-action="memory"]').forEach((b) => b.onclick = async () => { const out = element.querySelector('[data-j-memory]'); try { const d = await HermusClient.jget('/memory/stats'); out.innerHTML = `<pre>${esc(JSON.stringify(d, null, 2))}</pre>`; } catch(e) { out.innerHTML = error(e.message); } });
    const mb = element.querySelector('[data-j-action="memory"]'); if (mb) mb.click();
  }

  // Selected run/mission panels update from SSE-driven state without mixing
  // events from another run. Detached windows are naturally ignored.
  subscribe((_next, patch) => {
    if (!patch.activeRuns && !patch.selectedRun && !patch.missions && !patch.jobs) return;
    document.querySelectorAll('[data-jarvis-panel]').forEach((element) => {
      const panel = element.dataset.jarvisPanel;
      if (['swe','runtime','runs','missions'].includes(panel) && renderers[panel]) {
        element.innerHTML = renderers[panel](); bind(element, panel);
      }
    });
  });
  global.JarvisControl = { state, set, subscribe, refresh, trackRun, event, mount, esc, renderers };
})(window);
