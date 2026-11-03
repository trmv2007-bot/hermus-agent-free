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

  function tableHtml(view) {
    const cols = view.columns || [];
    const rows = view.rows || [];
    if (!cols.length || !rows.length) return '';
    return '<table class="mini"><thead><tr>' + cols.map((c) => '<th>' + esc(c) + '</th>').join('')
      + '</tr></thead><tbody>' + rows.map((r) => '<tr>' + cols.map((c) => '<td>' + esc(String(r[c] == null ? '' : r[c]).slice(0, 80)) + '</td>').join('') + '</tr>').join('')
      + '</tbody></table>';
  }

  function bodyHtml(panel, view) {
    if (!view) return '<div class="note">not probed yet — press “Refresh panels”.</div>';
    if (view.status !== 'ready') {
      return '<div class="panel-state error">' + esc(view.error || 'unavailable') + '</div>';
    }
    const kpis = (view.kpis || []).map((k) => kpi(String(k.value), k.label)).join('');
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
      + (state.results[panel.id] ? '<div class="act-out"><pre>' + esc(state.results[panel.id]) + '</pre></div>' : '')
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
    const text = res.ok ? JSON.stringify(res.data, null, 2).slice(0, 4000) : (res.message + ' (' + res.code + ')');
    state.results[panelId] = (res.ok ? 'POST ' : '✗ ') + action.path + '\n' + text;
    toast(panel.label + ' · ' + action.label + ': ' + (res.ok ? 'ok' : res.message),
      res.ok ? 'ok' : 'error',
      { requestId: res.requestId, retry: res.retryable, onRetry: () => runAction(panelId, index, node) });
    // An action changes real state, so re-read the panel rather than assume.
    await refreshPanel(panelId);
  }

  async function fetchEndpoint(path, panelId, node) {
    const res = await requestJSON(path, { method: 'GET' });
    state.results[panelId] = (res.ok ? 'GET ' : '✗ ') + path + '\n'
      + (res.ok ? JSON.stringify(res.data, null, 2).slice(0, 4000) : res.message);
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
    bind();
    if (!(await loadManifest())) return;
    syncGroups();
    render();
    await refresh();
    registerRetries();
  }

  window.HermusConsole = {
    boot: boot,
    refresh: refresh,
    refreshPanel: refreshPanel,
    reload: async () => { state.views = {}; await loadManifest(); syncGroups(); render(); await refresh(); },
    state: state,
  };
  boot();
})();
