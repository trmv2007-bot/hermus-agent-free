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
const client = require('../../gateway/static/hermus-client.js');

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
