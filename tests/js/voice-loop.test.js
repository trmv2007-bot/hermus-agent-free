/* Hands-free voice loop: VAD hysteresis, wake-word gating, barge-in.
 *
 * Everything here runs without a microphone. The detector and the state machine
 * take injected levels and an injected clock, so the interesting behaviour
 * (what counts as speech, when an utterance ends, what happens when the user
 * talks over the assistant) is asserted directly rather than approximated.
 *
 * Run: node --test "tests/js/*.test.js"
 */
const test = require('node:test');
const assert = require('node:assert/strict');

const client = require('../../gateway/static/control-client.js');
const {
  rmsLevel, vadStep, matchesWakeWord, stripWakeWord, createVoiceLoop, VAD_DEFAULTS,
} = client;

const flush = async (n = 6) => {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
};

/** Push `ms` worth of frames at a fixed level through the loop. */
function speak(loop, clock, ms, level) {
  const frames = Math.max(1, Math.ceil(ms / 20));
  for (let i = 0; i < frames; i += 1) {
    clock.t += 20;
    loop.feed(level, clock.t);
  }
}

// --- level metering --------------------------------------------------------

test('rmsLevel reads silence as (near) zero and a full-scale tone as loud', () => {
  const silence = new Uint8Array(512).fill(128);
  assert.ok(rmsLevel(silence) < 0.001, `silence measured ${rmsLevel(silence)}`);

  const loud = new Uint8Array(512);
  for (let i = 0; i < loud.length; i += 1) loud[i] = i % 2 === 0 ? 255 : 0;
  assert.ok(rmsLevel(loud) > 0.5, `tone measured ${rmsLevel(loud)}`);
  assert.ok(rmsLevel([]) === 0);
});

// --- VAD hysteresis --------------------------------------------------------

test('vadStep ignores background noise below the idle threshold', () => {
  let s = { speaking: false, voiceSince: null, silenceSince: null };
  let event = null;
  for (let t = 0; t < 2000; t += 20) {
    const r = vadStep(s, 0.004, t, {});
    s = r.state;
    if (r.event) event = r.event;
  }
  assert.equal(event, null);
  assert.equal(s.speaking, false);
});

test('vadStep fires speech_start only after sustained voice', () => {
  const o = { speechMs: 140 };
  let s = { speaking: false, voiceSince: null, silenceSince: null };
  let firedAt = null;
  for (let t = 0; t <= 200; t += 20) {
    const r = vadStep(s, 0.2, t, o);
    s = r.state;
    if (r.event === 'speech_start') { firedAt = t; break; }
  }
  assert.ok(firedAt !== null, 'speech_start never fired');
  assert.ok(firedAt >= o.speechMs, `fired too early at ${firedAt}ms`);
});

test('a key click shorter than speechMs never becomes an utterance', () => {
  let s = { speaking: false, voiceSince: null, silenceSince: null };
  const events = [];
  for (let t = 0; t <= 400; t += 20) {
    const level = t >= 100 && t < 140 ? 0.5 : 0.0; // 40ms blip
    const r = vadStep(s, level, t, { speechMs: 140 });
    s = r.state;
    if (r.event) events.push(r.event);
  }
  assert.deepEqual(events, []);
});

test('trailing silence fires speech_end after silenceMs', () => {
  let s = { speaking: false, voiceSince: null, silenceSince: null };
  const events = [];
  for (let t = 0; t <= 2000; t += 20) {
    const level = t < 400 ? 0.2 : 0.0;
    const r = vadStep(s, level, t, { speechMs: 140, silenceMs: 900 });
    s = r.state;
    if (r.event) events.push([t, r.event]);
  }
  assert.equal(events[0][1], 'speech_start');
  assert.equal(events[1][1], 'speech_end');
  assert.ok(events[1][0] - 400 >= 900, `ended too soon: ${events[1][0]}`);
});

test('a mid-sentence pause shorter than silenceMs does not end the utterance', () => {
  let s = { speaking: false, voiceSince: null, silenceSince: null };
  const events = [];
  // Stop before the trailing silence after the second segment can complete, so
  // the only thing that *could* have ended the utterance early is the 300ms gap.
  for (let t = 0; t <= 1800; t += 20) {
    const level = (t < 400 || (t >= 700 && t < 1100)) ? 0.2 : 0.0;
    const r = vadStep(s, level, t, { speechMs: 140, silenceMs: 900 });
    s = r.state;
    if (r.event) events.push(r.event);
  }
  assert.deepEqual(events, ['speech_start'], `unexpected events: ${events}`);
  assert.equal(s.speaking, true, 'the utterance should still be open across the gap');
});

test('the hangover threshold keeps quiet trailing frames inside the utterance', () => {
  // A level that would NOT start an utterance but must not END one either: the
  // gap between the two thresholds is the whole point of hysteresis.
  const level = (VAD_DEFAULTS.threshold + VAD_DEFAULTS.hangoverThreshold) / 2;
  assert.ok(level < VAD_DEFAULTS.threshold);
  assert.ok(level >= VAD_DEFAULTS.hangoverThreshold);

  const speaking = vadStep({ speaking: true, voiceSince: 0, silenceSince: null }, level, 500, {});
  assert.equal(speaking.state.silenceSince, null,
    'a quiet frame wrongly started the silence timer while speaking');

  const idle = vadStep({ speaking: false, voiceSince: null, silenceSince: null }, level, 500, {});
  assert.equal(idle.event, null, 'the same level must not instantly start a new utterance');
});

// --- wake word -------------------------------------------------------------

test('matchesWakeWord accepts the wake word and near-miss transcriptions', () => {
  for (const text of ['jarvis what time is it', 'Jarvis, what time is it?',
    'jervis what time is it', 'JARVIS what time is it']) {
    assert.ok(matchesWakeWord(text, 'jarvis'), `rejected: ${text}`);
  }
});

test('a far mistranscription is rejected rather than loosening the gate', () => {
  // "travis" is 3 edits from "jarvis". Accepting that would also wave through
  // ordinary words, which silently defeats the purpose of a wake word. Such
  // cases are declared as aliases instead.
  assert.equal(matchesWakeWord('travis what time is it', 'jarvis'), false);
  assert.ok(matchesWakeWord('travis what time is it', 'jarvis', { aliases: ['travis'] }));
});

test('aliases are stripped from the instruction too', () => {
  assert.equal(stripWakeWord('travis what time is it', 'jarvis', { aliases: ['travis'] }),
    'what time is it');
});

test('matchesWakeWord rejects ordinary conversation', () => {
  assert.equal(matchesWakeWord('what time is it', 'jarvis'), false);
  assert.equal(matchesWakeWord('the garage is open', 'jarvis'), false);
  assert.equal(matchesWakeWord('', 'jarvis'), false);
});

test('no wake word configured means every utterance counts', () => {
  assert.equal(matchesWakeWord('anything at all', ''), true);
  assert.equal(matchesWakeWord('anything at all', null), true);
});

test('the wake word must open the utterance, not merely appear in it', () => {
  // Regression: the matcher used to scan every offset, so any sentence that
  // happened to mention the name un-gated the microphone.
  assert.equal(matchesWakeWord('the jarvis report is on the desk', 'jarvis'), false);
  assert.equal(matchesWakeWord('did jarvis finish the report', 'jarvis'), false);
  assert.equal(matchesWakeWord('ask jarvis', 'jarvis'), false);
  assert.ok(matchesWakeWord('jarvis is the report ready', 'jarvis'));
});

test('a multi-word wake word is matched word by word', () => {
  assert.ok(matchesWakeWord('hey hermus open the file', 'hey hermus'));
  assert.equal(matchesWakeWord('hey there open the file', 'hey hermus'), false);
});

test('stripWakeWord hands the model the instruction, not the greeting', () => {
  assert.equal(stripWakeWord('jarvis what time is it', 'jarvis'), 'what time is it');
  assert.equal(stripWakeWord('jervis what time is it', 'jarvis'), 'what time is it');
  assert.equal(stripWakeWord('what time is it', 'jarvis'), 'what time is it');
  assert.equal(stripWakeWord('jarvis', 'jarvis'), 'jarvis', 'a bare wake word keeps its text');
});

// --- the loop --------------------------------------------------------------

function makeLoop(over) {
  const events = [];
  const clock = { t: 0 };
  const calls = { run: [], speak: [], stopSpeaking: 0, cancel: 0, capture: 0 };
  const deps = {
    now: () => clock.t,
    onEvent: (type, data) => events.push({ type, ...data }),
    capture: () => {
      calls.capture += 1;
      return { stop: () => {}, promise: Promise.resolve({ size: 100, type: 'audio/webm' }) };
    },
    transcribe: async () => ({ text: 'jarvis what time is it' }),
    run: async (text) => { calls.run.push(text); return { ok: true, response: 'It is 8pm.' }; },
    speak: async (text) => { calls.speak.push(text); },
    stopSpeaking: () => { calls.stopSpeaking += 1; },
    cancel: () => { calls.cancel += 1; },
  };
  Object.assign(deps, (over && over.deps) || {});
  const loop = createVoiceLoop(deps, Object.assign({
    wakeWord: 'jarvis', wakeRequired: true, minUtteranceMs: 350,
    speechMs: 140, silenceMs: 900, maxUtteranceMs: 0,
  }, (over && over.opts) || {}));
  return { loop, events, clock, calls };
}

const types = (events) => events.map((e) => e.type);

test('a full utterance travels listening -> capturing -> thinking -> speaking', async () => {
  const { loop, events, clock, calls } = makeLoop();
  loop.start();
  assert.equal(loop.state, 'listening');

  speak(loop, clock, 1200, 0.2);
  assert.equal(loop.state, 'capturing');
  speak(loop, clock, 1000, 0.0);

  await flush();
  assert.equal(loop.state, 'listening', `stuck in ${loop.state}`);
  assert.deepEqual(calls.run, ['what time is it'], 'the wake word must not reach the model');
  assert.deepEqual(calls.speak, ['It is 8pm.']);
  assert.ok(types(events).includes('transcript'));
  assert.ok(types(events).includes('answer'));
});

test('speech that is not addressed to the assistant never reaches the model', async () => {
  const { loop, events, clock, calls } = makeLoop({
    deps: { transcribe: async () => ({ text: 'did you see the game last night' }) },
  });
  loop.start();
  speak(loop, clock, 1200, 0.2);
  speak(loop, clock, 1000, 0.0);
  await flush();

  assert.deepEqual(calls.run, [], 'ambient conversation must not trigger a run');
  assert.deepEqual(calls.speak, [], 'and must not be spoken back');
  const disc = events.filter((e) => e.type === 'discarded');
  assert.equal(disc.length, 1);
  assert.equal(disc[0].reason, 'wake_word');
  assert.equal(loop.state, 'listening', 'loop must keep listening');
});

test('a blip too short to be a command is dropped before transcription', async () => {
  const { loop, events, clock, calls } = makeLoop({ opts: { minUtteranceMs: 350 } });
  loop.start();
  // ~200ms of voice: enough to trip speech_start, far short of a real sentence.
  speak(loop, clock, 200, 0.2);
  speak(loop, clock, 1000, 0.0);
  await flush();

  const disc = events.filter((e) => e.type === 'discarded');
  assert.equal(disc.length, 1);
  assert.equal(disc[0].reason, 'too_short');
  assert.deepEqual(calls.run, []);
});

test('an empty transcript is discarded, not sent to the model', async () => {
  const { loop, events, clock, calls } = makeLoop({
    deps: { transcribe: async () => ({ text: '   ' }) },
  });
  loop.start();
  speak(loop, clock, 1200, 0.2);
  speak(loop, clock, 1000, 0.0);
  await flush();
  assert.equal(events.filter((e) => e.type === 'discarded')[0].reason, 'empty_transcript');
  assert.deepEqual(calls.run, []);
});

test('a transcription failure is reported and the loop keeps listening', async () => {
  const { loop, events, clock, calls } = makeLoop({
    deps: { transcribe: async () => { throw new Error('whisper unavailable'); } },
  });
  loop.start();
  speak(loop, clock, 1200, 0.2);
  speak(loop, clock, 1000, 0.0);
  await flush();

  const errs = events.filter((e) => e.type === 'error');
  assert.equal(errs.length, 1);
  assert.equal(errs[0].stage, 'transcribe');
  assert.match(errs[0].message, /whisper unavailable/);
  assert.equal(loop.state, 'listening');
  assert.deepEqual(calls.run, []);
});

test('barge-in stops playback and cancels the running job', async () => {
  let releaseRun;
  const runGate = new Promise((r) => { releaseRun = r; });
  const { loop, events, clock, calls } = makeLoop({
    deps: {
      run: async (text) => {
        calls.run.push(text);
        await runGate;
        return { ok: true, response: 'stale answer' };
      },
    },
  });
  loop.start();
  speak(loop, clock, 1200, 0.2);
  speak(loop, clock, 1000, 0.0);
  await flush(2); // reach the run() await

  assert.equal(loop.busy, true, 'a turn should be in flight');
  // Now talk over it.
  speak(loop, clock, 400, 0.3);

  assert.ok(calls.cancel >= 1, 'the in-flight job must be cancelled');
  assert.ok(types(events).includes('barge_in'));

  releaseRun({ ok: true });
  await flush();
  assert.deepEqual(calls.speak, [], 'a superseded turn must not speak its stale answer');
  assert.ok(types(events).includes('superseded'));
});

test('stop() disarms the loop and drops any turn in flight', async () => {
  let releaseRun;
  const runGate = new Promise((r) => { releaseRun = r; });
  const { loop, clock, calls } = makeLoop({
    deps: { run: async () => { await runGate; return { ok: true, response: 'late' }; } },
  });
  loop.start();
  speak(loop, clock, 1200, 0.2);
  speak(loop, clock, 1000, 0.0);
  await flush(2);
  loop.stop();
  assert.equal(loop.state, 'off');

  releaseRun();
  await flush();
  assert.deepEqual(calls.speak, [], 'nothing may be spoken after stop()');
});

test('maxUtteranceMs ends a capture that never goes quiet', async () => {
  const { loop, events, clock } = makeLoop({ opts: { maxUtteranceMs: 40 } });
  loop.start();
  // Continuous noise, e.g. a fan or a TV: the silence timer never fires.
  speak(loop, clock, 600, 0.3);
  await new Promise((r) => setTimeout(r, 90));
  await flush();
  assert.ok(types(events).includes('transcript'), 'a runaway capture must still be processed');
  assert.equal(loop.state, 'listening');
});

test('with barge-in disabled, playback is never interrupted', async () => {
  let releaseRun;
  const runGate = new Promise((r) => { releaseRun = r; });
  const { loop, events, clock, calls } = makeLoop({
    opts: { bargeIn: false },
    deps: { run: async () => { await runGate; return { ok: true, response: 'answer' }; } },
  });
  loop.start();
  speak(loop, clock, 1200, 0.2);
  speak(loop, clock, 1000, 0.0);
  await flush(2);
  speak(loop, clock, 400, 0.3);

  assert.equal(calls.cancel, 0, 'nothing should be cancelled with barge-in off');
  assert.ok(!types(events).includes('barge_in'));
  releaseRun();
  await flush();
  assert.deepEqual(calls.speak, ['answer'], 'the turn in flight should complete normally');
});

// --- browser integration ---------------------------------------------------

test('attachMic drives frames through window-bound animation callbacks', async () => {
  // Real browsers throw "TypeError: Illegal invocation" when a window method is
  // pulled off window and called bare, e.g. `(window.requestAnimationFrame ||
  // setTimeout)(cb)`. Node has no window, so this reproduces that rule: the mock
  // only accepts calls whose receiver is window.
  const savedWindow = global.window;
  const frames = [];
  let ids = 0;
  const fakeWindow = {
    AudioContext: class {
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createAnalyser() {
        return { fftSize: 1024, getByteTimeDomainData(buf) { buf.fill(200); } };
      }
      close() {}
    },
  };
  fakeWindow.requestAnimationFrame = function (cb) {
    if (this !== fakeWindow) throw new TypeError('Illegal invocation');
    ids += 1;
    setTimeout(cb, 1);
    return ids;
  };
  fakeWindow.cancelAnimationFrame = function (id) {
    if (this !== fakeWindow) throw new TypeError('Illegal invocation');
  };
  global.window = fakeWindow;
  // `navigator` is a non-writable built-in on Node 21+, so a plain assignment
  // silently does nothing and mediaDevices stays undefined.
  const navDesc = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', {
    value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } },
    configurable: true, writable: true,
  });

  try {
    const mic = await client.attachMic({ feed: (level) => frames.push(level) }, {});
    await new Promise((r) => setTimeout(r, 60));
    mic.stop();
    assert.ok(frames.length > 0, 'the voice loop received no audio frames');
    assert.ok(frames[0] > 0, 'a loud buffer should not meter as silence');
  } finally {
    global.window = savedWindow;
    if (navDesc) Object.defineProperty(globalThis, 'navigator', navDesc);
    else delete globalThis.navigator;
  }
});
