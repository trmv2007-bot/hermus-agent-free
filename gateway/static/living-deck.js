(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    view: 'command', mode: 'agent', preview: true, motion: true,
    currentRun: null, dashboard: null, computer: null, approvals: null,
    eventCount: 0, talkOpen: false, muted: false, loading: false,
    playedAudio: new Set(), recorder: null, recordingStream: null,
    recordingChunks: [], reconnects: {}, history: [],
  };
  let toastTimer;

  const viewMeta = {
    missions: ['MISSION ARCHIVE', 'Missions', 'Active and completed work across the gateway and computer agent.'],
    computer: ['AUTONOMOUS DESKTOP CONTROL', 'Computer', 'Live task control, safety state, and visual-computer evidence.'],
    agents: ['DISTRIBUTED COGNITION', 'Agent Crew', 'Persistent specialists and currently active workers.'],
    memory: ['LOCAL LONG-TERM CONTEXT', 'Memory', 'Embedding health, recalled knowledge, and recorded computer episodes.'],
    models: ['INFERENCE ROUTING', 'Models', 'Local and cloud model routes available to Hermus.'],
    connections: ['CAPABILITY NETWORK', 'Connections', 'Channels, tools, MCP servers, and gateway links.'],
    settings: ['LOCAL SYSTEM CONTROL', 'Settings', 'Speech, workspace, cache, update, and gateway configuration.'],
  };

  const agentNames = ['researcher', 'critic', 'runner', 'verifier'];
  const agentLabels = { researcher: 'RESEARCHER_01', critic: 'CRITIC_02', runner: 'TOOL_RUNNER_03', verifier: 'VERIFIER_04' };
  const agentStatuses = {
    researcher: 'SCANNING SOURCES', critic: 'CHALLENGING CLAIMS', runner: 'USING TOOLS', verifier: 'VERIFYING OUTCOME',
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>\'"]/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[char]);
  }
  function safeJson(value) {
    try { return escapeHtml(JSON.stringify(value, null, 2)); }
    catch (_) { return escapeHtml(String(value)); }
  }
  function clip(value, length = 130) {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  }
  function token() {
    return new URLSearchParams(location.search).get('token') || localStorage.getItem('hermus_gateway_token') || '';
  }
  function authHeaders(extra = {}) {
    const headers = { ...extra };
    if (token()) headers['X-Hermus-Token'] = token();
    return headers;
  }
  async function api(path, options = {}) {
    const request = { ...options, headers: authHeaders(options.headers || {}) };
    if (request.body && typeof request.body !== 'string' && !(request.body instanceof Blob)) {
      request.headers['Content-Type'] = 'application/json';
      request.body = JSON.stringify(request.body);
    }
    const response = await fetch(path, request);
    const type = response.headers.get('content-type') || '';
    const data = type.includes('json') ? await response.json() : await response.text();
    if (!response.ok || (data && data.success === false && data.error)) {
      const message = typeof data === 'object' ? (data.error || data.detail || JSON.stringify(data)) : data;
      throw new Error(message || `Request failed (${response.status})`);
    }
    return data;
  }
  async function settled(path, options) {
    try { return { ok: true, data: await api(path, options) }; }
    catch (error) { return { ok: false, error: error.message }; }
  }
  function toast(message, error = false) {
    const node = $('toast');
    node.innerHTML = `<b class="${error ? 'fault' : ''}">${error ? 'FAULT' : 'HERMUS CORE'} //</b> ${escapeHtml(message)}`;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 3200);
  }
  function relativeTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return clip(value, 20);
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
    return date.toLocaleDateString();
  }
  function normalizeList(value) {
    if (Array.isArray(value)) return value;
    if (value && typeof value === 'object') return Object.entries(value).map(([name, detail]) => ({ name, detail }));
    return [];
  }

  // ---------- living visual field ----------
  function createParticleField(canvas, count) {
    if (!canvas) return () => {};
    const context = canvas.getContext('2d');
    let width = 0; let height = 0; let dpr = 1; let points = [];
    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth; height = canvas.clientHeight;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      points = Array.from({ length: count }, () => ({
        x: Math.random() * width, y: Math.random() * height,
        vx: (Math.random() - .5) * .16, vy: (Math.random() - .5) * .16,
        radius: Math.random() * 1.3 + .25, alpha: Math.random() * .5 + .12,
      }));
    }
    function draw() {
      context.clearRect(0, 0, width, height);
      points.forEach((point, index) => {
        if (state.motion) {
          point.x += point.vx; point.y += point.vy;
          if (point.x < 0 || point.x > width) point.vx *= -1;
          if (point.y < 0 || point.y > height) point.vy *= -1;
        }
        context.fillStyle = `rgba(120,225,255,${point.alpha})`;
        context.beginPath(); context.arc(point.x, point.y, point.radius, 0, Math.PI * 2); context.fill();
        for (let otherIndex = index + 1; otherIndex < points.length; otherIndex += 1) {
          const other = points[otherIndex]; const distance = Math.hypot(point.x - other.x, point.y - other.y);
          if (distance < 95) {
            context.strokeStyle = `rgba(78,232,255,${(1 - distance / 95) * .055})`;
            context.beginPath(); context.moveTo(point.x, point.y); context.lineTo(other.x, other.y); context.stroke();
          }
        }
      });
      requestAnimationFrame(draw);
    }
    resize(); draw(); window.addEventListener('resize', resize);
    return resize;
  }
  const resizeStars = createParticleField($('starfield'), 72);
  const resizeTheatre = createParticleField($('theatreCanvas'), 105);
  window.addEventListener('pointermove', (event) => {
    $('pointerGlow').style.left = `${event.clientX}px`; $('pointerGlow').style.top = `${event.clientY}px`;
  });

  function tickClock() {
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    const online = state.dashboard?.gateway === 'online';
    $('missionClock').innerHTML = `LOCAL TIME // ${escapeHtml(now)}<br>CORE SYNC // ${online ? '99.8%' : 'CONNECTING'}`;
  }

  function setActiveAgent(agent, coreState = '') {
    qsa('.crew-unit').forEach((node) => {
      const active = node.dataset.agent === agent;
      node.classList.toggle('active', active); node.classList.toggle('idle', !active);
      const status = qs('span', node); if (status) status.textContent = active ? agentStatuses[agent] : 'LINKED';
    });
    qsa('.crew-card').forEach((node, index) => node.classList.toggle('active', agentNames[index] === agent));
    qsa('.crew-row').forEach((node, index) => {
      const active = agentNames[index] === agent; const status = qs('.crew-status', node);
      if (status) { status.textContent = active ? 'WORKING' : 'READY'; status.classList.toggle('wait', !active); }
    });
    $('theatreAgent').textContent = agent ? agentLabels[agent] : 'CREW READY';
    if (coreState) { $('coreState').textContent = coreState; $('theatreCoreState').textContent = coreState; }
  }

  function setMissionProgress(percent, label, runState = 'RUNNING') {
    const value = Math.max(0, Math.min(100, Number(percent) || 0));
    $('progressNumber').textContent = `${Math.round(value)}%`; $('progressBar').style.width = `${value}%`;
    $('theatreProgress').textContent = `${Math.round(value)}%`; $('progressLabel').textContent = label;
    const pill = $('statePill'); pill.innerHTML = `<i></i>${escapeHtml(runState)}`;
    pill.style.color = /FAIL|CANCEL|HALT/.test(runState) ? 'var(--red)' : /PAUSE|WAIT/.test(runState) ? 'var(--gold)' : '';
    const stage = value >= 100 ? 4 : value >= 82 ? 3 : value >= 52 ? 2 : value >= 20 ? 1 : 0;
    qsa('.phase', $('phases')).forEach((node, index) => {
      node.classList.toggle('done', index < stage || value >= 100);
      node.classList.toggle('active', index === stage && value < 100);
      const small = qs('small', node);
      small.textContent = `${String(index + 1).padStart(2, '0')} · ${value >= 100 || index < stage ? 'COMPLETE' : index === stage ? 'ACTIVE' : 'QUEUED'}`;
    });
  }

  // ---------- command and mission lifecycle ----------
  function bindCommandControls() {
    qsa('.command-mode').forEach((button) => button.addEventListener('click', () => {
      state.mode = button.dataset.mode;
      qsa('.command-mode').forEach((item) => item.classList.toggle('active', item === button));
      if (state.mode === 'talking') { $('theatreInput').value = $('directive').value; openTalkingMode(true); }
    }));
    $('preview').addEventListener('click', () => {
      state.preview = !state.preview; $('preview').classList.toggle('on', state.preview);
      toast(state.preview ? 'Preview-first safety enabled.' : 'Real actions may run after permission checks.');
    });
    $('launch').addEventListener('click', executeDirective);
    $('directive').addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') executeDirective();
    });
    $('pause').addEventListener('click', () => controlCurrentTask(state.currentRun?.status === 'paused' ? 'resume' : 'pause'));
    $('cancel').addEventListener('click', () => controlCurrentTask('cancel'));
    $('kill').addEventListener('click', emergencyStop);
    $('demo').addEventListener('click', () => {
      state.motion = !state.motion; document.body.classList.toggle('motion-paused', !state.motion);
      $('demo').textContent = state.motion ? 'PAUSE AMBIENCE' : 'RESUME AMBIENCE';
      toast(state.motion ? 'Ambient systems resumed.' : 'Ambient motion paused. Live data remains connected.');
    });
  }
  async function executeDirective() {
    const input = $('directive'); const text = input.value.trim();
    if (!text) { toast('Enter a mission directive first.', true); input.focus(); return; }
    input.value = '';
    if (state.mode === 'talking') { openTalkingMode(true); await runAgentCommand(text, true); }
    else if (state.mode === 'computer') await runComputerTask(text);
    else await runAgentCommand(text, false);
  }
  function optimisticRun(runId, text, kind, talking = false) {
    state.currentRun = { id: runId, text, kind, status: 'running', started: new Date().toISOString() };
    $('pageTitle').textContent = 'The crew is working.'; $('missionTitle').textContent = text;
    $('theatreMission').textContent = text; $('runCode').textContent = runId.toUpperCase();
    $('theatreState').textContent = 'PLANNING'; setMissionProgress(6, 'Hermus is decomposing the mission', 'INITIALIZING');
    setActiveAgent('researcher', 'PLANNING'); setTalkState('thinking', 'PLANNING');
    if (talking) setCaption('OPERATOR', text);
    addEvent({ type: 'session_started', ts: new Date().toISOString(), data: { run_id: runId, text, mode: kind, talking } });
  }
  async function runAgentCommand(text, talking) {
    if (state.loading) { toast('A mission is already initializing.', true); return; }
    state.loading = true; $('launch').disabled = true;
    const runId = `run_${Math.random().toString(16).slice(2, 12)}`;
    const crew = state.mode === 'multi-agent';
    const requestedMode = crew ? (state.preview ? 'multi-chat' : 'multi-agent') : (state.preview ? 'chat' : 'agent');
    const safeText = state.preview && !crew ? `Preview and plan this task without executing tools or making changes: ${text}` : text;
    optimisticRun(runId, text, talking ? 'talking' : state.mode, talking);
    try {
      const result = await api('/command', { method: 'POST', body: {
        platform: 'dashboard', user_id: 'living_control_operator', text: safeText,
        mode: requestedMode, model: $('modelSelect').value || undefined,
        talking, speak: talking, run_id: runId,
      } });
      applyCommandResult(result, text, talking);
    } catch (error) {
      failMission(error.message); toast(error.message, true);
    } finally {
      state.loading = false; $('launch').disabled = false; refreshOverview();
    }
  }
  async function runComputerTask(text) {
    if (state.loading) { toast('A mission is already initializing.', true); return; }
    state.loading = true; $('launch').disabled = true;
    const runId = `computer_${Math.random().toString(16).slice(2, 10)}`;
    optimisticRun(runId, text, 'computer');
    try {
      const result = await api('/computer/run', { method: 'POST', body: { task: text, dry_run: state.preview } });
      state.currentRun.id = result.task_id || runId; $('runCode').textContent = state.currentRun.id.toUpperCase();
      setMissionProgress(12, state.preview ? 'Computer preview is building an action plan' : 'Computer agent is executing', 'RUNNING');
      setActiveAgent('runner', 'TOOL LINK'); toast(`Computer mission started in ${state.preview ? 'preview' : 'real-action'} mode.`);
    } catch (error) { failMission(error.message); toast(error.message, true); }
    finally { state.loading = false; $('launch').disabled = false; }
  }
  function applyCommandResult(result, originalText, talking) {
    const response = result.response || result.error || 'Mission completed without a text response.';
    const failed = Boolean(result.error);
    state.currentRun.status = failed ? 'failed' : 'done'; state.currentRun.model = result.model;
    setMissionProgress(failed ? 0 : 100, failed ? response : 'Verified response ready', failed ? 'FAILED' : 'VERIFIED');
    setActiveAgent(failed ? null : 'verifier', failed ? 'FAULT' : 'COMPLETE');
    $('theatreState').textContent = failed ? 'FAULT' : 'COMPLETE'; setCaption(failed ? 'SYSTEM FAULT' : 'HERMUS', response);
    setTalkState(failed ? 'error' : 'ready', failed ? 'FAULT' : 'MISSION COMPLETE');
    state.history.unshift({ description: originalText, mode: talking ? 'talking' : state.mode, status: failed ? 'failed' : 'verified', updated: new Date().toISOString(), model: result.model, crew: (result.tool_calls || []).length || 1 });
    if (result.speech?.audio_url) playSpeech(result.speech.audio_url, response, result.speech.audio_id);
    renderRecentRuns(state.dashboard?.tasks, state.computer);
  }
  function failMission(message) {
    if (state.currentRun) state.currentRun.status = 'failed';
    setMissionProgress(0, message, 'FAILED'); setActiveAgent(null, 'FAULT'); $('theatreState').textContent = 'FAULT';
    setCaption('SYSTEM FAULT', message); setTalkState('error', 'FAULT');
  }
  async function controlCurrentTask(action) {
    if (!state.currentRun || state.currentRun.status === 'done') { toast('No controllable mission is active.', true); return; }
    if (state.currentRun.kind !== 'computer') { toast('Pause and cancel controls apply to asynchronous computer missions.', true); return; }
    try {
      await api('/remote/control', { method: 'POST', body: { action, task_id: state.currentRun.id, reason: `dashboard ${action}` } });
      state.currentRun.status = action === 'cancel' ? 'cancelled' : action === 'pause' ? 'paused' : 'running';
      $('pause').textContent = action === 'pause' ? 'RESUME TASK' : 'PAUSE TASK';
      setMissionProgress(action === 'cancel' ? 0 : Number($('progressNumber').textContent.replace('%', '')), `Computer mission ${action} requested`, action.toUpperCase());
      toast(`Computer mission ${action} requested.`);
    } catch (error) { toast(error.message, true); }
  }
  async function emergencyStop() {
    if (!window.confirm('Engage the global computer-control halt? This immediately blocks mouse and keyboard actions.')) return;
    try {
      await api('/computer/control/emergency-stop', { method: 'POST', body: { reason: 'Living Control Room global halt' } });
      toast('Global computer-control halt engaged.', true); $('gatewayText').textContent = 'CONTROL HALTED';
    } catch (error) { toast(error.message, true); }
  }

  // ---------- live event links ----------
  const eventLabels = {
    session_started: 'Directive accepted', agent_response: 'Agent response ready', speech_ready: 'Speech buffer ready',
    speech_unavailable: 'Speech link unavailable', session_finished: 'Mission completed', session_failed: 'Mission failed',
    task_started: 'Computer mission started', plan_created: 'Execution plan created', action_started: 'Tool action started',
    action_completed: 'Tool action completed', verification_started: 'Verification started', verification_completed: 'Verification completed',
    repair_started: 'Repair sequence started', repair_completed: 'Repair completed', task_completed: 'Mission verified',
    task_failed: 'Computer mission failed', task_interrupted: 'Computer mission interrupted', emergency_stop: 'Control lock changed',
  };
  function eventLabel(event) { return eventLabels[event.type] || String(event.type || 'event').replaceAll('_', ' '); }
  function eventDetail(event) {
    const data = event.data || {};
    return clip(data.text || data.task || data.description || data.detail || data.action || data.error || data.reason || data.state || data.model || 'Telemetry packet received', 155);
  }
  function addEvent(event, historical = false) {
    const list = $('eventList');
    if (list.querySelector('.empty-state')) list.innerHTML = '';
    const row = document.createElement('div'); row.className = `event${historical ? '' : ' enter'}`;
    row.innerHTML = `<div class="event-icon ${/(complete|finished|ready|verified)/.test(event.type) ? 'ok' : ''}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 12h16M14 6l6 6-6 6"/></svg></div><div class="event-copy"><strong>${escapeHtml(eventLabel(event))}</strong><span>${escapeHtml(eventDetail(event))}</span></div><time>${historical ? relativeTime(event.ts) : 'NOW'}</time>`;
    list.prepend(row); while (list.children.length > 5) list.lastElementChild.remove();
    addTheatreEvent(event, historical);
  }
  function addTheatreEvent(event, historical = false) {
    const list = $('theatreEvents'); if (list.querySelector('.empty-state')) list.innerHTML = '';
    state.eventCount += 1; $('theatreCount').textContent = String(state.eventCount).padStart(3, '0');
    const row = document.createElement('div'); row.className = 'theatre-event';
    row.innerHTML = `<small>${historical ? relativeTime(event.ts) : 'NOW'} · ${escapeHtml(String(event.type || 'event').toUpperCase())}</small><b>${escapeHtml(eventLabel(event))}</b>`;
    list.prepend(row); while (list.children.length > 16) list.lastElementChild.remove();
  }
  function handleDashboardEvent(event, historical = false) {
    if (!event || event.kind) return; addEvent(event, historical); if (historical) return;
    const data = event.data || {};
    if (event.type === 'session_started') {
      state.currentRun = { id: data.run_id, kind: data.talking ? 'talking' : data.mode, text: data.text, status: 'running', started: event.ts };
      $('missionTitle').textContent = data.text || 'Mission active'; $('theatreMission').textContent = data.text || 'Mission active'; $('runCode').textContent = String(data.run_id || 'RUN').toUpperCase();
      $('pageTitle').textContent = 'The crew is working.'; setMissionProgress(8, 'Hermus is planning the mission', 'RUNNING'); setActiveAgent('researcher', 'PLANNING');
      if (data.talking && !state.talkOpen) openTalkingMode(false);
    } else if (event.type === 'agent_response') {
      setMissionProgress(84, 'Verifier is reviewing the generated response', 'VERIFYING'); setActiveAgent('verifier', 'VERIFYING');
      $('theatreState').textContent = 'VERIFYING'; if (data.text) setCaption('HERMUS', data.text);
    } else if (event.type === 'speech_ready') {
      setActiveAgent('runner', 'SPEAKING'); if (state.talkOpen) playSpeech(data.audio_url, data.text, data.audio_id);
    } else if (event.type === 'speech_unavailable') {
      setCaption('HERMUS', data.text || data.error); setTalkState('ready', 'TEXT RESPONSE');
    } else if (event.type === 'session_finished') {
      if (state.currentRun) state.currentRun.status = 'done'; setMissionProgress(100, 'Mission completed and verified', 'VERIFIED'); setActiveAgent('verifier', 'COMPLETE');
    } else if (event.type === 'session_failed') failMission(data.error || 'Agent operation failed');
  }
  function handleComputerEvent(event, historical = false) {
    if (!event || event.kind) return; addEvent(event, historical); if (historical) return;
    const data = event.data || {};
    if (event.type === 'task_started') {
      state.currentRun = { id: data.task_id, kind: 'computer', text: data.task, status: 'running', started: event.ts };
      $('missionTitle').textContent = data.task || 'Computer mission'; $('theatreMission').textContent = data.task || 'Computer mission'; $('runCode').textContent = String(data.task_id || 'COMPUTER').toUpperCase();
      setMissionProgress(10, 'Computer agent is preparing its plan', 'RUNNING'); setActiveAgent('runner', 'TOOL LINK');
    } else if (event.type === 'plan_created') { setMissionProgress(28, 'Execution plan is ready', 'RUNNING'); setActiveAgent('critic', 'PLAN REVIEW'); }
    else if (/action_(started|completed)/.test(event.type)) { setMissionProgress(52, eventLabel(event), 'RUNNING'); setActiveAgent('runner', 'EXECUTING'); }
    else if (/verification/.test(event.type)) { setMissionProgress(78, eventLabel(event), 'VERIFYING'); setActiveAgent('verifier', 'VERIFYING'); }
    else if (event.type === 'task_completed') { if (state.currentRun) state.currentRun.status = 'done'; setMissionProgress(100, 'Computer mission verified', 'VERIFIED'); setActiveAgent('verifier', 'COMPLETE'); }
    else if (/(task_failed|task_interrupted)/.test(event.type)) failMission(data.reason || data.error || 'Computer mission failed');
  }
  function socketUrl(path) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${location.host}${path}${token() ? `?token=${encodeURIComponent(token())}` : ''}`;
  }
  function connectSocket(name, path, handler) {
    try {
      const socket = new WebSocket(socketUrl(path)); state[`${name}Socket`] = socket;
      socket.onopen = () => { state.reconnects[name] = 1000; $('activityPulse').textContent = '● LIVE EVENT LINK'; $('talkWsStatus').classList.add('connected'); };
      socket.onmessage = (message) => {
        let packet; try { packet = JSON.parse(message.data); } catch (_) { return; }
        if (packet.kind === 'snapshot') (packet.events || []).slice(0, 10).reverse().forEach((event) => handler(event, true));
        else if (packet.kind !== 'ping') handler(packet, false);
      };
      socket.onclose = () => {
        $('activityPulse').textContent = '● RECONNECTING'; $('talkWsStatus').classList.remove('connected');
        const delay = Math.min(15000, state.reconnects[name] || 1000); state.reconnects[name] = delay * 1.7;
        setTimeout(() => connectSocket(name, path, handler), delay);
      };
      socket.onerror = () => socket.close();
    } catch (_) { setTimeout(() => connectSocket(name, path, handler), 3000); }
  }

  // ---------- Talking Mode ----------
  function openTalkingMode(nativeFullscreen) {
    const panel = $('talkMode'); state.talkOpen = true; panel.classList.add('open'); panel.setAttribute('aria-hidden', 'false'); document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => { resizeTheatre(); $('theatreInput').focus(); });
    if (nativeFullscreen && panel.requestFullscreen && !document.fullscreenElement) panel.requestFullscreen().catch(() => {});
  }
  function closeTalkingMode() {
    state.talkOpen = false; $('talkMode').classList.remove('open'); $('talkMode').setAttribute('aria-hidden', 'true'); document.body.style.overflow = '';
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    if (state.recorder?.state === 'recording') state.recorder.stop();
  }
  function setTalkState(kind, label) {
    const chamber = $('theatreChamber'); chamber.classList.remove('thinking', 'speaking');
    if (kind === 'thinking') chamber.classList.add('thinking'); if (kind === 'speaking' || kind === 'listening') chamber.classList.add('speaking');
    $('theatreState').textContent = label; if (!/LIVE CAPTION/.test($('captionSpeaker').textContent)) $('captionSpeaker').textContent = `HERMUS // ${label}`;
  }
  function setCaption(speaker, text) { $('captionSpeaker').textContent = `${String(speaker).toUpperCase()} // LIVE CAPTION`; $('captionText').textContent = text || '—'; }
  async function playSpeech(url, text, audioId) {
    if (!url || (audioId && state.playedAudio.has(audioId))) return;
    if (audioId) state.playedAudio.add(audioId); if (text) setCaption('HERMUS', text);
    const audio = $('talkAudio'); audio.src = url; audio.muted = state.muted;
    try { await audio.play(); } catch (_) { setTalkState('ready', 'AUDIO READY'); toast('Generated audio is ready. Interact with the page to allow playback.'); }
  }
  async function sendTalkingDirective() {
    const input = $('theatreInput'); const text = input.value.trim();
    if (!text) { toast('Voice channel is waiting for a directive.', true); input.focus(); return; }
    input.value = ''; state.mode = 'talking'; await runAgentCommand(text, true);
  }
  async function toggleRecording() {
    if (state.recorder?.state === 'recording') { state.recorder.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { toast('This browser does not support microphone recording.', true); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true }); state.recordingStream = stream; state.recordingChunks = [];
      const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find((item) => MediaRecorder.isTypeSupported(item));
      state.recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
      state.recorder.ondataavailable = (event) => { if (event.data.size) state.recordingChunks.push(event.data); };
      state.recorder.onstop = transcribeRecording; state.recorder.start(); $('microphone').classList.add('recording'); setTalkState('listening', 'LISTENING'); setCaption('OPERATOR', 'Listening… select the microphone again when finished.');
    } catch (error) { toast(`Microphone access failed: ${error.message}`, true); }
  }
  async function transcribeRecording() {
    $('microphone').classList.remove('recording'); state.recordingStream?.getTracks().forEach((track) => track.stop());
    const type = state.recorder?.mimeType || 'audio/webm'; const blob = new Blob(state.recordingChunks, { type });
    if (!blob.size) { setTalkState('ready', 'NO AUDIO'); return; }
    setTalkState('thinking', 'TRANSCRIBING'); setCaption('HERMUS', 'Converting microphone audio to text locally…');
    try {
      const result = await api('/speech/transcribe?model=base', { method: 'POST', headers: { 'Content-Type': type }, body: blob });
      const text = String(result.text || '').trim(); if (!text) throw new Error('No speech was detected');
      setCaption('OPERATOR', text); await runAgentCommand(text, true);
    } catch (error) { setTalkState('error', 'TRANSCRIPTION FAULT'); setCaption('SYSTEM FAULT', error.message); toast(error.message, true); }
  }
  function bindTalkingMode() {
    $('watch').addEventListener('click', () => openTalkingMode(true)); $('theatreExit').addEventListener('click', closeTalkingMode);
    $('audioToggle').addEventListener('click', () => { state.muted = !state.muted; $('talkAudio').muted = state.muted; $('audioToggle').textContent = state.muted ? 'AUDIO OFF' : 'AUDIO ON'; });
    $('microphone').addEventListener('click', toggleRecording); $('theatreSend').addEventListener('click', sendTalkingDirective);
    $('theatreInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); sendTalkingDirective(); } });
    $('talkStop').addEventListener('click', async () => { $('talkAudio').pause(); if (state.recorder?.state === 'recording') state.recorder.stop(); if (state.currentRun?.kind === 'computer') await controlCurrentTask('cancel'); setTalkState('ready', 'STOPPED'); });
    $('talkAudio').addEventListener('play', () => setTalkState('speaking', 'SPEAKING'));
    $('talkAudio').addEventListener('ended', () => setTalkState('ready', 'AWAITING DIRECTIVE'));
  }

  // ---------- real overview data ----------
  function populateModels(keys) {
    if (!keys) return; const models = [];
    Object.entries(keys.llm_keys || {}).forEach(([provider, entries]) => (entries || []).forEach((entry) => {
      const model = entry.default_model || entry.model; if (model) models.push(`${provider}/${model}`);
    }));
    $('modelSelect').innerHTML = '<option value="">AUTO-ROUTE MODEL</option>' + [...new Set(models)].map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model.toUpperCase())}</option>`).join('');
  }
  function populateWorkspaces(workspace) {
    if (!workspace) return; const current = workspace.current || 'default';
    const projects = normalizeList(workspace.projects).map((item) => typeof item === 'string' ? item : item.name).filter(Boolean);
    $('workspaceSelect').innerHTML = `<option value="">WORKSPACE · ${escapeHtml(String(current).toUpperCase())}</option>` + projects.filter((name) => name !== current).map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(String(name).toUpperCase())}</option>`).join('');
  }
  function renderSystems(dashboard, computer) {
    const speech = dashboard?.speech || {}; const channels = dashboard?.channels || {};
    const channelOnline = Object.values(channels).some((value) => value && (value.running || value.connected || value === true));
    const halted = Boolean(computer?.control?.emergency_stopped || computer?.halted);
    const rows = [
      ['Gateway', dashboard?.gateway === 'online' ? 'FastAPI core linked' : 'No response', dashboard?.gateway === 'online' ? 100 : 8, dashboard?.gateway === 'online' ? 'NOMINAL' : 'OFFLINE', false],
      ['Computer agent', halted ? 'Global action lock engaged' : 'Action engine available', halted ? 10 : 88, halted ? 'HALTED' : 'NOMINAL', halted],
      ['Speech engine', speech.backend || 'Local TTS', speech.available ? 82 : 12, speech.available ? 'NOMINAL' : 'OFFLINE', !speech.available],
      ['Channels', channelOnline ? 'External channel linked' : 'Telegram / Discord standby', channelOnline ? 74 : 16, channelOnline ? 'NOMINAL' : 'STANDBY', !channelOnline],
    ];
    $('telemetryList').innerHTML = rows.map(([name, sub, level, status, warning]) => `<div class="telemetry-row"><div><b>${escapeHtml(name)}</b><span>${escapeHtml(sub)}</span></div><div class="meter"><i style="width:${level}%;background:${warning ? 'var(--gold)' : 'var(--cyan)'}"></i></div><em class="${warning ? 'warn' : ''}">${escapeHtml(status)}</em></div>`).join('');
  }
  function renderGate(approvals) {
    const pending = approvals?.status?.pending || approvals?.pending || []; const first = pending[0];
    if (!first) { $('approvalBody').innerHTML = '<div class="gate-clear"><b>✓ AUTHORIZATION GATE CLEAR</b><p>No sensitive action is waiting for approval.</p></div>'; return; }
    $('approvalBody').innerHTML = `<div class="approval-type"><i>!</i>${escapeHtml(String(first.risk || 'medium').toUpperCase())} RISK</div><h3>${escapeHtml(first.description || first.action || 'Authorize pending action?')}</h3><p>The agent remains blocked until the local operator decides.</p><div class="approval-path">ACTION // ${escapeHtml(first.action || 'unknown')}<br>ARGS // ${escapeHtml(clip(JSON.stringify(first.args || {}), 180))}</div><div class="approval-actions"><button class="approve" data-approval="approve" data-id="${escapeHtml(first.prompt_id)}">AUTHORIZE ONCE</button><button class="reject" data-approval="reject" data-id="${escapeHtml(first.prompt_id)}">REJECT</button></div>`;
  }
  async function resolveApproval(id, action) {
    try { await api(`/remote/${action}`, { method: 'POST', body: { prompt_id: id, by: 'living_control_operator' } }); toast(action === 'approve' ? 'Action authorized once.' : 'Action rejected.'); refreshOverview(); }
    catch (error) { toast(error.message, true); }
  }
  function renderRecentRuns(tasks, computer) {
    const active = (tasks?.active_tasks || []).map((item) => ({ ...item, source: 'gateway', updated: item.last_update || item.started }));
    const completed = (tasks?.completed_tasks || []).map((item) => ({ ...item, source: 'gateway', updated: item.ended || item.last_update }));
    const computerTasks = (computer?.tasks || []).map((item) => ({ ...item, source: 'computer', description: item.task || item.description, updated: item.updated_at || item.created_at }));
    const runs = [...state.history, ...active, ...computerTasks, ...completed].sort((a, b) => new Date(b.updated || 0) - new Date(a.updated || 0)).slice(0, 10);
    $('recentRuns').innerHTML = runs.length ? runs.map((run) => `<tr><td><b>${escapeHtml(clip(run.description || run.task || run.name || run.task_id || 'Untitled mission', 76))}</b><small>${escapeHtml(run.task_id || run.id || relativeTime(run.updated))}</small></td><td>${escapeHtml(String(run.mode || run.type || run.source || 'agent').toUpperCase())}</td><td>${escapeHtml(String(run.crew || run.agent || run.model || 'AUTO'))}</td><td>${escapeHtml(run.duration || relativeTime(run.updated))}</td><td><span class="result ${/fail|cancel|interrupt/.test(String(run.status)) ? 'fault' : ''}">${escapeHtml(String(run.status || 'verified').toUpperCase())}</span></td></tr>`).join('') : '<tr><td colspan="5" class="empty-state">NO LOCAL MISSION RECORDS YET</td></tr>';
  }
  function adoptActiveMission(tasks, computer) {
    if (state.currentRun && !['done', 'failed', 'cancelled'].includes(state.currentRun.status)) return;
    const gatewayTask = tasks?.active_tasks?.[0];
    const computerTask = computer?.tasks?.find((item) => item?.status === 'running') || (computer?.current_task?.status === 'running' ? computer.current_task : null);
    const task = computerTask || gatewayTask;
    if (!task) return;
    const kind = computerTask ? 'computer' : 'agent';
    const id = task.task_id || task.id || `${kind}_active`;
    const text = task.task || task.description || task.name || 'Active Hermus mission';
    const rawProgress = Number.parseFloat(String(task.progress ?? task.percent ?? ''));
    const progress = Number.isFinite(rawProgress) ? rawProgress : 34;
    state.currentRun = { id, text, kind, status: 'running', started: task.started || task.created_at };
    $('pageTitle').textContent = 'The crew is working.'; $('missionTitle').textContent = text; $('theatreMission').textContent = text; $('runCode').textContent = String(id).toUpperCase();
    setMissionProgress(progress, kind === 'computer' ? 'Computer agent mission is active' : 'Gateway agent mission is active', 'RUNNING');
    setActiveAgent(kind === 'computer' ? 'runner' : 'researcher', kind === 'computer' ? 'TOOL LINK' : 'WORKING');
  }

  async function refreshOverview() {
    const [dashboard, computer, approvals, keys, workspace, agents] = await Promise.all([
      settled('/dashboard/status'), settled('/computer/status'), settled('/remote/approvals'), settled('/keys/list'), settled('/workspace'), settled('/agents'),
    ]);
    if (dashboard.ok) {
      state.dashboard = dashboard.data; $('gatewayText').textContent = 'CORE ONLINE'; $('gatewayLed').classList.remove('offline-led'); tickClock();
      renderSystems(dashboard.data, computer.ok ? computer.data : null); renderRecentRuns(dashboard.data.tasks, computer.ok ? computer.data : null);
      adoptActiveMission(dashboard.data.tasks, computer.ok ? computer.data : null);
      const totalAgents = Number(dashboard.data.agents || 0) + normalizeList(agents.ok ? agents.data.agents : []).length;
      const visibleCrew = totalAgents || 4;
      const coreCopy = qs('.core-link p'); if (coreCopy) coreCopy.innerHTML = `${visibleCrew} agents synchronized<br>Local execution secured`;
      const agentBadge = qs('[data-view="agents"] em'); if (agentBadge) agentBadge.textContent = visibleCrew;
      const tracked = dashboard.data.tasks || {};
      $('missionBadge').textContent = (tracked.active_tasks?.length || 0) + (tracked.completed_tasks?.length || 0) + (computer.ok ? (computer.data.tasks?.length || 0) : 0);
      $('computerBadge').textContent = computer.ok ? (computer.data.task_stats?.running || computer.data.tasks?.filter((item) => item?.status === 'running').length || 0) : 0;
    } else {
      $('gatewayText').textContent = 'CORE OFFLINE'; $('gatewayLed').classList.add('offline-led'); renderSystems(null, null);
    }
    if (computer.ok) state.computer = computer.data;
    if (approvals.ok) { state.approvals = approvals.data; renderGate(approvals.data); }
    else $('approvalBody').innerHTML = `<div class="gate-clear"><b class="fault">AUTHORIZATION LINK OFFLINE</b><p>${escapeHtml(approvals.error)}</p></div>`;
    if (keys.ok) populateModels(keys.data); if (workspace.ok) populateWorkspaces(workspace.data);
  }

  // ---------- connected modules ----------
  function switchView(view, updateHash = true) {
    if (view === 'command') {
      state.view = view; $('commandPage').style.display = 'block'; $('modulePage').classList.remove('show'); $('crumb').textContent = 'Control Room';
    } else {
      if (!viewMeta[view]) view = 'missions'; state.view = view; $('commandPage').style.display = 'none'; $('modulePage').classList.add('show'); $('crumb').textContent = viewMeta[view][1]; renderModule(view);
    }
    qsa('.nav-button').forEach((button) => button.classList.toggle('active', button.dataset.view === view)); document.body.classList.remove('menu-open');
    if (updateHash && location.hash !== `#${view}`) history.replaceState(null, '', `#${view}`);
  }
  function card(title, body, full = false) { return `<article class="module-card ${full ? 'full' : ''}"><header><h3>${escapeHtml(title)}</h3></header>${body}</article>`; }
  function keyValues(values) { return `<div class="kv-list">${values.map(([key, value]) => `<div class="kv"><span>${escapeHtml(key)}</span><b>${escapeHtml(value ?? '—')}</b></div>`).join('')}</div>`; }
  function rawCard(title, value, full = false) { return card(title, `<pre class="code-block">${safeJson(value)}</pre>`, full); }
  async function renderModule(view) {
    const meta = viewMeta[view]; $('moduleKicker').textContent = meta[0]; $('moduleTitle').textContent = meta[1]; $('moduleDescription').textContent = meta[2]; $('moduleLoading').style.display = 'block'; $('moduleContent').innerHTML = ''; $('moduleActions').innerHTML = '<button class="action-btn" id="moduleRefresh">REFRESH DATA</button>';
    try {
      let content = '';
      if (view === 'missions') {
        const [missionsData, artsData, rbData] = await Promise.all([
          settled('/missions'), settled('/artifacts'), settled('/rollback/checkpoints')
        ]);
        const missions = (missionsData.ok && missionsData.data && missionsData.data.missions) ? missionsData.data.missions : [];
        const artifacts = (artsData.ok && artsData.data && artsData.data.artifacts) ? artsData.data.artifacts : [];
        const checkpoints = (rbData.ok && rbData.data && rbData.data.checkpoints) ? rbData.data.checkpoints : [];

        let missionCards = '';
        if (missions.length === 0) {
          missionCards = card('MISSION STATUS', '<p style="color:var(--text-dim);font-size:13px;">No active missions in workspace. Start one via CLI (<code>hermus mission start "goal"</code>) or API (<code>POST /missions</code>).</p>');
        } else {
          for (const m of missions) {
            let stepsHtml = '<ul style="list-style:none;padding:0;margin:8px 0;font-size:12px;">';
            for (const sg of (m.subgoals || [])) {
              let icon = '■';
              let color = '#8892b0';
              if (sg.status === 'completed' || sg.status === 'done') { icon = '✓'; color = '#00f5a0'; }
              else if (sg.status === 'running') { icon = '→'; color = '#00e5ff'; }
              else if (sg.status === 'failed') { icon = '✕'; color = '#ff0055'; }
              else if (sg.status === 'blocked') { icon = '⚠'; color = '#ffaa00'; }
              stepsHtml += `<li style="color:${color};margin:4px 0;">${icon} <b>${escapeHtml(sg.goal)}</b> <span style="opacity:0.7;">(${sg.status})</span></li>`;
            }
            stepsHtml += '</ul>';

            let evidenceHtml = '<div style="font-size:11px;color:var(--text-dim);margin-top:6px;">';
            evidenceHtml += `<div>Evidence items: <b>${(m.evidence || []).length}</b> | Artifacts: <b>${(m.artifacts || []).length}</b></div>`;
            evidenceHtml += `<div>Confidence score: <b>${Math.round((m.confidence_score || 0) * 100)}%</b> | Domain: <b>${escapeHtml(m.domain || 'generic')}</b></div>`;
            if (m.blocker_reason) {
              evidenceHtml += `<div style="color:#ffaa00;margin-top:4px;">⚠ Blocker: ${escapeHtml(m.blocker_reason)}</div>`;
            }
            evidenceHtml += '</div>';

            const mBody = `
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-weight:700;font-size:14px;color:#fff;">${escapeHtml(m.goal)}</span>
                <span class="badge" style="background:${m.state === 'completed' ? '#00f5a022' : (m.state === 'blocked' ? '#ffaa0022' : '#00e5ff22')};color:${m.state === 'completed' ? '#00f5a0' : (m.state === 'blocked' ? '#ffaa00' : '#00e5ff')};border:1px solid currentColor;">${escapeHtml((m.state || 'pending').toUpperCase())} ${m.progress_pct || 0}%</span>
              </div>
              <div style="background:rgba(255,255,255,0.08);height:6px;border-radius:3px;overflow:hidden;margin-bottom:10px;">
                <div style="background:linear-gradient(90deg,#00e5ff,#00f5a0);height:100%;width:${m.progress_pct || 0}%;"></div>
              </div>
              ${stepsHtml}
              ${evidenceHtml}
            `;
            missionCards += card(`MISSION [${m.mission_id}]`, mBody);
          }
        }

        let artHtml = '';
        if (artifacts.length === 0) {
          artHtml = '<p style="color:var(--text-dim);font-size:12px;">No build deliverables or artifacts produced yet.</p>';
        } else {
          artHtml = '<table style="width:100%;font-size:11px;border-collapse:collapse;"><thead><tr style="text-align:left;color:var(--text-dim);border-bottom:1px solid rgba(255,255,255,0.1);"><th>NAME</th><th>TYPE</th><th>SIZE</th></tr></thead><tbody>';
          for (const a of artifacts) {
            artHtml += `<tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td><b>${escapeHtml(a.name)}</b></td><td><span class="badge">${escapeHtml(a.artifact_type)}</span></td><td>${a.size_bytes} B</td></tr>`;
          }
          artHtml += '</tbody></table>';
        }

        let chkHtml = '';
        if (checkpoints.length === 0) {
          chkHtml = '<p style="color:var(--text-dim);font-size:12px;">No recovery checkpoints created.</p>';
        } else {
          chkHtml = '<ul style="list-style:none;padding:0;font-size:11px;">';
          for (const c of checkpoints.slice(0, 5)) {
            chkHtml += `<li style="margin:4px 0;"><span style="color:#00e5ff;">${c.id}</span>: ${escapeHtml(c.label)} <span style="color:var(--text-dim);">(${c.timestamp})</span></li>`;
          }
          chkHtml += '</ul>';
        }

        content = missionCards + card('ARTIFACTS & DELIVERABLES', artHtml) + card('RECOVERY CHECKPOINTS', chkHtml);
      } else if (view === 'computer') {
        const [status, control, resources] = await Promise.all([api('/computer/status'), api('/computer/control/status'), settled('/computer/resources')]);
        $('moduleActions').innerHTML += '<button class="action-btn" id="openComputer">OPEN FULL COMPUTER CONTROL</button>';
        content = card('Control state', keyValues([['Emergency stop', control.emergency_stopped ? 'ENGAGED' : 'CLEAR'], ['Running', control.running_tasks?.length || 0], ['Paused', control.paused_tasks?.length || 0]])) + rawCard('Current computer mission', status.current_task || { status: 'No task' }) + rawCard('Resources', resources.ok ? resources.data : { error: resources.error }, true);
      } else if (view === 'agents') {
        const [persistent, live] = await Promise.all([api('/agents'), api('/agents/status')]);
        content = rawCard('Persistent robotic crew', persistent) + rawCard('Live workers', live);
      } else if (view === 'memory') {
        const [embedding, episodes, evaluation] = await Promise.all([settled('/embeddings/status'), settled('/computer/episodes/stats'), settled('/eval/summary')]);
        content = rawCard('Embedding memory', embedding.ok ? embedding.data : { error: embedding.error }) + rawCard('Recorded episodes', episodes.ok ? episodes.data : { error: episodes.error }) + rawCard('Evaluation memory', evaluation.ok ? evaluation.data : { error: evaluation.error }, true);
      } else if (view === 'models') {
        const [keys, providers, health] = await Promise.all([api('/keys/list'), settled('/providers'), settled('/keys/health')]);
        content = rawCard('Configured model routes', keys) + rawCard('Provider catalog', providers.ok ? providers.data : { error: providers.error }) + rawCard('Key health', health.ok ? health.data : { error: health.error }, true);
      } else if (view === 'connections') {
        const [channels, tools, mcp] = await Promise.all([api('/channels/status'), api('/tools'), settled('/mcp/servers')]);
        content = rawCard('Channel links', channels) + rawCard('Tool registry', tools) + rawCard('MCP capability servers', mcp.ok ? mcp.data : { error: mcp.error }, true);
      } else if (view === 'settings') {
        const [dashboard, speech, workspace, cache, update] = await Promise.all([api('/dashboard/status'), api('/speech/status'), api('/workspace'), api('/cache/stats'), settled('/update/check')]);
        $('moduleActions').innerHTML += '<button class="action-btn" id="clearCache">CLEAR CACHE</button>';
        content = card('Living Control status', keyValues([['Gateway', dashboard.gateway], ['Version', dashboard.version], ['Local only', dashboard.local], ['Speech', speech.available ? speech.backend : 'Unavailable']])) + rawCard('Workspace', workspace) + rawCard('Cache', cache) + rawCard('Update status', update.ok ? update.data : { error: update.error }, true);
      }
      $('moduleContent').innerHTML = content || card('Module', '<div class="empty-state">No data returned.</div>', true);
      bindModuleButtons();
    } catch (error) {
      $('moduleContent').innerHTML = `<article class="module-card full connection-error"><div class="empty-state">MODULE LINK FAILED<br>${escapeHtml(error.message)}</div></article>`;
    } finally { $('moduleLoading').style.display = 'none'; $('moduleRefresh')?.addEventListener('click', () => renderModule(view)); }
  }
  function bindModuleButtons() {
    $('openComputer')?.addEventListener('click', () => { location.href = '/computer/dashboard'; });
    $('clearCache')?.addEventListener('click', async () => { try { await api('/cache/clear', { method: 'POST' }); toast('Gateway caches cleared.'); renderModule('settings'); } catch (error) { toast(error.message, true); } });
  }
  function bindShell() {
    qsa('.nav-button').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));
    qsa('[data-open-view]').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.openView)));
    $('menu').addEventListener('click', () => document.body.classList.toggle('menu-open'));
    $('approval').addEventListener('click', (event) => { const button = event.target.closest('[data-approval]'); if (button) resolveApproval(button.dataset.id, button.dataset.approval); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && state.talkOpen) closeTalkingMode(); });
    window.addEventListener('hashchange', () => switchView(location.hash.slice(1) || 'command', false));
  }

  async function start() {
    bindShell(); bindCommandControls(); bindTalkingMode(); tickClock(); setInterval(tickClock, 1000);
    switchView(location.hash.slice(1) || 'command', false); connectSocket('dashboard', '/dashboard/events', handleDashboardEvent); connectSocket('computer', '/computer/events', handleComputerEvent);
    await refreshOverview(); setInterval(() => { if (!document.hidden) refreshOverview(); }, 9000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { resizeStars(); refreshOverview(); } });
  }
  start().catch((error) => { $('gatewayText').textContent = 'CORE OFFLINE'; $('gatewayLed').classList.add('offline-led'); toast(error.message, true); });
})();
