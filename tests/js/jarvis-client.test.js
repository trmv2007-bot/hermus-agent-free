'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');

class FakeEventSource {
  constructor(url) { this.url = url; this.listeners = {}; FakeEventSource.last = this; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  emit(type, data) { for (const fn of this.listeners[type] || []) fn({ data }); }
  close() { this.closed = true; }
}
global.EventSource = FakeEventSource;
global.window = global;
const client = require('../../gateway/static/control-client.js');

test('SSE parses typed events, isolates malformed payloads and reports disconnect', () => {
  const seen = []; const malformed = []; let disconnected = false;
  const stream = client.openStream('run/a b', {
    onEvent: (type, data) => seen.push([type, data]),
    onMalformed: (info) => malformed.push(info),
    onDisconnect: () => { disconnected = true; }
  });
  assert.match(stream.source.url, /run%2Fa%20b/);
  stream.source.emit('tool_call', JSON.stringify({ name: 'shell' }));
  stream.source.emit('mission_state', '{bad json');
  stream.source.onerror({});
  assert.deepEqual(seen, [['tool_call', { name: 'shell' }]]);
  assert.equal(malformed[0].type, 'mission_state');
  assert.equal(disconnected, true);
  stream.close(); assert.equal(stream.source.closed, true);
});

test('HTTP wrapper exposes status and malformed successful JSON', async () => {
  global.fetch = async () => ({ ok: false, status: 429, statusText: 'Too Many', text: async () => '{"error":"rate limited"}' });
  await assert.rejects(client.jget('/x'), (error) => error.status === 429 && /rate limited/.test(error.message));
  global.fetch = async () => ({ ok: true, status: 200, statusText: 'OK', text: async () => '<html>' });
  await assert.rejects(client.jget('/x'), /Malformed JSON/);
});

test('inline fallback returns the authoritative real response', async () => {
  global.fetch = async (url) => {
    assert.equal(url, '/command');
    return { ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify({ response: 'backend answer', run_kind: 'chat' }) };
  };
  const result = await client.sendCommand({ text: 'hello', runId: 'inline-test' });
  assert.equal(result.ok, true); assert.equal(result.transport, 'inline'); assert.equal(result.response, 'backend answer');
});

test('offline model fallback is visibly failed, never fake success', async () => {
  global.fetch = async () => ({ ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify({ response: 'Ollama not running. Fallback mock for: hello', run_kind: 'chat' }) });
  const result = await client.sendCommand({ text: 'hello', runId: 'offline-test' });
  assert.equal(result.ok, false); assert.match(result.error, /Model not configured/); assert.equal(result.response, '');
});

test('mission failure never normalises to success', async () => {
  global.fetch = async () => ({ ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify({ run_kind: 'mission_failed', mission_error: 'verification failed' }) });
  const result = await client.sendCommand({ text: 'mission', runId: 'failure-test' });
  assert.equal(result.ok, false); assert.match(client.formatFailure(result), /verification failed/);
});

test('postVoiceBlob speaks the ack first, then resolves on voice_answer', async () => {
  const calls = [];
  global.fetch = async (url, init) => {
    calls.push([url, (init || {}).method]);
    return {
      ok: true, status: 202, statusText: 'Accepted',
      text: async () => JSON.stringify({
        success: true, queued: true, transcript: 'what time is it',
        ack: { spoken: true, text: 'On it.', audio_url: '/speech/audio/1', mode: 'canned' },
        job_id: 'job-1', run_id: 'run-1', events_url: '/jobs/job-1/events',
      }),
    };
  };

  const seen = [];
  const blob = new Blob(['audio'], { type: 'audio/webm' });
  const pending = client.postVoiceBlob(blob, {
    onTranscript: (t) => seen.push(['transcript', t]),
    onAck: (a) => seen.push(['ack', a.text]),
    onAnswer: (d) => seen.push(['answer', d.text]),
  });

  // Let the fetch + ack playback settle before asserting on order.
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));

  // The 202 must have been issued before any streaming starts.
  assert.deepEqual(calls, [['/voice/command', 'POST']]);
  assert.equal(seen[0][0], 'transcript');
  assert.equal(seen[0][1], 'what time is it');
  assert.equal(seen[1][0], 'ack');
  assert.equal(seen[1][1], 'On it.');
  assert.equal(seen.length, 2, 'answer must not arrive before the job streams it');

  FakeEventSource.last.emit('voice_answer', JSON.stringify({ text: 'It is 18:40.', audio_url: null }));
  await pending;
  assert.deepEqual(seen[2], ['answer', 'It is 18:40.']);
  assert.equal(FakeEventSource.last.closed, true, 'stream must close once answered');
});

test('postVoiceBlob rejects a failed transcription instead of speaking nothing', async () => {
  global.fetch = async () => ({
    ok: false, status: 503, statusText: 'Service Unavailable',
    text: async () => JSON.stringify({ success: false, stage: 'transcribe', error: 'no whisper model' }),
  });
  let acked = false;
  await assert.rejects(
    client.postVoiceBlob(new Blob(['a'], { type: 'audio/webm' }), { onAck: () => { acked = true; } }),
    /no whisper model/,
  );
  assert.equal(acked, false, 'must not acknowledge a request that never got transcribed');
});

test('startRecording rejects cleanly where MediaRecorder is unavailable', async () => {
  const rec = client.startRecording();
  await assert.rejects(rec.promise, /MediaRecorder/);
  rec.stop(); // must be safe to call even when recording never began
});
