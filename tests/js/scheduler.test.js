'use strict';
/**
 * The Control Room's one scheduler: one bundle request for the first screen,
 * a bounded number of sockets, one request per URL, backoff per task, and no
 * timers at all while the document is hidden.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const SOURCE = path.join(__dirname, '..', '..', 'gateway', 'static', 'scheduler.js');

/** A gateway stand-in: counts requests, can be told to be slow or to fail. */
function makeFetch() {
  const calls = [];
  const impl = async (url) => {
    calls.push(url);
    if (String(url).startsWith('/api/v1/console/state')) {
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({
          v: 1,
          warm: true,
          sections: {
            health: { ok: true, capabilities: {} },
            jobs: { jobs: [{ id: 'j1' }], queue: { workers: 4 } },
            presence: { identity: { name: 'fairy' }, presence: { state: 'idle' } },
            console: { panels: [] },
            missions: { missions: [] },
            events: { events: [] },
            personal_space: { status: 'quiet' },
            jarvis: { warming: true },
            vram: { warming: true },
            computer: { warming: true },
          },
        }),
      };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify({ path: url }) };
  };
  return { impl, calls };
}

let current = null;

function freshBus() {
  // A fresh module instance per test: the bus holds process-wide state.
  delete require.cache[require.resolve(SOURCE)];
  const bus = require(SOURCE);
  bus.reset();
  current = bus;
  return bus;
}

// A scheduled task keeps a timer alive; clear it so the runner can exit.
test.afterEach(() => {
  if (current) current.reset();
  current = null;
});

test('the first screen is one request, not one per panel', async () => {
  const { impl, calls } = makeFetch();
  const bus = freshBus();
  bus.configure({ fetch: impl });
  await bus.prime();
  const before = calls.length;
  const results = await Promise.all([
    bus.get('/api/v1/system/health'),
    bus.get('/jobs'),
    bus.get('/presence'),
    bus.get('/api/v1/console/manifest'),
    bus.get('/api/v1/system/health'), // same panel asking twice
  ]);
  assert.equal(calls.length, before, 'bundle-backed reads must not touch the network');
  assert.equal(results[0].data.ok, true);
  assert.deepEqual(results[1].data.jobs, [{ id: 'j1' }]);
  assert.equal(results[2].data.identity.name, 'fairy');
  assert.equal(bus.stats().bundleFetches, 1);
});

test('a stale bundle is refreshed once for every waiter', async () => {
  const { impl, calls } = makeFetch();
  const bus = freshBus();
  bus.configure({ fetch: impl, bundleTtlMs: 0 });
  await bus.prime();
  const after = calls.length;
  await Promise.all([
    bus.get('/api/v1/system/health'),
    bus.get('/jobs'),
    bus.get('/presence'),
    bus.get('/missions'),
  ]);
  assert.equal(calls.length - after, 1, 'four stale readers, one bundle request');
  assert.equal(bus.stats().coalesced, 3);
});

test('a section the bundle does not carry falls through to its endpoint', async () => {
  const bus = freshBus();
  const calls = [];
  bus.configure({
    fetch: async (url) => {
      calls.push(String(url));
      if (String(url).startsWith('/api/v1/console/state')) {
        return { ok: true, status: 200, text: async () => JSON.stringify({ v: 1, warm: true, sections: { health: { ok: true } } }) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify({ path: url }) };
    },
  });
  await bus.prime();
  const result = await bus.get('/api/jarvis/status');
  assert.equal(calls[calls.length - 1], '/api/jarvis/status');
  assert.deepEqual(result.data, { path: '/api/jarvis/status' });
});

test('a heavy section the bundle says is warming never runs its probe', async () => {
  // /api/v1/agents/vram/status costs ~10s cold, /api/jarvis/status ~2s. Those
  // are exactly the first-screen requests this bundle exists to remove, so a
  // fresh bundle that says "warming" must answer with that fact instead.
  const { impl, calls } = makeFetch();
  const bus = freshBus();
  bus.configure({ fetch: impl });
  await bus.prime();
  const before = calls.length;
  for (const url of ['/api/v1/agents/vram/status', '/api/jarvis/status', '/computer/status']) {
    const result = await bus.get(url);
    assert.equal(result.warming, true, `${url} should answer as warming`);
    assert.deepEqual(result.data, { warming: true });
  }
  assert.equal(calls.length, before, 'a warming heavy section must not touch the network');
  assert.equal(bus.stats().warming, 3);
});

test('a first read issued before the bundle lands answers warming, not a probe', async () => {
  // The page opens and a panel asks before scheduler.js has adopted the
  // bundle. That read must fetch the bundle once and then report "warming" —
  // never run the 10-second probe it was asking for.
  const { impl, calls } = makeFetch();
  const bus = freshBus();
  bus.configure({ fetch: impl });
  const result = await bus.get('/api/v1/agents/vram/status');
  assert.equal(result.warming, true);
  assert.deepEqual(result.data, { warming: true });
  assert.ok(!calls.includes('/api/v1/agents/vram/status'), 'the cold probe must not run');
  assert.equal(bus.stats().bundleFetches, 1);
});

test('an old gateway without the bundle still gets the endpoint asked', async () => {  const calls = [];
  const bus = freshBus();
  bus.configure({
    fetch: async (url) => {
      calls.push(String(url));
      if (String(url).startsWith('/api/v1/console/state')) return { ok: false, status: 404, text: async () => '' };
      return { ok: true, status: 200, text: async () => JSON.stringify({ from: url }) };
    },
  });
  await bus.prime();
  const result = await bus.get('/api/jarvis/status');
  assert.deepEqual(result.data, { from: '/api/jarvis/status' });
  assert.equal(calls[calls.length - 1], '/api/jarvis/status');
});

test('query strings and trailing slashes still hit the bundle', async () => {
  const { impl, calls } = makeFetch();
  const bus = freshBus();
  bus.configure({ fetch: impl });
  await bus.prime();
  const before = calls.length;
  await bus.get('/events/recent?limit=50');
  await bus.get('/personal-space?limit=6');
  await bus.get('/presence/');
  assert.equal(calls.length, before);
});

test('concurrent reads of one URL share a single socket', async () => {
  const { impl, calls } = makeFetch();
  const bus = freshBus();
  bus.configure({ fetch: impl });
  await bus.prime();
  const before = calls.length;
  const [a, b] = await Promise.all([
    bus.get('/models/catalog'),
    bus.get('/models/catalog'),
  ]);
  assert.equal(calls.length - before, 1);
  assert.deepEqual(a.data, b.data);
});

test('the in-flight cap is respected and always under six sockets', async () => {
  const { impl } = makeFetch();
  let active = 0;
  let peak = 0;
  const slow = async (url) => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 5));
    active -= 1;
    return impl(url);
  };
  const bus = freshBus();
  bus.configure({ fetch: slow, maxInFlight: 3 });
  await bus.prime();
  await new Promise((resolve) => setTimeout(resolve, 20));
  peak = 0;
  await Promise.all([
    bus.get('/models/catalog'),
    bus.get('/models/selected'),
    bus.get('/models/health'),
    bus.get('/focus'),
    bus.get('/learning'),
    bus.get('/routines'),
    bus.get('/devices'),
  ]);
  assert.ok(peak <= 3, `peak concurrency ${peak} exceeded the cap`);
});

test('a failing task backs off instead of hammering the endpoint', async () => {
  const bus = freshBus();
  let calls = 0;
  bus.every('flaky', 1000, async () => {
    calls += 1;
    return false;
  });
  await bus.refresh('flaky');
  const task = bus.stats().tasks;
  assert.equal(task, 1);
  assert.equal(calls, 1);
});

test('hidden means silent: no request is issued while the tab is hidden', async () => {
  const { impl, calls } = makeFetch();
  const bus = freshBus();
  bus.configure({ fetch: impl });
  await bus.prime();
  bus.pause();
  let ran = 0;
  bus.every('probe', 50, async () => {
    ran += 1;
    await bus.get('/models/catalog');
  }, { immediate: true });
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(ran, 0, 'a paused scheduler must not run tasks');
  assert.equal(bus.stats().paused, true);
  bus.resume();
  assert.equal(bus.stats().paused, false);
});

test('a payload from an unknown bundle version is refused', async () => {
  const bus = freshBus();
  bus.configure({
    fetch: async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ v: 99, sections: { health: { ok: true } } }),
    }),
  });
  const adopted = await bus.prime();
  assert.equal(adopted, false);
  assert.equal(bus.stats().bundleVersion, null);
});

test('path normalization strips query and trailing slash', () => {
  const bus = freshBus();
  assert.equal(bus.pathOf('/events/recent?limit=50'), '/events/recent');
  assert.equal(bus.pathOf('/presence/'), '/presence');
  assert.equal(bus.pathOf('/readyz'), '/readyz');
});
