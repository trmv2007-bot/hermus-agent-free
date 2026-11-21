'use strict';
/**
 * Hermus Systems console — the generated control-room surface.
 *
 * Every card on this tab comes from `GET /api/v1/console/manifest`, which is
 * `core/console.py`: one table naming each subsystem, the owner that really
 * holds it, the endpoints that serve it and the actions a human may trigger.
 * Live values come from `GET /api/v1/console/panels`, a probe of those same
 * owners.
 *
 * Two deliberate properties:
 *
 * * **No per-panel code.** Rendering is generic, so a capability added to the
 *   table appears here without touching this file. The alternative — a bespoke
 *   panel per subsystem — is what this tab replaces.
 * * **It reuses the control room's plumbing** (`requestJSON` for the canonical
 *   error envelope, `stateHtml` for loading/empty/error states, `toast` for
 *   action feedback, `RETRY_ACTIONS` for retry buttons) instead of carrying a
 *   second copy of all four.
 *
 * Actions post to the endpoint printed on the card. This file never invents a
 * route, and a card whose endpoint is not in the gateway's own route table is
 * shown as unverified rather than clickable-and-silently-broken.
 */
(function () {
  const state = {
    manifest: null,
    views: {},          // panel id -> latest probe payload
    group: 'all',
    filter: '',
    open: {},           // panel id -> raw JSON expanded
    results: {},        // panel id -> action result (text)
  };

  const HOST = () => document.querySelector('#consolePanels');

  /* ------------------------------------------------------------------ load */
  async function loadManifest() {
    const res = await requestJSON('/api/v1/console/manifest');
    if (!res.ok) {
      const host = HOST();
      if (host) host.innerHTML = stateHtml({ error: res }, { label: 'console manifest', onRetryId: 'console' });
      return false;
    }
    state.manifest = res.data;
    if (state.manifest.panel_count === undefined) state.manifest.panel_count = (state.manifest.panels || []).length;
    return true;
  }

  async function refresh(ids) {
    const query = ids && ids.length ? '?ids=' + encodeURIComponent(ids.join(',')) : '';
    const res = await requestJSON('/api/v1/console/panels' + query);
    if (!res.ok) {
      const host = HOST();
      if (host) host.innerHTML = stateHtml({ error: res }, { label: 'panels', onRetryId: 'console' });
      return res;
    }
    (res.data.panels || []).forEach((p) => { state.views[p.id] = p; });
    render();
    return res;
  }

  async function refreshPanel(id) {
    const res = await requestJSON('/api/v1/console/projection/' + encodeURIComponent(id));
    if (res.ok) { state.views[id] = res.data; render(); }
    else toast('panel ' + id + ': ' + res.message, 'error', { requestId: res.requestId, retry: res.retryable, onRetry: () => refreshPanel(id) });
    return res;
  }

  /* ---------------------------------------------------------------- render */
  /* KPI labels arrive as dotted snake_case keys from the server's shaper
   * (`engine.doctor_model`, `stuck.threshold_minutes`). Rendered raw they
   * read as code; render them as words — "engine · doctor model" — while the
   * exact key stays in the tooltip so a value can still be correlated with
   * the raw owner return one click away. */
  function kpiLabel(label) {
    const raw = String(label == null ? '' : label);
    const text = raw.split('.').map((part) => part.replace(/_/g, ' ').trim()).filter(Boolean).join(' · ');
    return { text: text || raw, title: raw };
  }

  function summaryHtml() {
    const m = state.manifest || {};
    const views = Object.keys(state.views).length;
    const ready = Object.values(state.views).filter((v) => v.status === 'ready').length;
    const down = views - ready;
    return kpi(String(m.panel_count || 0), 'panels')
      + kpi(String(m.verified_panels == null ? '—' : m.verified_panels), 'endpoints verified')
      + kpi(String(m.action_count || 0), 'declared actions')
      + kpi(String(views), 'probed')
      + kpi(String(down), 'unavailable');
  }

  function chipsHtml(lists) {
    const parts = [];
    Object.keys(lists || {}).forEach((label) => {
      const items = lists[label] || [];
      if (!items.length) return;
      parts.push('<div class="chip-row"><span class="chip-label">' + esc(label) + '</span>'
        + items.slice(0, 24).map((v) => '<span class="tag">' + esc(v) + '</span>').join('') + '</div>');
    });
    return parts.join('');
  }

  /* Table cells arrive straight from owner JSON, where a list field renders
   * as Python repr — ['doctor', 'background'] — and a boolean as "false".
   * cellText turns those into what a human scans: word-dot-word lists and
   * yes/no. Object items collapse to compact JSON so a list of dicts reads
   * as data, never "[object Object]". Values that are already strings pass
   * through untouched. */
  function cellText(v) {
    if (v == null || v === '') return '';
    if (Array.isArray(v)) return v.map((item) => {
      if (item == null) return '';
      if (typeof item === 'object') return JSON.stringify(item).slice(0, 40);
      return String(item);
    }).filter(Boolean).join(' · ');
    if (typeof v === 'boolean') return v ? 'yes' : 'no';
    if (typeof v === 'object') return JSON.stringify(v).slice(0, 60);
    return String(v).slice(0, 80);
  }

  function tableHtml(view) {
    const cols = view.columns || [];
    const rows = view.rows || [];
    if (!cols.length || !rows.length) return '';
    return '<table class="mini"><thead><tr>' + cols.map((c) => '<th title="' + esc(String(c)) + '">' + esc(kpiLabel(c).text) + '</th>').join('')
      + '</tr></thead><tbody>' + rows.map((r) => '<tr>' + cols.map((c) => '<td>' + esc(cellText(r[c])) + '</td>').join('') + '</tr>').join('')
      + '</tbody></table>';
  }

  function bodyHtml(panel, view) {
    if (!view) return '<div class="note">not probed yet — press “Refresh panels”.</div>';
    if (view.status !== 'ready') {
      return '<div class="panel-state error">' + esc(view.error || 'unavailable') + '</div>';
    }
    const kpis = (view.kpis || []).map((k) => {
      const label = kpiLabel(k.label);
      return '<div class="kpi" title="' + esc(label.title) + '"><div class="v">' + esc(valueLabel(k.value)) + '</div><div class="l">' + esc(label.text) + '</div></div>';
    }).join('');
    const raw = state.open[panel.id]
      ? '<details class="raw" open><summary>raw owner return</summary><pre>' + esc(JSON.stringify(view.raw, null, 2)) + '</pre></details>'
      : '<details class="raw"><summary>raw owner return</summary><pre>' + esc(JSON.stringify(view.raw, null, 2)) + '</pre></details>';
    return '<div class="cells">' + kpis + '</div>' + chipsHtml(view.lists) + tableHtml(view)
      + '<div class="owner">read from <code>' + esc(panel.owner || panel.source) + '</code> in ' + esc(String(view.ms)) + 'ms</div>' + raw;
  }

  function endpointsHtml(panel) {
    const status = panel.endpoint_status || [];
    if (!status.length) return '';
    return '<div class="chip-row"><span class="chip-label">endpoints</span>' + status.map((e) => {
      const ok = e.verified;
      const cls = ok ? 'tag ep' : 'tag ep dead';
      const title = ok ? 'verified in the gateway route table — click to GET' : 'not in the live route table (renamed or removed)';
      const clickable = ok && e.method === 'GET';
      return '<span class="' + cls + '" title="' + esc(title) + '"'
        + (clickable ? ' data-ep-get="' + esc(e.path) + '" data-ep-panel="' + esc(panel.id) + '"' : '')
        + '>' + esc(e.endpoint) + (ok ? '' : ' ⚠') + '</span>';
    }).join('') + '</div>';
  }

  function actionsHtml(panel) {
    return (panel.actions || []).map((a, i) => {
      const inputs = (a.fields || []).map((f) =>
        '<input class="act-in" data-field="' + esc(f.name) + '" placeholder="' + esc(f.name) + '"'
        + (f.default && f.required ? '' : '') + ' value="' + esc(f.default || '') + '" />').join('');
      return '<div class="act"><button class="ghost" data-act="' + i + '" data-panel="' + esc(panel.id) + '"'
        + (a.confirm ? ' data-confirm="1"' : '') + '>' + esc(a.label) + '</button>' + inputs + '</div>';
    }).join('');
  }

  function cardHtml(panel) {
    const view = state.views[panel.id];
    const state_ = view ? view.status : 'pending';
    return '<section class="console-card ' + (state_ === 'ready' ? 'ok' : (state_ === 'unavailable' ? 'bad' : '')) + '" data-panel-card="' + esc(panel.id) + '">'
      + '<div class="card-head"><div><div class="card-title">' + esc(panel.label) + '</div>'
      + '<div class="card-sub">' + esc(panel.group) + ' · ' + esc(panel.source) + '</div></div>'
      + '<span class="tag">' + esc(state_) + '</span></div>'
      + '<div class="card-sum">' + esc(panel.summary) + '</div>'
      + bodyHtml(panel, view)
      + endpointsHtml(panel)
      + (panel.actions && panel.actions.length ? '<div class="acts">' + actionsHtml(panel) + '</div>' : '')
      + (state.results[panel.id] ? '<div class="act-out">'
        + (state.results[panel.id].doctor
          ? state.results[panel.id].doctor
          : '<pre>' + esc(state.results[panel.id].text || state.results[panel.id]) + '</pre>')
        + (state.results[panel.id].raw ? '<details class="raw"><summary>raw owner return</summary><pre>' + esc(state.results[panel.id].raw) + '</pre></details>' : '')
        + '</div>' : '')
      + '</section>';
  }

  function visible(panel) {
    if (state.group !== 'all' && panel.group !== state.group) return false;
    if (!state.filter) return true;
    const hay = (panel.id + ' ' + panel.label + ' ' + panel.group + ' ' + panel.source + ' ' + panel.summary + ' ' + (panel.owner || '')).toLowerCase();
    return hay.indexOf(state.filter) !== -1;
  }

  function render() {
    const m = state.manifest;
    if (!m) return;
    const summary = document.querySelector('#consoleSummary');
    if (summary) summary.innerHTML = summaryHtml();
    const host = HOST();
    if (!host) return;
    const panels = (m.panels || []).filter(visible);
    if (!panels.length) {
      host.innerHTML = '<div class="note">no panel matches this filter.</div>';
      return;
    }
    host.innerHTML = panels.map(cardHtml).join('');
  }

  function syncGroups() {
    const select = document.querySelector('#consoleGroup');
    if (!select || !state.manifest) return;
    select.innerHTML = '<option value="all">all groups</option>' + (state.manifest.groups || []).map((g) =>
      '<option value="' + esc(g.id) + '">' + esc(g.label) + ' (' + esc(g.panels) + ')</option>').join('');
    select.value = state.group;
  }

  /* --------------------------------------------------------------- actions */
  function coerce(raw) {
    const v = String(raw == null ? '' : raw).trim();
    if (v === 'true') return true;
    if (v === 'false') return false;
    if (v !== '' && !isNaN(Number(v))) return Number(v);
    return v;
  }

  /* Action/GET results read like a product: flat label/value lines first, the
   * full owner return one click away in a collapsed details block. */
  function flatSummary(d) {
    if (d == null) return 'done';
    if (typeof d !== 'object') return String(d);
    const lines = [];
    for (const [k, v] of Object.entries(d)) {
      if (v == null || k === 'success' || k === 'trace_id' || k === 'request_id') continue;
      if (typeof v === 'object') lines.push(k.replace(/_/g, ' ') + ': ' + (Array.isArray(v) ? v.length + ' item(s)' : 'see raw return'));
      else lines.push(k.replace(/_/g, ' ') + ': ' + valueLabel(v));
      if (lines.length >= 12) { lines.push('…'); break; }
    }
    return lines.join('\n') || 'done';
  }

  /* A doctor report is not a generic blob: its findings ARE the product —
   * severity, title, evidence, and the fixes the doctor already wrote. The
   * generic summary reduced all of that to "findings: 16 item(s)" over a raw
   * JSON dump; this renders what the user ran the tool to read. */
  function doctorReportHtml(d) {
    const counts = d.counts || {};
    const sevOrder = ['critical', 'high', 'medium', 'low', 'info'];
    const chips = sevOrder.filter((k) => counts[k]).map((k) => '<span class="tag doc-sev ' + esc(k) + '">' + esc(k) + ': ' + esc(counts[k]) + '</span>').join(' ');
    const engine = d.engine && typeof d.engine === 'object'
      ? 'engine: ' + [d.engine.mode, d.engine.status, d.engine.doctor_model ? 'doctor model ' + d.engine.doctor_model : ''].filter(Boolean).join(' · ') : '';
    const findings = (Array.isArray(d.findings) ? d.findings : []).slice(0, 16).map((f) => {
      const fixes = Array.isArray(f.fixes) && f.fixes.length
        ? '<div class="doc-fixes">' + f.fixes.slice(0, 4).map((x) => '<code>' + esc(String(x).slice(0, 140)) + '</code>').join('') + '</div>' : '';
      return '<div class="doc-finding sev-' + esc(String(f.severity || 'info').toLowerCase()) + '">'
        + '<div class="doc-finding-head"><b>' + esc(String(f.title || f.category || 'finding').slice(0, 120)) + '</b>'
        + (f.component ? '<span class="tag">' + esc(String(f.component)) + '</span>' : '')
        + (f.auto_fixable ? '<span class="tag ok">auto-fixable</span>' : '')
        + '</div>'
        + (f.evidence ? '<small>' + esc(String(f.evidence).slice(0, 200)) + '</small>' : '')
        + fixes + '</div>';
    }).join('');
    const stuck = d.stuck && (d.stuck.count || d.stuck.runs || d.stuck.jobs)
      ? '<div class="kv"><span class="k">stuck work</span><span class="v">' + esc(valueLabel(d.stuck.count != null ? d.stuck.count : d.stuck)) + '</span></div>' : '';
    return '<div class="doc-report">'
      + '<div class="doc-line">' + esc(String(d.status || 'done')) + (d.worst_severity ? ' · worst: ' + esc(d.worst_severity) : '') + (engine ? ' · ' + esc(engine) : '') + '</div>'
      + (chips ? '<div class="chip-row">' + chips + '</div>' : '')
      + (findings || '<div class="note">no findings — nothing needs attention</div>')
      + stuck + '</div>';
  }

  function resultRecord(prefix, res) {
    if (!res.ok) return { text: '✗ ' + prefix + '\n' + res.message + ' (' + res.code + ')' };
    const d = res.data;
    // Shape-match a doctor report: findings list + severity counts. Generic
    // results keep the flat summary; the doctor gets the findings renderer.
    if (d && typeof d === 'object' && Array.isArray(d.findings) && d.counts && typeof d.counts === 'object') {
      return { doctor: doctorReportHtml(d), raw: JSON.stringify(d, null, 2).slice(0, 4000) };
    }
    return { text: prefix + ' — ok\n' + flatSummary(d), raw: JSON.stringify(d, null, 2).slice(0, 4000) };
  }

  async function runAction(panelId, index, node) {
    const panel = (state.manifest.panels || []).find((p) => p.id === panelId);
    const action = panel && panel.actions[index];
    if (!action) return;
    if (action.confirm && !window.confirm(action.label + ' — ' + panel.label + '?\n\nPOST ' + action.path)) return;
    const card = node.closest('.console-card');
    const body = {};
    (card ? card.querySelectorAll('.act-in') : []).forEach((input) => {
      const name = input.getAttribute('data-field');
      if (input.value !== '') body[name] = coerce(input.value);
      else if (input.getAttribute('value') === '') { /* untouched empty stays omitted */ }
    });
    const isPost = action.method === 'POST';
    const res = await requestJSON(action.path, isPost
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : { method: 'GET' });
    state.results[panelId] = resultRecord('POST ' + action.path, res);
    toast(panel.label + ' · ' + action.label + ': ' + (res.ok ? 'ok' : res.message),
      res.ok ? 'ok' : 'error',
      { requestId: res.requestId, retry: res.retryable, onRetry: () => runAction(panelId, index, node) });
    // An action changes real state, so re-read the panel rather than assume.
    await refreshPanel(panelId);
  }

  async function fetchEndpoint(path, panelId, node) {
    const res = await requestJSON(path, { method: 'GET' });
    state.results[panelId] = resultRecord('GET ' + path, res);
    render();
    toast(panelId + ' · GET ' + path + ': ' + (res.ok ? 'ok' : res.message), res.ok ? 'ok' : 'error',
      { requestId: res.requestId, retry: res.retryable, onRetry: () => fetchEndpoint(path, panelId, node) });
  }

  /* ----------------------------------------------------------------- events */
  function bind() {
    const host = HOST();
    if (host) {
      host.addEventListener('click', (ev) => {
        const act = ev.target.closest ? ev.target.closest('[data-act]') : null;
        if (act) { runAction(act.getAttribute('data-panel'), Number(act.getAttribute('data-act')), act); return; }
        const ep = ev.target.closest ? ev.target.closest('[data-ep-get]') : null;
        if (ep) { fetchEndpoint(ep.getAttribute('data-ep-get'), ep.getAttribute('data-ep-panel'), ep); return; }
      });
    }
    const filter = document.querySelector('#consoleFilter');
    if (filter) filter.addEventListener('input', () => { state.filter = (filter.value || '').trim().toLowerCase(); render(); });
    const group = document.querySelector('#consoleGroup');
    if (group) group.addEventListener('change', () => { state.group = group.value; render(); });
    const refreshBtn = document.querySelector('#consoleRefresh');
    if (refreshBtn) refreshBtn.addEventListener('click', () => refresh());
    const probeAll = document.querySelector('#consoleProbeAll');
    if (probeAll) probeAll.addEventListener('click', () => refresh());
  }

  /* Retry buttons rendered by stateHtml() are delegated through the control
   * room's RETRY_ACTIONS table; registering one entry per panel is what keeps
   * "every panel that offers Retry has an action registered" true for the
   * generated panels, including the doctor card that replaced its own tab. */
  function registerRetries() {
    (state.manifest.panels || []).forEach((p) => { RETRY_ACTIONS[p.id] = () => refreshPanel(p.id); });
    RETRY_ACTIONS.console = () => refresh();
  }

  async function boot() {
    if (!document.querySelector('#consolePanels')) return;
    // The landing shell mounts #consolePanels without the control-room script;
    // without the shared plumbing there is nothing to boot against.
    if (typeof requestJSON === 'undefined') return;
    bind();
    if (!(await loadManifest())) return;
    syncGroups();
    render();
    // Probing every panel on page load meant a ~20s storm of SQLite/filesystem
    // probes behind an Overview nobody had asked for. Probe when Systems is
    // actually visible; refreshTab("systems") still triggers it on open.
    const panel = document.getElementById('tab-systems');
    if (panel && panel.classList.contains('on')) await refresh();
    registerRetries();
  }

  window.HermusConsole = {
    boot: boot,
    refresh: refresh,
    refreshPanel: refreshPanel,
    reload: async () => { state.views = {}; await loadManifest(); syncGroups(); render(); await refresh(); },
    state: state,
    // Exposed for the Node test harness; not part of the page contract.
    __test: { cellText, flatSummary, doctorReportHtml, resultRecord },
  };
  boot();
})();
