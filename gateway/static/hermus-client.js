/*!
 * hermus-client.js — the single frontend runtime/API client for every Hermus
 * dashboard (main control room, JARVIS spatial HUD, computer deck, remote).
 *
 * Why this exists
 * ---------------
 * The repo grew four semi-independent frontends, each with its own copy of
 * "how to talk to the backend" (dashboard.html, jarvis_dashboard.html,
 * dashboard_computer.html, remote.html, living-deck.js). That is how the main
 * dashboard became queue-first while /jarvis quietly stayed a dead UI that
 * never issued a single request.
 *
 * Everything surfaces share now:
 *
 *   HermusClient.sendCommand({ text, files, ... })   queue-first execution
 *   HermusClient.openStream(runId, handlers)         SSE events
 *   HermusClient.missions.{list,get,resume,extend}   mission lifecycle
 *   HermusClient.capabilities()                      model capability report
 *   HermusClient.steer(runId, text)                  mid-run steering
 *
 * Queue-first contract
 * --------------------
 * A turn is always submitted with `async: true` so it runs as a durable
 * gateway job: closing the tab does not cancel the work, and every surface
 * (including attachments, which are uploaded as multipart/form-data) goes
 * through the same `runtime.turn` job kind and therefore the same universal
 * mission runtime. If the queue is unavailable the client falls back to the
 * inline HTTP response and labels it (`transport: 'inline'`).
 *
 * Mission failures are surfaced, not hidden: `sendCommand` resolves with
 * `{ ok: false, failure: { stage, reason, recoverable, resumable,
 * resume_command, resume_api } }` when the runtime reports MISSION FAILED.
 */
(function (global) {
  'use strict';

  var DEFAULT_TIMEOUT = 1800;
  var POLL_MS = 2500;

  function _rand(n) { return Math.random().toString(16).slice(2, 2 + (n || 8)); }

  async function jget(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  }

  async function jpost(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`${r.status}: ${text.slice(0, 300)}`);
    try { return JSON.parse(text); } catch (e) { return {}; }
  }

  /**
   * Subscribe to a run's SSE stream.
   *
   * handlers: { onOpen, onDelta(text), onEvent(type, data), onFinal(text),
   *             onJobFinished(data), onError(err) }
   * Returns a handle with close().
   */
  function openStream(runId, handlers) {
    handlers = handlers || {};
    let es = null;
    try {
      es = new EventSource(`/stream/run/${encodeURIComponent(runId)}`);
    } catch (err) {
      if (handlers.onError) handlers.onError(err);
      return { close() {}, source: null };
    }
    if (handlers.onOpen) handlers.onOpen(es);

    const parse = (ev) => { try { return JSON.parse(ev.data); } catch (e) { return {}; } };
    const seen = new Set();
    const bind = (type, fn) => {
      if (seen.has(type)) return;
      seen.add(type);
      es.addEventListener(type, (ev) => {
        const data = parse(ev);
        if (handlers.onEvent) handlers.onEvent(type, data);
        try { fn(data, ev); } catch (e) { /* a UI handler must never kill the stream */ }
      });
    };

    bind('llm_delta', (d) => { if (handlers.onDelta && d.text) handlers.onDelta(String(d.text)); });
    bind('agent_response', (d) => { if (handlers.onFinal && d.text) handlers.onFinal(String(d.text)); });
    const onDone = (type) => bind(type, (d) => {
      if (handlers.onJobFinished) handlers.onJobFinished(d, type);
    });
    ['job_finished', 'run_finished', 'run_error', 'session_finished', 'stream_timeout'].forEach(onDone);
    // generic mirror of everything else (tool calls, mission state, …)
    ['tool_call', 'tool_result', 'mission_runtime_started', 'mission_started', 'mission_state',
     'mission_verification', 'mission_repair', 'mission_error', 'mission_finished',
     'node_started', 'node_finished', 'steer_applied', 'turn_started',
     'model_capability_warning', 'skill_created', 'verification'].forEach((t) => bind(t, () => {}));

    es.onerror = () => { /* EventSource auto-reconnects; the POST/job resolves the turn */ };
    return {
      source: es,
      close() { try { es.close(); } catch (e) {} },
    };
  }

  function _buildBody(opts) {
    const files = opts.files || [];
    const runId = opts.runId || ('run_' + _rand(6) + Date.now().toString(16));
    const fields = {
      platform: opts.platform || 'dashboard',
      user_id: opts.userId || 'user-01',
      text: opts.text,
      mode: opts.mode || 'agent',
      run_id: runId,
      stream: opts.stream === false ? 'false' : 'true',
      // queue-first: the turn runs as a durable gateway job
      async: 'true',
      timeout: String(opts.timeout || DEFAULT_TIMEOUT),
    };
    if (opts.model) fields.model = opts.model;
    if (opts.provider) fields.provider = opts.provider;
    if (opts.keyName) fields.key_name = opts.keyName;
    if (opts.prefer) fields.prefer = opts.prefer;
    if (opts.autonomous) fields.autonomous = 'true';
    return { runId, files, fields };
  }

  /**
   * Submit a turn. Queue-first; resolves with a normalised result.
   *
   * { ok, response, run_kind, mission_id, state, jobId, runId, transport,
   *   failure, error, raw }
   */
  async function sendCommand(opts) {
    opts = opts || {};
    const { runId, files, fields } = _buildBody(opts);
    let stream = null;
    let streamedText = '';
    let gotFinal = false;

    // 1. subscribe before submitting so no event is missed
    stream = openStream(runId, {
      onOpen: (es) => { if (opts.onStreamOpen) opts.onStreamOpen(es); },
      onDelta: (piece) => {
        streamedText += piece;
        if (opts.onDelta) opts.onDelta(piece, streamedText);
      },
      onEvent: (type, data) => { if (opts.onEvent) opts.onEvent(type, data); },
      onFinal: (text) => {
        gotFinal = true;
        if (text) streamedText = text;
        if (opts.onFinal) opts.onFinal(text);
      },
      onJobFinished: (data, type) => { if (opts.onJobFinished) opts.onJobFinished(data, type); },
    });

    // 2. submit (multipart when attachments are present so bytes reach the agent)
    let payload;
    try {
      let fetchOpts;
      if (files.length) {
        const fd = new FormData();
        Object.keys(fields).forEach((k) => fd.append(k, fields[k]));
        files.forEach((s) => { if (s && s.file) fd.append('files', s.file, s.name); });
        fetchOpts = { method: 'POST', body: fd };
      } else {
        fetchOpts = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(Object.assign({}, fields, { async: true, stream: fields.stream !== 'false' })),
        };
      }
      const res = await fetch('/command', fetchOpts);
      payload = await res.json().catch(() => ({ error: `Bad response (HTTP ${res.status})` }));
    } catch (err) {
      if (stream) stream.close();
      return { ok: false, error: err.message || String(err), runId, transport: 'none' };
    }

    if (payload && payload.error) {
      if (stream) setTimeout(() => stream.close(), 1500);
      return { ok: false, error: payload.error, runId, transport: 'inline', raw: payload };
    }

    // 3a. queued: the job owns the turn; poll until it settles
    if (payload && payload.async && payload.job_id) {
      const jobId = payload.job_id;
      if (opts.onJobId) opts.onJobId(jobId);
      const deadline = Date.now() + (opts.timeout || DEFAULT_TIMEOUT) * 1000;
      let missing = 0;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        let st = null;
        try {
          st = await jget(`/jobs/${jobId}`);
          missing = 0;
        } catch (e) {
          // A job the gateway does not know about will never appear: give up
          // instead of spinning until the timeout (the SSE path already had
          // its chance to deliver the answer).
          missing += 1;
          if (missing >= 5) {
            if (stream) stream.close();
            return {
              ok: false, error: `job ${jobId} is unknown to the gateway`,
              jobId, runId, transport: 'queue',
            };
          }
          continue;
        }
        if (opts.onJobStatus) opts.onJobStatus(st);
        if (st && st.status === 'succeeded') {
          let result = null;
          try { result = (await jget(`/jobs/${jobId}/result`)).result || {}; } catch (e) { result = {}; }
          if (stream) setTimeout(() => stream.close(), 1200);
          return _normalise(result, { jobId, runId, transport: 'queue', streamedText, gotFinal });
        }
        if (st && (st.status === 'failed' || st.status === 'cancelled')) {
          if (stream) setTimeout(() => stream.close(), 1200);
          return { ok: false, error: st.error || `job ${st.status}`, jobId, runId, transport: 'queue' };
        }
      }
      if (stream) stream.close();
      return { ok: false, error: 'timed out waiting for job', jobId, runId, transport: 'queue' };
    }

    // 3b. inline (queue disabled): the HTTP response is authoritative
    if (stream) setTimeout(() => stream.close(), 1500);
    return _normalise(payload || {}, { runId, transport: 'inline', streamedText, gotFinal });
  }

  function _normalise(result, meta) {
    const response = result.response || result.final_answer || result.final_proof || meta.streamedText || '';
    const failure = result.failure || (result.run_kind === 'mission_failed' ? {
      stage: 'mission', reason: result.mission_error || 'mission failed',
    } : null);
    const out = Object.assign({}, meta, {
      ok: !failure && !result.error,
      response,
      run_kind: result.run_kind,
      mission_id: result.mission_id,
      state: result.state,
      failure,
      error: result.error || null,
      raw: result,
    });
    return out;
  }

  /** Format a mission failure for a chat bubble (never hide it). */
  function formatFailure(result) {
    const f = (result && result.failure) || {};
    const lines = [
      '⚠️ MISSION FAILED — the run stopped before completing the goal.',
      f.stage ? `stage: ${f.stage}` : null,
      f.reason ? `reason: ${f.reason}` : (result && result.error) || null,
      f.recoverable === false ? 'recoverable: no' : null,
      f.resume_command ? `resume: ${f.resume_command}` : null,
    ].filter(Boolean);
    return lines.join('\n');
  }

  const HermusClient = {
    version: '2.3-queue-first',
    DEFAULT_TIMEOUT,
    jget,
    jpost,
    openStream,
    sendCommand,
    formatFailure,
    steer: (runId, text) => jpost('/run/steer', { run_id: runId, text }),
    cancel: (runId) => jpost(`/run/cancel/${encodeURIComponent(runId)}`, {}).catch(() => ({})),
    capabilities: (model) => jget('/models/capabilities' + (model ? `?model=${encodeURIComponent(model)}` : '')),
    missions: {
      list: () => jget('/missions'),
      get: (id) => jget(`/missions/${encodeURIComponent(id)}`),
      resume: (id, restartFailed) =>
        jpost(`/missions/${encodeURIComponent(id)}/resume?restart_failed=${restartFailed ? 'true' : 'false'}`, {}),
      extend: (id, steps, emergency) =>
        jpost(`/missions/${encodeURIComponent(id)}/extend?steps=${steps || 10}&emergency=${emergency ? 'true' : 'false'}`, {}),
    },
    jobs: {
      status: (id) => jget(`/jobs/${id}`),
      result: (id) => jget(`/jobs/${id}/result`),
    },
  };

  global.HermusClient = HermusClient;
  if (typeof module !== 'undefined' && module.exports) module.exports = HermusClient;
})(typeof window !== 'undefined' ? window : globalThis);
