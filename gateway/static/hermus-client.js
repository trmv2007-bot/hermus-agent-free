/* Hermus' single browser transport: queue-first commands, SSE and control. */
(function (global) {
  'use strict';
  const DEFAULT_TIMEOUT = 1800;
  const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

  class HermusHttpError extends Error {
    constructor(message, status, body) { super(message); this.name = 'HermusHttpError'; this.status = status; this.body = body; }
  }
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const randomId = () => 'run_' + Math.random().toString(16).slice(2, 10) + Date.now().toString(16);

  async function request(path, options) {
    options = Object.assign({}, options || {});
    const timeoutMs = options.timeoutMs || 20000;
    delete options.timeoutMs;
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const external = options.signal;
    let timer;
    if (controller) {
      options.signal = controller.signal;
      if (external) external.addEventListener('abort', () => controller.abort(), { once: true });
      timer = setTimeout(() => controller.abort(), timeoutMs);
    }
    let response;
    try { response = await fetch(path, options); }
    catch (error) {
      if (timer) clearTimeout(timer);
      if (error && error.name === 'AbortError') throw new HermusHttpError(`Request timed out: ${path}`, 0, null);
      throw new HermusHttpError(`Gateway unavailable: ${error.message || error}`, 0, null);
    }
    if (timer) clearTimeout(timer);
    const raw = await response.text();
    let body = null;
    if (raw) {
      try { body = JSON.parse(raw); }
      catch (_) { if (response.ok) throw new HermusHttpError(`Malformed JSON from ${path}`, response.status, raw.slice(0, 500)); }
    }
    if (!response.ok) {
      const detail = body && (body.error || body.detail);
      throw new HermusHttpError(detail || `HTTP ${response.status} ${response.statusText}`, response.status, body || raw.slice(0, 500));
    }
    return body || {};
  }
  const jget = (path, options) => request(path, options);
  const jpost = (path, body, options) => request(path, Object.assign({}, options || {}, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {})
  }));

  const EVENT_TYPES = [
    'llm_delta', 'agent_response', 'job_started', 'job_finished', 'run_finished', 'run_error',
    'session_finished', 'stream_timeout', 'stream_end', 'turn_started', 'model_activity',
    'tool_call', 'tool_result', 'command_execution', 'mission_runtime_started', 'mission_started',
    'mission_state', 'mission_verification', 'mission_repair', 'mission_error', 'mission_finished',
    'node_started', 'node_finished', 'verification', 'steer', 'steer_applied', 'cancel_requested',
    'skill_created', 'model_capability_warning', 'swe_started', 'swe_coder_started', 'swe_repair_started',
    'research_started', 'delegation_started', 'speech_ready', 'log'
  ];

  function openStream(runId, handlers) {
    handlers = handlers || {};
    if (!runId || typeof global.EventSource !== 'function') {
      const error = new Error(!runId ? 'run_id is required' : 'EventSource is unavailable');
      if (handlers.onError) handlers.onError(error);
      return { source: null, close() {} };
    }
    let closed = false;
    let opened = false;
    let source;
    try { source = new global.EventSource(`/stream/run/${encodeURIComponent(runId)}`); }
    catch (error) { if (handlers.onError) handlers.onError(error); return { source: null, close() {} }; }
    source.onopen = () => {
      const reconnected = opened; opened = true;
      if (reconnected && handlers.onReconnect) handlers.onReconnect();
      if (handlers.onOpen) handlers.onOpen(source);
    };
    const parse = (event, type) => {
      try { return event.data ? JSON.parse(event.data) : {}; }
      catch (error) {
        if (handlers.onMalformed) handlers.onMalformed({ type, raw: event.data, error });
        return null;
      }
    };
    EVENT_TYPES.forEach((type) => source.addEventListener(type, (event) => {
      const data = parse(event, type);
      if (data === null) return;
      try {
        if (handlers.onEvent) handlers.onEvent(type, data);
        if (type === 'llm_delta' && data.text && handlers.onDelta) handlers.onDelta(String(data.text));
        if (type === 'agent_response' && data.text && handlers.onFinal) handlers.onFinal(String(data.text));
        if (TERMINAL.has(String(data.status)) || ['job_finished', 'run_finished', 'run_error', 'session_finished', 'stream_timeout'].includes(type)) {
          if (handlers.onJobFinished) handlers.onJobFinished(data, type);
        }
      } catch (error) { if (handlers.onHandlerError) handlers.onHandlerError(error, type, data); }
    }));
    source.onerror = (event) => {
      if (closed) return;
      if (handlers.onDisconnect) handlers.onDisconnect(event);
      if (handlers.onError) handlers.onError(new Error('Stream disconnected — waiting for job result'));
      // Native EventSource reconnects using the server retry directive.
    };
    return { source, close() { closed = true; try { source.close(); } catch (_) {} } };
  }

  function normalise(result, meta) {
    result = result || {};
    const response = result.response || result.final_answer || result.final_proof || meta.streamedText || '';
    const unavailable = /Fallback mock for:|Ollama not running and fallback key failed:|Ollama error:/i.test(response);
    const failure = result.failure || (result.run_kind === 'mission_failed' ? {
      stage: 'mission', reason: result.mission_error || 'mission failed'
    } : null);
    return Object.assign({}, meta, {
      ok: !failure && !result.error && !unavailable, response: unavailable ? '' : response, run_kind: result.run_kind,
      mission_id: result.mission_id, state: result.state, failure,
      error: result.error || (unavailable ? 'Model not configured or provider offline. The backend returned its non-executing fallback; no result was produced.' : null), attachments: result.attachments || [], raw: result
    });
  }

  async function sendCommand(opts) {
    opts = opts || {};
    const files = Array.from(opts.files || []);
    const runId = opts.runId || randomId();
    const timeout = Number(opts.timeout || DEFAULT_TIMEOUT);
    const fields = {
      platform: opts.platform || 'dashboard', user_id: opts.userId || 'user-01', text: opts.text,
      mode: opts.mode || 'agent', run_id: runId, stream: opts.stream === false ? 'false' : 'true',
      async: 'true', timeout: String(timeout)
    };
    ['model', 'provider', 'keyName', 'prefer'].forEach((key) => {
      const wire = key === 'keyName' ? 'key_name' : key;
      if (opts[key]) fields[wire] = opts[key];
    });
    if (opts.autonomous) fields.autonomous = 'true';
    let streamedText = '';
    const stream = openStream(runId, {
      onOpen: opts.onStreamOpen,
      onReconnect: opts.onStreamReconnect,
      onDisconnect: opts.onStreamDisconnect,
      onMalformed: opts.onMalformedEvent,
      onError: opts.onStreamError,
      onDelta(piece) { streamedText += piece; if (opts.onDelta) opts.onDelta(piece, streamedText); },
      onFinal(text) { if (text) streamedText = text; if (opts.onFinal) opts.onFinal(text); },
      onEvent: opts.onEvent, onJobFinished: opts.onJobFinished
    });
    let payload;
    try {
      let requestOptions;
      if (files.length) {
        const form = new FormData();
        Object.keys(fields).forEach((key) => form.append(key, fields[key]));
        files.forEach((entry) => {
          const file = entry && entry.file ? entry.file : entry;
          if (file) form.append('files', file, entry.name || file.name || 'attachment');
        });
        requestOptions = { method: 'POST', body: form, timeoutMs: 30000, signal: opts.signal };
      } else {
        requestOptions = { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(Object.assign({}, fields, { async: true, stream: fields.stream !== 'false' })),
          timeoutMs: 30000, signal: opts.signal };
      }
      payload = await request('/command', requestOptions);
    } catch (error) {
      stream.close();
      return { ok: false, error: error.message, status: error.status || 0, runId, transport: 'none' };
    }
    if (payload.async && payload.job_id) {
      const jobId = payload.job_id;
      if (opts.onJobId) opts.onJobId(jobId, runId);
      const deadline = Date.now() + timeout * 1000;
      let interval = 1000;
      let missing = 0;
      while (Date.now() < deadline) {
        if (opts.signal && opts.signal.aborted) { stream.close(); return { ok: false, error: 'Observation cancelled', jobId, runId, transport: 'queue' }; }
        await sleep(interval);
        interval = Math.min(5000, Math.round(interval * 1.25));
        let status;
        try { status = await jget(`/jobs/${encodeURIComponent(jobId)}`, { timeoutMs: 15000, signal: opts.signal }); missing = 0; }
        catch (error) {
          if (error.status === 404) missing += 1;
          if (missing >= 3) { stream.close(); return { ok: false, error: `Job ${jobId} is no longer known (gateway may have restarted)`, jobId, runId, transport: 'queue' }; }
          if (opts.onPollError) opts.onPollError(error);
          continue;
        }
        if (opts.onJobStatus) opts.onJobStatus(status);
        if (status.status === 'succeeded') {
          try {
            const wrapped = await jget(`/jobs/${encodeURIComponent(jobId)}/result`, { timeoutMs: 15000 });
            setTimeout(() => stream.close(), 750);
            return normalise(wrapped.result || {}, { jobId, runId, transport: 'queue', streamedText });
          } catch (error) { stream.close(); return { ok: false, error: error.message, jobId, runId, transport: 'queue' }; }
        }
        if (['failed', 'cancelled', 'interrupted'].includes(status.status)) {
          stream.close(); return { ok: false, error: status.error || `Job ${status.status}`, state: status.status, jobId, runId, transport: 'queue' };
        }
      }
      stream.close();
      return { ok: false, error: `Timed out after ${timeout}s waiting for job`, jobId, runId, transport: 'queue' };
    }
    setTimeout(() => stream.close(), 750);
    return normalise(payload, { runId, transport: 'inline', streamedText });
  }

  function formatFailure(result) {
    const failure = (result && result.failure) || {};
    return ['MISSION FAILED — the runtime did not complete the goal.',
      failure.stage && `Stage: ${failure.stage}`,
      failure.reason && `Reason: ${failure.reason}`,
      failure.recoverable === false && 'Recoverable: no',
      failure.resume_command && `Resume: ${failure.resume_command}`,
      !failure.reason && result && result.error].filter(Boolean).join('\n');
  }

  global.HermusClient = {
    version: '3.0-control-plane', DEFAULT_TIMEOUT, HermusHttpError, request, jget, jpost,
    openStream, sendCommand, formatFailure,
    steer: (runId, text) => jpost('/run/steer', { run_id: runId, text }),
    cancelRun: (runId) => jpost(`/run/cancel/${encodeURIComponent(runId)}`, {}),
    cancelJob: (jobId) => jpost(`/jobs/${encodeURIComponent(jobId)}/cancel`, {}),
    cancel: (runId) => jpost(`/run/cancel/${encodeURIComponent(runId)}`, {}),
    capabilities: (model) => jget('/models/capabilities' + (model ? `?model=${encodeURIComponent(model)}` : '')),
    status: () => jget('/api/jarvis/status'),
    navigate: (url) => jpost('/navigator/fetch', { url }, { timeoutMs: 45000 }),
    missions: {
      list: () => jget('/missions'), get: (id) => jget(`/missions/${encodeURIComponent(id)}`),
      resume: (id, restart) => jpost(`/missions/${encodeURIComponent(id)}/resume?restart_failed=${restart ? 'true' : 'false'}`, {}),
      extend: (id, steps, emergency) => jpost(`/missions/${encodeURIComponent(id)}/extend?steps=${Number(steps) || 10}&emergency=${emergency ? 'true' : 'false'}`, {})
    },
    jobs: { list: () => jget('/jobs'), status: (id) => jget(`/jobs/${encodeURIComponent(id)}`), result: (id) => jget(`/jobs/${encodeURIComponent(id)}/result`) },
    runs: { list: () => jget('/runs'), get: (id) => jget(`/runs/${encodeURIComponent(id)}`) }
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = global.HermusClient;
})(typeof window !== 'undefined' ? window : globalThis);
