'use strict';
/**
 * The Control Room's one scheduler and one state source.
 *
 * Why this file exists: the page used to run fourteen independent intervals
 * across six scripts. On a cold gateway that meant ~20 simultaneous requests on
 * load and, because Chrome keeps six sockets per host, requests queued for tens
 * of seconds — a typed question waited behind diagnostics nobody had opened.
 *
 * Two rules now hold for every script in the room:
 *
 *   1. **No script owns a timer.** Periodic work registers here with
 *      `every()`; this file owns the only timer, applies per-task backoff, and
 *      stops entirely while the document is hidden.
 *   2. **No script fetches first-screen state on its own.** `get()` serves any
 *      endpoint covered by `GET /api/v1/console/state` from that bundle, and
 *      refreshes the bundle once for every waitress that wants it. One request
 *      replaces twenty; concurrent callers share one socket.
 *
 * Everything is bounded: at most `maxInFlight` GETs at a time, one request per
 * URL at a time, and an exponential backoff per task (never per request) so a
 * broken endpoint costs one probe instead of a storm.
 *
 * Works under Node for `tests/js/scheduler.test.js`: browser globals are read
 * through `env` and injected in tests.
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.HermusBus = api;
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  /** Endpoints whose payload the console state bundle already carries. */
  const BUNDLE_SECTIONS = {
    '/readyz': 'readiness',
    '/api/v1/system/health': 'health',
    '/api/v1/system/capabilities': 'capabilities',
    '/presence': 'presence',
    '/presence/kernel': 'presence_kernel',
    '/jobs': 'jobs',
    '/queue/status': 'queue',
    '/missions': 'missions',
    '/events/recent': 'events',
    '/emergency/status': 'emergency',
    '/api/v1/agents/list': 'agents',
    '/api/v1/agents/messages': 'agent_messages',
    '/api/v1/agents/api-keys/list': 'api_keys',
    '/personal-space': 'personal_space',
    '/voice/status': 'voice',
    '/api/v1/console/manifest': 'console',
    '/api/v1/harness/overview': 'harness',
    '/computer/status': 'computer',
    '/remote/approvals': 'approvals',
    '/api/jarvis/status': 'jarvis',
    '/api/v1/agents/vram/status': 'vram',
  };

  /** Bundle sections that are gathered in the background and may still be warming. */
  const WARMING_SECTIONS = new Set(['jarvis', 'vram', 'computer']);

  /**
   * Endpoints that are deliberately not in the bundle and are expensive to ask
   * (`/doctor/status` reads every diagnostic). Reused for this long, so opening
   * the diagnostics surface twice costs one examination.
   */
  const DIRECT_TTL_MS = {
    '/doctor/status': 60000,
    '/engine/status': 30000,
    '/models/catalog': 30000,
    '/models/selected': 30000,
    '/models/health': 30000,
  };

  const config = {
    bundleUrl: '/api/v1/console/state',
    bundleTtlMs: 4000,
    maxInFlight: 3,
    minIntervalMs: 1000,
    maxBackoff: 8,
  };

  const state = {
    epoch: 0,
    bundle: null,
    bundleAt: 0,
    bundlePromise: null,
    bundleVersion: null,
    /** path -> {at, data, status} for endpoints the bundle does not carry. */
    cache: new Map(),
    /** url -> promise, so two panels asking the same question share one socket. */
    inflight: new Map(),
    /** queued network jobs, highest priority first. */
    queue: [],
    active: 0,
    tasks: new Map(),
    timer: null,
    paused: false,
    stats: { network: 0, bundleFetches: 0, bundleHits: 0, failures: 0, shared: 0, coalesced: 0, warming: 0 },
  };

  const env = {
    setTimeout: (fn, ms) => root.setTimeout(fn, ms),
    clearTimeout: (id) => root.clearTimeout(id),
    fetch: (url, opts) => root.fetch(url, opts),
    now: () => Date.now(),
    isHidden: () => Boolean(root.document && root.document.hidden),
    visibilityTarget: () => (root.document ? root.document : null),
  };

  function pathOf(url) {
    const text = String(url || '');
    const q = text.indexOf('?');
    const hash = text.indexOf('#');
    let end = text.length;
    if (q >= 0) end = Math.min(end, q);
    if (hash >= 0) end = Math.min(end, hash);
    const path = text.slice(0, end);
    return path.endsWith('/') && path.length > 1 ? path.slice(0, -1) : path;
  }

  function resetStats() {
    Object.keys(state.stats).forEach((key) => { state.stats[key] = 0; });
  }

  // ---------------------------------------------------------------- transport
  function rawFetch(url, options) {
    state.stats.network += 1;
    return env.fetch(url, options).then(
      (response) => response.text().then((text) => {
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        return { ok: Boolean(response.ok), status: response.status, data: data, text: text };
      }),
      (error) => ({
        ok: false,
        status: 0,
        data: null,
        text: '',
        error: (error && error.message) ? error.message : 'network error',
      }),
    );
  }

  function pump() {
    while (state.active < config.maxInFlight && state.queue.length) {
      state.queue.sort((a, b) => a.priority - b.priority || a.seq - b.seq);
      const job = state.queue.shift();
      state.active += 1;
      const settle = (result) => {
        if (job.epoch === state.epoch) state.active = Math.max(0, state.active - 1);
        job.resolve(result);
        pump();
      };
      rawFetch(job.url, job.options).then(settle, (error) => {
        settle({ ok: false, status: 0, data: null, text: '', error: String(error) });
      });
    }
  }

  let seq = 0;
  function schedule(url, options, priority) {
    return new Promise((resolve) => {
      // The epoch lets reset() discard a stale completion instead of letting
      // it free a slot that now belongs to a different page life (tests and a
      // hot reload both reset mid-flight).
      const epoch = state.epoch;
      state.queue.push({ url: url, options: options, priority: priority || 5, seq: seq++, epoch: epoch, resolve: resolve });
      pump();
    });
  }

  function fetchOnce(url, options, priority) {
    const key = String(url);
    const existing = state.inflight.get(key);
    if (existing) {
      state.stats.shared += 1;
      return existing;
    }
    const promise = schedule(url, options, priority).then(
      (result) => {
        state.inflight.delete(key);
        if (!result.ok) state.stats.failures += 1;
        return result;
      },
      (error) => {
        state.inflight.delete(key);
        state.stats.failures += 1;
        return { ok: false, status: 0, data: null, text: '', error: String(error) };
      },
    );
    state.inflight.set(key, promise);
    return promise;
  }

  // ------------------------------------------------------------------- bundle
  function bundleFresh() {
    if (!state.bundle) return false;
    return (env.now() - state.bundleAt) < config.bundleTtlMs;
  }

  function bundleSection(name) {
    if (!state.bundle || !state.bundle.sections) return null;
    const value = state.bundle.sections[name];
    if (value == null) return null;
    if (WARMING_SECTIONS.has(name) && value.warming === true) return null;
    return value;
  }

  function adoptBundle(payload) {
    if (!payload || typeof payload !== 'object' || !payload.sections) return false;
    // A payload this file cannot read is refused rather than half-rendered.
    if (Number(payload.v) !== 1) return false;
    state.bundle = payload;
    state.bundleAt = env.now();
    state.bundleVersion = payload.v;
    return true;
  }

  function refreshBundle() {
    if (state.bundlePromise) {
      state.stats.coalesced += 1;
      return state.bundlePromise;
    }
    state.stats.bundleFetches += 1;
    state.bundlePromise = fetchOnce(config.bundleUrl, { headers: { Accept: 'application/json' } }, 1)
      .then((result) => {
        state.bundlePromise = null;
        if (result.ok && adoptBundle(result.data)) return true;
        return false;
      }, () => {
        state.bundlePromise = null;
        return false;
      });
    return state.bundlePromise;
  }

  /**
   * Read a state endpoint.
   *
   * GETs covered by the bundle come from it (refreshing it once if stale);
   * everything else goes through the bounded queue. Returns the same shape as
   * `fetch`: `{ok, status, data, text, error}`.
   */
  /**
   * The bundle's answer for `section`, or null when it cannot give one.
   *
   * A heavy section that is still warming is an answer, not a miss: falling
   * through to its endpoint would run the very probe the bundle exists to keep
   * off the first screen (/api/v1/agents/vram/status ~10s cold, /api/jarvis/
   * status ~2s). The caller renders "warming" and the next refresh (4s) brings
   * the real thing.
   */
  function bundleAnswer(section) {
    const value = bundleSection(section);
    if (value != null) {
      state.stats.bundleHits += 1;
      return { ok: true, status: 200, data: value, text: '', fromBundle: true };
    }
    const raw = state.bundle && state.bundle.sections ? state.bundle.sections[section] : null;
    if (raw != null && WARMING_SECTIONS.has(section)) {
      state.stats.warming += 1;
      return { ok: true, status: 200, data: raw, text: '', warming: true, fromBundle: true };
    }
    return null;
  }

  function get(url, options) {
    const opts = options || {};
    const method = String(opts.method || 'GET').toUpperCase();
    if (method !== 'GET') return fetchOnce(url, opts, 0);
    const path = pathOf(url);
    const section = BUNDLE_SECTIONS[path];
    if (!section) {
      const ttl = DIRECT_TTL_MS[path];
      if (ttl) {
        const hit = state.cache.get(path);
        if (hit && (env.now() - hit.at) < ttl) {
          state.stats.bundleHits += 1;
          return Promise.resolve({ ok: true, status: 200, data: hit.data, text: '', cached: true });
        }
        return fetchOnce(url, opts, 3).then((result) => {
          if (result.ok) state.cache.set(path, { at: env.now(), data: result.data });
          return result;
        });
      }
      return fetchOnce(url, opts, 3);
    }
    const answer = bundleFresh() ? bundleAnswer(section) : null;
    if (answer) return Promise.resolve(answer);
    return refreshBundle().then((bundleOk) => {
      const fresh = bundleOk ? bundleAnswer(section) : null;
      if (fresh) return fresh;
      // The bundle could not answer (not built yet, or the route is missing):
      // fall back to the endpoint itself so a panel still renders.
      return fetchOnce(url, opts, 3);
    });
  }

  // -------------------------------------------------------------------- tasks
  function scheduleNext() {
    if (state.paused) return;
    let soonest = null;
    state.tasks.forEach((task) => {
      if (soonest === null || task.nextAt < soonest) soonest = task.nextAt;
    });
    if (soonest === null) {
      if (state.timer) { env.clearTimeout(state.timer); state.timer = null; }
      return;
    }
    if (state.timer) env.clearTimeout(state.timer);
    const delay = Math.max(0, soonest - env.now());
    state.timer = env.setTimeout(runDue, delay);
  }

  function runDue() {
    state.timer = null;
    if (state.paused) return;
    const now = env.now();
    const due = [];
    state.tasks.forEach((task) => { if (task.nextAt <= now) due.push(task); });
    due.forEach((task) => {
      // Claim the slot before the work starts so a slow task cannot double-run.
      task.nextAt = now + task.every * task.backoff;
      let result;
      try {
        result = task.fn();
      } catch (error) {
        task.backoff = Math.min(task.backoff * 2, config.maxBackoff);
        return;
      }
      if (result && typeof result.then === 'function') {
        result.then((ok) => {
          if (ok === false) task.backoff = Math.min(task.backoff * 2, config.maxBackoff);
          else { task.backoff = 1; task.nextAt = env.now() + task.every; }
          scheduleNext();
        }, () => {
          task.backoff = Math.min(task.backoff * 2, config.maxBackoff);
          task.nextAt = env.now() + task.every * task.backoff;
          scheduleNext();
        });
      } else if (result === false) {
        task.backoff = Math.min(task.backoff * 2, config.maxBackoff);
      } else {
        task.backoff = 1;
        task.nextAt = now + task.every;
      }
    });
    scheduleNext();
  }

  /**
   * Register periodic work. `fn` returns a promise (or a boolean). A rejected
   * promise or `false` doubles this task's interval, up to `maxBackoff`.
   */
  function every(name, intervalMs, fn, options) {
    const opts = options || {};
    const every = Math.max(config.minIntervalMs, Number(intervalMs) || config.minIntervalMs);
    const existing = state.tasks.get(name);
    if (existing) {
      existing.every = every;
      existing.fn = fn;
      return existing;
    }
    const task = {
      name: name,
      every: every,
      fn: fn,
      backoff: 1,
      nextAt: env.now() + (opts.immediate === false ? every : 0),
      network: opts.network !== false,
    };
    state.tasks.set(name, task);
    scheduleNext();
    return task;
  }

  function cancel(name) {
    state.tasks.delete(name);
    scheduleNext();
  }

  /** Run a registered task now (a person opened its tab, or pressed refresh). */
  function refresh(names) {
    const wanted = Array.isArray(names) ? names : [names];
    return Promise.all(wanted.map((name) => {
      const task = state.tasks.get(name);
      if (!task) return Promise.resolve(false);
      task.backoff = 1;
      task.nextAt = env.now();
      try {
        const result = task.fn();
        return Promise.resolve(result);
      } catch (error) {
        return Promise.resolve(false);
      }
    })).then(() => { scheduleNext(); });
  }

  // -------------------------------------------------------------- visibility
  function pause() {
    state.paused = true;
    if (state.timer) { env.clearTimeout(state.timer); state.timer = null; }
  }

  function resume() {
    if (!state.paused) return;
    state.paused = false;
    const now = env.now();
    state.tasks.forEach((task) => { task.backoff = 1; task.nextAt = now; });
    scheduleNext();
  }

  function bindVisibility() {
    const target = env.visibilityTarget();
    if (!target || typeof target.addEventListener !== 'function') return;
    target.addEventListener('visibilitychange', () => {
      if (env.isHidden()) pause();
      else resume();
    });
    if (env.isHidden()) pause();
  }

  function stats() {
    const out = { inFlight: state.active, queued: state.queue.length, tasks: state.tasks.size, paused: state.paused };
    Object.keys(state.stats).forEach((key) => { out[key] = state.stats[key]; });
    out.bundleAgeMs = state.bundle ? env.now() - state.bundleAt : null;
    out.bundleVersion = state.bundleVersion;
    return out;
  }

  /** Test/config hook. Only the keys given are applied. */
  function configure(overrides) {
    Object.keys(overrides || {}).forEach((key) => {
      if (key in config) config[key] = overrides[key];
      else if (key === 'fetch') env.fetch = overrides[key];
      else if (key === 'now') env.now = overrides[key];
      else if (key === 'setTimeout') env.setTimeout = overrides[key];
      else if (key === 'clearTimeout') env.clearTimeout = overrides[key];
      else if (key === 'isHidden') env.isHidden = overrides[key];
    });
    return Object.assign({}, config);
  }

  function reset() {
    state.epoch += 1;
    if (state.timer) { env.clearTimeout(state.timer); state.timer = null; }
    state.bundle = null;
    state.bundleAt = 0;
    state.bundlePromise = null;
    state.bundleVersion = null;
    state.cache.clear();
    state.inflight.clear();
    state.queue.length = 0;
    state.active = 0;
    state.tasks.clear();
    state.paused = false;
    resetStats();
  }

  bindVisibility();
  // Prime the bundle at parse time: the first screen is then one request.
  refreshBundle();

  return {
    get: get,
    every: every,
    cancel: cancel,
    refresh: refresh,
    prime: refreshBundle,
    stats: stats,
    pause: pause,
    resume: resume,
    configure: configure,
    reset: reset,
    pathOf: pathOf,
    BUNDLE_SECTIONS: BUNDLE_SECTIONS,
  };
});
