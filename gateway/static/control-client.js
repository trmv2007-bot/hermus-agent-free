'use strict';
/**
 * Hermus browser client: typed SSE stream handling + voice-first (Jarvis) flow.
 *
 * Works in the browser (attaches to `window.HermusClient`) and under Node
 * (`module.exports`) so tests/js/jarvis-client.test.js can exercise the stream
 * logic without a DOM.
 *
 * Voice-first flow (see gateway/routes_voice.py):
 *   record -> POST /voice/command -> server transcribes, speaks a short ack,
 *   queues the real work and returns 202 -> we play the ack immediately and
 *   stream the job until `voice_answer` arrives, then speak that.
 *
 * The point is perceived latency: the user hears a reply in well under a second
 * instead of waiting out a full ~20K-token agent turn in silence.
 */

/** SSE event types the gateway publishes on a run stream. */
const STREAM_EVENT_TYPES = [
  'job_queued', 'job_started', 'job_retry',
  'run_started', 'run_finished', 'run_error', 'run_cancelled',
  'turn_started', 'step_started', 'tool_call', 'tool_result', 'llm_delta',
  'agent_started', 'agent_response', 'agent_finished', 'agent_failed',
  'mission_started', 'mission_state', 'mission_finished', 'mission_error',
  'mission_repair', 'mission_verification', 'mission_runtime_started',
  'node_started', 'node_finished', 'verification', 'preflight_state',
  'approval_required', 'cancel_requested', 'steer', 'steer_applied',
  'steer_consumed', 'log', 'runtime_issue', 'runtime_refreshed',
  'model_capability_warning', 'tools_disabled', 'stream_timeout',
  'delegation_started', 'delegation_planned', 'delegation_fanout',
  'delegation_child_done', 'delegation_finished', 'delegation_failed',
  'delegation_fallback', 'research_started', 'swe_started',
  'swe_coder_started', 'swe_repair_started', 'skill_harvest_started',
  'skill_created', 'skill_skipped', 'memory_swept', 'connector_refreshed',
  'channel_delivery', 'speech_ready', 'voice_answer',
];

function streamUrl(path) {
  return '/stream/' + encodeURIComponent(String(path == null ? '' : path));
}

/**
 * Open an SSE stream for a run/job.
 * @returns {{source: EventSource, close: () => void}}
 */
function openStream(path, handlers) {
  const h = handlers || {};
  const source = new EventSource(streamUrl(path));

  const deliver = (type) => (ev) => {
    const raw = ev && ev.data;
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (err) {
      // One malformed frame must not kill the stream — report and carry on.
      if (h.onMalformed) {
        h.onMalformed({
          type,
          raw: String(raw == null ? '' : raw).slice(0, 500),
          error: String((err && err.message) || err),
        });
      }
      return;
    }
    if (h.onEvent) h.onEvent(type, parsed);
  };

  STREAM_EVENT_TYPES.forEach((type) => source.addEventListener(type, deliver(type)));
  source.onerror = (e) => { if (h.onDisconnect) h.onDisconnect(e); };

  return {
    source,
    close() { try { source.close(); } catch (err) { /* already closed */ } },
  };
}

// ---------------------------------------------------------------------------
// Voice helpers (browser only; no-ops under Node)
// ---------------------------------------------------------------------------
const isBrowser = typeof window !== 'undefined' && typeof document !== 'undefined';

/** Speak text with the browser's own synthesizer. Fallback when no local WAV. */
function speakText(text, opts) {
  if (!isBrowser || !window.speechSynthesis || !text) return false;
  try {
    const utterance = new SpeechSynthesisUtterance(String(text));
    const o = opts || {};
    if (o.rate) utterance.rate = o.rate;
    if (o.voiceURI) {
      const voice = window.speechSynthesis.getVoices()
        .find((v) => v.voiceURI === o.voiceURI);
      if (voice) utterance.voice = voice;
    }
    window.speechSynthesis.speak(utterance);
    return true;
  } catch (err) {
    return false;
  }
}

function stopSpeaking() {
  if (isBrowser && window.speechSynthesis) {
    try { window.speechSynthesis.cancel(); } catch (err) { /* ignore */ }
  }
}

/** Play a gateway WAV clip; falls back to browser TTS when the clip is absent. */
function playClip(clip, fallbackText) {
  if (!clip) return Promise.resolve(false);
  const url = clip.audio_url;
  const text = clip.text || fallbackText || '';
  if (!isBrowser || !url) {
    return Promise.resolve(speakText(text));
  }
  return new Promise((resolve) => {
    const audio = new Audio(url);
    const done = (ok) => { audio.onended = null; audio.onerror = null; resolve(ok); };
    audio.onended = () => done(true);
    // A failed clip (expired, backend gone) still leaves the user with words.
    audio.onerror = () => done(speakText(text));
    audio.play().catch(() => done(speakText(text)));
  });
}

/**
 * Start recording and hand back a stop handle (for hold-to-talk).
 *
 * Pass `opts.stream` to record from an already-open microphone (the hands-free
 * loop keeps one stream open for VAD and records segments from it). A borrowed
 * stream is never closed here — the owner stops its own tracks.
 * @returns {{stop: () => void, promise: Promise<Blob>}}
 */
function startRecording(opts) {
  const o = opts || {};
  const borrowed = Boolean(o.stream);
  if (!isBrowser || !window.MediaRecorder || (!borrowed && !navigator.mediaDevices)) {
    return {
      stop: () => {},
      promise: Promise.reject(new Error('MediaRecorder is not available in this browser')),
    };
  }
  let stopFn = () => {};
  const got = (stream) => new Promise((resolve, reject) => {
    let recorder;
    try {
      recorder = new MediaRecorder(stream);
    } catch (err) {
      if (!borrowed) stream.getTracks().forEach((t) => t.stop());
      reject(err);
      return;
    }
    const chunks = [];
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.onerror = (e) => {
      if (!borrowed) stream.getTracks().forEach((t) => t.stop());
      reject(e.error || e);
    };
    recorder.onstop = () => {
      if (!borrowed) stream.getTracks().forEach((t) => t.stop());
      resolve(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }));
    };
    stopFn = () => { if (recorder.state !== 'inactive') recorder.stop(); };
    recorder.start();
  });
  const promise = borrowed ? got(o.stream)
    : navigator.mediaDevices.getUserMedia({ audio: true }).then(got);
  return { stop: () => stopFn(), promise };
}

/** Record from the microphone. Resolves with the recorded Blob. */
function recordAudio(opts) {
  const o = opts || {};
  const rec = startRecording();
  if (o.maxMs) setTimeout(rec.stop, o.maxMs);
  return rec.promise;
}

async function api(path, init) {
  const response = await fetch(path, init);
  const raw = await response.text();
  let data = null;
  try { data = JSON.parse(raw); } catch (err) { data = null; }
  return { ok: response.ok, status: response.status, data, raw };
}

class HttpError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
  }
}

/**
 * GET/POST JSON with real error propagation.
 *
 * A non-2xx response rejects carrying the server's own message and `.status`.
 * A 2xx response whose body is not JSON also rejects — silently treating an HTML
 * error page as success is how a broken gateway ends up looking healthy.
 */
async function jget(path, init) {
  const response = await fetch(path, init);
  const raw = await response.text();
  let data;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    if (!response.ok) {
      throw new HttpError(`HTTP ${response.status} ${response.statusText || ''}`.trim(), response.status);
    }
    throw new HttpError(`Malformed JSON from ${path}: ${String(raw).slice(0, 200)}`, response.status);
  }
  if (!response.ok) {
    const detail = (data && (data.error || data.detail)) || `HTTP ${response.status}`;
    throw new HttpError(String(detail), response.status);
  }
  return data;
}

/** core/llm.py emits this when no model is reachable. It is not an answer. */
const MOCK_FALLBACK_RE = /Fallback mock for:/i;

/**
 * Run one command inline and normalize the outcome.
 *
 * Two things must never be presented as success:
 *  - the local mock fallback text (no model was reachable), and
 *  - a failed mission. Both are real failures and are reported as such.
 */
async function sendCommand(opts) {
  const o = opts || {};
  const body = { text: String(o.text == null ? '' : o.text) };
  if (o.runId) body.run_id = o.runId;
  if (o.async) body.async = true;

  const data = await jget('/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }) || {};

  const response = String(data.response || '');
  const runKind = String(data.run_kind || '');

  if (MOCK_FALLBACK_RE.test(response)) {
    return {
      ok: false, transport: 'inline', run_kind: runKind, response: '',
      error: 'Model not configured or unreachable — the reply was a local mock '
        + 'fallback, not a real answer. Start Ollama or set an API key.',
    };
  }
  if (runKind === 'mission_failed' || data.mission_error) {
    return {
      ok: false, transport: 'inline', run_kind: runKind, response,
      error: String(data.mission_error || 'mission failed'),
      mission_error: String(data.mission_error || ''),
    };
  }
  return { ok: true, transport: 'inline', run_kind: runKind, response, raw: data };
}

/** Human-readable one-liner for a failed sendCommand result. */
function formatFailure(result) {
  const r = result || {};
  const parts = [];
  if (r.error) parts.push(String(r.error));
  if (r.mission_error && r.mission_error !== r.error) parts.push(String(r.mission_error));
  if (!parts.length) parts.push(String(r.run_kind || 'unknown failure'));
  return parts.join(' — ');
}

/**
 * POST an already-recorded Blob to /voice/command and drive the ack + stream.
 * Split out from voiceCommand so hold-to-talk can own the record start/stop.
 */
async function postVoiceBlob(blob, h) {
  const cb = h || {};
  if (!blob || !blob.size) throw new Error('no audio captured');
  const params = new URLSearchParams();
  if (cb.model) params.set('model', cb.model);
  if (cb.language) params.set('language', cb.language);
  if (cb.sessionId) params.set('session_id', cb.sessionId);
  const query = params.toString() ? `?${params}` : '';

  const res = await api(`/voice/command${query}`, {
    method: 'POST',
    headers: { 'Content-Type': blob.type || 'audio/webm' },
    body: blob,
  });
  if (!res.data || !res.data.success) {
    const err = new Error((res.data && res.data.error) || `voice command failed (${res.status})`);
    if (cb.onError) cb.onError(err, res.data);
    throw err;
  }
  const payload = res.data;
  if (cb.onTranscript) cb.onTranscript(payload.transcript || '');
  if (cb.onAck) cb.onAck(payload.ack || {});
  // Speak the acknowledgment right away — this is the latency the user feels.
  await playClip(payload.ack, (payload.ack || {}).text);

  if (!payload.queued) {
    if (cb.onAnswer) cb.onAnswer(payload);
    await playClip(payload.speech, payload.answer);
    return payload;
  }
  return new Promise((resolve, reject) => {
    const stream = openStream(`run/${payload.run_id}`, {
      onEvent: (type, data) => {
        if (cb.onEvent) cb.onEvent(type, data);
        if (type === 'voice_answer') {
          if (cb.onAnswer) cb.onAnswer(data);
          playClip(data, data.text).then(() => { stream.close(); resolve(payload); });
        } else if (type === 'run_error' || type === 'agent_failed') {
          stream.close();
          reject(new Error((data && (data.error || data.reason)) || type));
        }
      },
      onMalformed: (info) => { if (cb.onMalformed) cb.onMalformed(info); },
      onDisconnect: () => { if (cb.onDisconnect) cb.onDisconnect(); },
    });
  });
}

/**
 * Full voice-first turn: record -> /voice/command -> speak ack -> stream answer.
 *
 * @param {object} h  callbacks: onState, onTranscript, onAck, onEvent, onAnswer, onError
 * @returns {Promise<object>} the 202 payload
 */
/**
 * Transcribe a recorded blob *without* acting on it.
 *
 * The hands-free loop needs this split: it must see the words to decide whether
 * they were even addressed to the assistant before it spends an agent turn on
 * them. `postVoiceBlob` couples transcription to running the command, so ambient
 * speech would reach the model.
 */
async function transcribeBlob(blob, opts) {
  const o = opts || {};
  if (!blob || !blob.size) throw new Error('no audio captured');
  const params = new URLSearchParams();
  if (o.model) params.set('model', o.model);
  if (o.language) params.set('language', o.language);
  const query = params.toString() ? `?${params}` : '';

  const res = await api(`/speech/transcribe${query}`, {
    method: 'POST',
    headers: { 'Content-Type': blob.type || 'audio/webm' },
    body: blob,
  });
  if (!res.data || !res.data.success) {
    throw new Error((res.data && res.data.error) || `transcription failed (${res.status})`);
  }
  return { text: String(res.data.text || ''), backend: res.data.backend, raw: res.data };
}

async function voiceCommand(h) {
  const cb = h || {};
  const state = (s, extra) => { if (cb.onState) cb.onState(s, extra || {}); };
  if (!isBrowser) throw new Error('voiceCommand requires a browser');

  state('recording');
  const blob = await recordAudio(cb.record || {});
  state('transcribing');
  try {
    const payload = await postVoiceBlob(blob, cb);
    state('done', payload);
    return payload;
  } catch (err) {
    state('error', { error: String((err && err.message) || err) });
    throw err;
  }
}

/** Typed input, spoken response — same ack-then-queue flow without a mic. */
async function sayText(text, h) {
  const cb = h || {};
  const res = await api('/voice/say', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, sessionId: cb.sessionId || '', prefer: cb.prefer || 'auto' }),
  });
  if (!res.data || !res.data.success) {
    const err = new Error((res.data && res.data.error) || `say failed (${res.status})`);
    if (cb.onError) cb.onError(err, res.data);
    throw err;
  }
  const payload = res.data;
  if (cb.onAck) cb.onAck(payload.ack || {});
  await playClip(payload.ack, (payload.ack || {}).text);
  if (!payload.queued) {
    if (cb.onAnswer) cb.onAnswer(payload);
    await playClip(payload.speech, payload.answer);
    return payload;
  }
  return new Promise((resolve, reject) => {
    const stream = openStream(`run/${payload.run_id}`, {
      onEvent: (type, data) => {
        if (cb.onEvent) cb.onEvent(type, data);
        if (type === 'voice_answer') {
          if (cb.onAnswer) cb.onAnswer(data);
          playClip(data, data.text).then(() => { stream.close(); resolve(payload); });
        } else if (type === 'run_error' || type === 'agent_failed') {
          stream.close();
          reject(new Error((data && (data.error || data.reason)) || type));
        }
      },
      onDisconnect: () => { if (cb.onDisconnect) cb.onDisconnect(); },
    });
  });
}

/* --------------------------------------------------------------------------
 * Hands-free voice loop
 *
 * The point is that the user never touches a button again: open the Voice tab,
 * arm it, and talk. Three pieces make that work.
 *
 *   VAD       energy-based voice activity detection over an AnalyserNode, with
 *             hysteresis so a key click is not a sentence and a mid-sentence
 *             pause does not end one. No dependency, runs entirely in-browser.
 *   wake word spoken audio is transcribed but only *acted on* if it starts with
 *             the wake word, so ambient conversation never reaches the model.
 *   barge-in  talking over the assistant stops its playback and takes the floor.
 *
 * Detection is pure functions driven by injected clocks and levels so the state
 * machine is testable under Node, where there is no microphone at all.
 * ----------------------------------------------------------------------- */

const VAD_DEFAULTS = {
  threshold: 0.015,        // RMS to treat as speech when idle
  hangoverThreshold: 0.008, // lower while already speaking (trailing consonants)
  speechMs: 140,           // continuous voice before firing speech_start
  silenceMs: 900,          // trailing silence before firing speech_end
  maxUtteranceMs: 20000,
};

/** RMS energy of a time-domain frame, normalized to roughly 0..1. */
function rmsLevel(samples) {
  if (!samples || !samples.length) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const v = samples[i] / 128 - 1;
    sum += v * v;
  }
  return Math.sqrt(sum / samples.length);
}

/**
 * One step of the hysteresis state machine.
 *
 * Two thresholds matter: a higher one to *start* (so background hum does not
 * trigger) and a lower one to *continue* (so a speaker's quiet trailing sounds
 * are not read as the end of a sentence). Two timers matter: sustained voice to
 * begin, sustained silence to end.
 *
 * @returns {{state: object, event: 'speech_start'|'speech_end'|null}}
 */
function vadStep(state, level, now, opts) {
  const o = Object.assign({}, VAD_DEFAULTS, opts || {});
  const s = Object.assign({ speaking: false, voiceSince: null, silenceSince: null }, state);
  const next = { speaking: s.speaking, voiceSince: s.voiceSince, silenceSince: s.silenceSince };
  const active = level >= (s.speaking ? o.hangoverThreshold : o.threshold);

  if (active) {
    next.silenceSince = null;
    if (next.voiceSince === null) next.voiceSince = now;
  } else {
    next.voiceSince = null;
    if (next.silenceSince === null) next.silenceSince = now;
  }

  let event = null;
  if (!s.speaking && next.voiceSince !== null && now - next.voiceSince >= o.speechMs) {
    next.speaking = true;
    event = 'speech_start';
  } else if (s.speaking && next.silenceSince !== null && now - next.silenceSince >= o.silenceMs) {
    next.speaking = false;
    next.silenceSince = null;
    event = 'speech_end';
  }
  // A single frame cannot both start and end an utterance.
  return { state: next, event };
}

function _normWord(w) {
  return String(w || '').toLowerCase().replace(/[^a-z0-9']/g, '');
}

function _editDistance(a, b) {
  if (a === b) return 0;
  const prev = new Array(b.length + 1);
  for (let j = 0; j <= b.length; j += 1) prev[j] = j;
  for (let i = 1; i <= a.length; i += 1) {
    let last = prev[0];
    prev[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const tmp = prev[j];
      prev[j] = Math.min(
        prev[j] + 1,
        prev[j - 1] + 1,
        last + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
      last = tmp;
    }
  }
  return prev[b.length];
}

/**
 * Does `text` open with `phrase`, tolerating transcription errors?
 *
 * A literal startsWith would be useless in practice: speech-to-text renders the
 * same wake word as "jarvis", "jervis", "travis" and "jarvis is" depending on the
 * room. Each wake word's words are matched against the transcript's leading words
 * with an edit distance allowance that scales with word length.
 */
function matchesWakeWord(text, phrase, opts) {
  const o = opts || {};
  if (!phrase) return true; // no wake word configured -> everything counts
  // Speech-to-text renders the same wake word several ways. The edit budget below
  // is deliberately tight — loosening it enough to catch "travis" from "jarvis"
  // (3 edits in 6 letters) would also wave through ordinary words and quietly
  // un-gate the microphone. Mistranscriptions worth tolerating are declared
  // explicitly as aliases instead.
  const variants = [phrase].concat(o.aliases || []).filter(Boolean);
  return variants.some((v) => _matchesOne(text, v, o));
}

function _matchesOne(text, phrase, o) {
  const haystack = String(text || '').toLowerCase().split(/\s+/).map(_normWord).filter(Boolean);
  const needle = String(phrase).toLowerCase().split(/\s+/).map(_normWord).filter(Boolean);
  if (!needle.length) return true;
  if (haystack.length < needle.length) return false;

  // Anchored at the START only. Scanning the whole utterance would let any
  // sentence that happens to mention the name ("the jarvis report is on the
  // desk") un-gate the microphone, which defeats the point of a wake word.
  for (let i = 0; i < needle.length; i += 1) {
    const want = needle[i];
    const got = haystack[i];
    const allow = want.length <= 3 ? 0 : (o.maxEdits != null ? o.maxEdits : (want.length >= 6 ? 2 : 1));
    if (got !== want && _editDistance(got || '', want) > allow) return false;
  }
  return true;
}

/** Strip the wake word off the front so the model gets the actual instruction. */
function stripWakeWord(text, phrase, opts) {
  const o = opts || {};
  if (!phrase) return String(text || '').trim();
  const hit = [phrase].concat(o.aliases || [])
    .filter((v) => v && matchesWakeWord(text, v, { maxEdits: o.maxEdits }));
  if (hit.length) return _stripOne(text, hit[0]);
  return String(text || '').trim();
}

function _stripOne(text, phrase) {
  if (!phrase) return String(text || '').trim();
  const needle = String(phrase).toLowerCase().split(/\s+/).map(_normWord).filter(Boolean);
  const words = String(text || '').split(/\s+/).filter(Boolean);
  if (!needle.length || words.length <= needle.length) return words.join(' ').trim();
  const head = words.slice(0, needle.length).map(_normWord);
  const same = head.every((w, i) => w === needle[i]
    || _editDistance(w, needle[i]) <= (needle[i].length >= 6 ? 2 : 1));
  return (same ? words.slice(needle.length) : words).join(' ').trim();
}

/**
 * The hands-free state machine.
 *
 * Every side effect is injected, so this runs under Node in tests:
 *   capture()    -> {stop(), promise: Promise<Blob>}
 *   transcribe(blob) -> Promise<{text}>
 *   run(text)    -> Promise<{ok, response|error}>
 *   speak(text)  -> Promise
 *   stopSpeaking(), cancel(), onEvent(type, data), now()
 */
function createVoiceLoop(deps, opts) {
  const d = deps || {};
  const o = Object.assign({
    wakeWord: '', wakeAliases: [], wakeRequired: true, minUtteranceMs: 350, silenceMs: 900,
    speechMs: 140, maxUtteranceMs: 20000, bargeIn: true,
  }, opts || {});
  const wakeOpts = { aliases: o.wakeAliases };
  const now = d.now || (() => Date.now());
  const emit = (type, data) => { if (d.onEvent) d.onEvent(type, data || {}); };

  let state = 'off';
  let vad = { speaking: false, voiceSince: null, silenceSince: null };
  let rec = null;
  let startedAt = 0;
  let lastVoicedAt = 0;
  let maxTimer = null;
  let busy = false;
  // Bumped whenever a turn is superseded (barge-in) or abandoned. Every await in
  // endUtterance re-checks it, so a stale turn cannot speak over the one that
  // interrupted it — that would otherwise be the loop arguing with itself.
  let turn = 0;

  const setState = (s) => { state = s; emit('state', { state: s }); };
  const resetVad = () => { vad = { speaking: false, voiceSince: null, silenceSince: null }; };

  function beginCapture(at) {
    if (o.bargeIn && d.stopSpeaking) { try { d.stopSpeaking(); } catch (_e) { /* ignore */ } }
    if (o.bargeIn && d.cancel) { try { d.cancel(); } catch (_e) { /* best effort */ } }
    startedAt = at;
    lastVoicedAt = at;
    rec = d.capture ? d.capture() : null;
    setState('capturing');
    emit('speech_start', {});
    if (o.maxUtteranceMs > 0) {
      maxTimer = setTimeout(() => { if (state === 'capturing') endUtterance('max_length'); },
        o.maxUtteranceMs);
    }
  }

  function feed(level, at) {
    if (state === 'off') return null;
    const ts = at == null ? now() : at;
    const res = vadStep(vad, level, ts, o);
    vad = res.state;
    emit('level', { level });
    // Track the last frame that actually carried voice. Measuring the utterance
    // as `now() - startedAt` would count the 900ms of trailing silence we wait
    // for, so a 100ms cough followed by quiet would pass the min-length filter.
    if (vad.voiceSince !== null) lastVoicedAt = ts;

    if (res.event === 'speech_start') {
      if (state === 'listening') {
        beginCapture(ts);
      } else if (busy && o.bargeIn) {
        // User talked over the assistant: drop the turn in flight and take the
        // floor immediately rather than making them repeat themselves after it
        // finishes.
        turn += 1;
        busy = false;
        if (maxTimer) { clearTimeout(maxTimer); maxTimer = null; }
        emit('barge_in', { interrupted: state });
        beginCapture(ts);
      }
    } else if (res.event === 'speech_end' && state === 'capturing') {
      endUtterance('silence');
    }
    return res.event;
  }

  async function endUtterance(why) {
    if (maxTimer) { clearTimeout(maxTimer); maxTimer = null; }
    const held = rec;
    rec = null;
    resetVad();
    const dur = Math.max(0, lastVoicedAt - startedAt);
    const myTurn = turn;
    const stale = () => myTurn !== turn;
    if (held && held.stop) { try { held.stop(); } catch (_e) { /* ignore */ } }
    setState('listening');

    if (!held) return;
    if (dur < o.minUtteranceMs) { emit('discarded', { reason: 'too_short', ms: dur }); return; }

    busy = true;
    setState('thinking');
    let blob = null;
    try { blob = await held.promise; } catch (err) {
      if (stale()) return;
      busy = false; setState('listening');
      emit('error', { stage: 'capture', message: String((err && err.message) || err) });
      return;
    }
    if (stale()) return;

    let text = '';
    try {
      const t = await (d.transcribe ? d.transcribe(blob) : Promise.resolve({ text: '' }));
      text = String((t && (t.text || t.transcription)) || '').trim();
    } catch (err) {
      if (stale()) return;
      busy = false; setState('listening');
      emit('error', { stage: 'transcribe', message: String((err && err.message) || err) });
      return;
    }
    if (stale()) return;
    emit('transcript', { text, ms: dur, reason: why });

    if (!text) {
      busy = false; setState('listening');
      emit('discarded', { reason: 'empty_transcript', ms: dur });
      return;
    }
    if (o.wakeRequired && !matchesWakeWord(text, o.wakeWord, wakeOpts)) {
      busy = false; setState('listening');
      emit('discarded', { reason: 'wake_word', text, ms: dur });
      return;
    }

    const instruction = stripWakeWord(text, o.wakeRequired ? o.wakeWord : '', wakeOpts);
    if (!instruction) {
      busy = false; setState('listening');
      emit('discarded', { reason: 'no_instruction', text });
      return;
    }

    setState('speaking');
    emit('command', { text: instruction });
    let result = null;
    try { result = await (d.run ? d.run(instruction) : Promise.resolve({ ok: true })); } catch (err) {
      result = { ok: false, error: String((err && err.message) || err) };
    }
    if (stale()) { emit('superseded', { turn: myTurn }); return; }
    const answer = String((result && (result.response || result.text)) || '').trim();
    try {
      if (answer && d.speak) await d.speak(answer);
    } catch (_e) { /* playback failure must not wedge the loop */ }
    if (stale()) return;

    busy = false;
    emit('answer', { ok: Boolean(result && result.ok), answer });
    setState('listening');
  }

  return {
    feed,
    endUtterance,
    get state() { return state; },
    get busy() { return busy; },
    start() { if (state === 'off') { resetVad(); setState('listening'); } },
    stop() {
      turn += 1;
      if (maxTimer) { clearTimeout(maxTimer); maxTimer = null; }
      if (rec && rec.stop) { try { rec.stop(); } catch (_e) { /* ignore */ } }
      rec = null; busy = false;
      resetVad();
      setState('off');
    },
  };
}

/**
 * Wire a real microphone into a voice loop. Browser-only; resolves once the mic
 * is open so the caller can show an "armed" indicator.
 */
async function attachMic(loop, opts) {
  const o = opts || {};
  const ctxCtor = window.AudioContext || window.webkitAudioContext;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const ctx = new ctxCtor();
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = o.fftSize || 1024;
  source.connect(analyser);
  const buf = new Uint8Array(analyser.fftSize);
  let raf = 0;
  let stopped = false;
  // These must be invoked *through* window. Pulling the method off window and
  // calling it bare (`(window.requestAnimationFrame || setTimeout)(…)`) throws
  // "TypeError: Illegal invocation" in real browsers, which would silently stop
  // the loop from ever receiving a frame. Node has no window, hence the fallback.
  const schedule = window.requestAnimationFrame
    ? (cb) => window.requestAnimationFrame(cb)
    : (cb) => setTimeout(cb, 16);
  const unschedule = window.cancelAnimationFrame
    ? (id) => window.cancelAnimationFrame(id)
    : (id) => clearTimeout(id);

  const tick = () => {
    // Belt and braces: do not rely on cancelAnimationFrame having taken effect.
    // A tick that fires after stop() must not schedule another one.
    if (stopped) return;
    analyser.getByteTimeDomainData(buf);
    loop.feed(rmsLevel(buf));
    raf = schedule(tick);
  };
  raf = schedule(tick);

  return {
    stream,
    stop() {
      stopped = true;
      if (raf) unschedule(raf);
      raf = 0;
      try { source.disconnect(); } catch (_e) { /* ignore */ }
      stream.getTracks().forEach((t) => t.stop());
      if (ctx.close) ctx.close();
    },
  };
}

const client = {
  STREAM_EVENT_TYPES,
  HttpError,
  streamUrl,
  openStream,
  jget,
  sendCommand,
  formatFailure,
  speakText,
  stopSpeaking,
  playClip,
  startRecording,
  recordAudio,
  postVoiceBlob,
  transcribeBlob,
  voiceCommand,
  sayText,
  api,
  // hands-free voice loop
  VAD_DEFAULTS,
  rmsLevel,
  vadStep,
  matchesWakeWord,
  stripWakeWord,
  createVoiceLoop,
  attachMic,
};

if (typeof module !== 'undefined' && module.exports) module.exports = client;
if (isBrowser) window.HermusClient = client;
