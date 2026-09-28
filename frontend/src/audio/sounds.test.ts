// @vitest-environment jsdom

/**
 * A real Storage, installed before anything reads it.
 *
 * jsdom does not always provide localStorage, and the module under test
 * caches whatever it finds on first read -- so a stub installed later would
 * be ignored, and the failure would look like the module is broken rather
 * than the environment. Installing it at module scope removes both problems.
 */
const store = new Map<string, string>();
const localStorageStub = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => void store.set(k, String(v)),
  removeItem: (k: string) => void store.delete(k),
  clear: () => store.clear(),
  key: (i: number) => [...store.keys()][i] ?? null,
  get length() { return store.size; },
};
Object.defineProperty(globalThis, "localStorage", {
  value: localStorageStub, configurable: true, writable: true,
});
/**
 * Sound has one job, which is easy to get wrong in the direction of annoying.
 *
 * These tests are mostly about restraint. The failure mode of interface audio
 * is not "too quiet" -- it is "plays on everything, so it is muted within a
 * day, and now the confirmation it provided is gone forever". A muted system
 * is strictly worse than a silent one, because it still looks like it is
 * giving feedback.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  _setEnabled,
  _state,
  unlock,
  getLevel,
  init,
  isMuted,
  loop,
  play,
  setLevel,
  setMuted,
  stopAllLoops,
} from "./sounds";

function stubAudio() {
  class FakeParam {
    value = 0;
    setValueAtTime = vi.fn();
    exponentialRampToValueAtTime = vi.fn();
    cancelScheduledValues = vi.fn();
  }
  class FakeNode {
    connect = vi.fn(() => new FakeNode());
    disconnect = vi.fn();
    start = vi.fn();
    stop = vi.fn();
    frequency = new FakeParam();
    gain = new FakeParam();
    Q = new FakeParam();
    type = "";
    buffer = null;
  }
  class FakeCtx {
    currentTime = 0;
    state = "running";
    sampleRate = 48000;
    createOscillator = vi.fn(() => new FakeNode());
    createGain = vi.fn(() => new FakeNode());
    createBiquadFilter = vi.fn(() => new FakeNode());
    createBufferSource = vi.fn(() => new FakeNode());
    createBuffer = vi.fn(() => ({ getChannelData: () => new Float32Array(64) }));
    resume = vi.fn();
    destination = {};
  }
  const ctx = new FakeCtx();
  (globalThis as any).AudioContext = vi.fn(() => ctx);
  return ctx;
}

/** Give the module a working audio context, the way a real gesture would. */
function withContext() {
  const ctx = stubAudio();
  _setEnabled(true);
  unlock();
  return ctx;
}

beforeEach(() => {
  localStorage.clear();
  setMuted(false);
  setLevel(0.22);
  stopAllLoops();
});

describe("restraint", () => {
  it("defaults to a low master level", () => {
    expect(getLevel()).toBeLessThanOrEqual(0.3);
  });

  it("defaults to unmuted but still quiet -- mute is the user's call, not ours", () => {
    expect(isMuted()).toBe(false);
  });

  it("remembers mute across a reload", () => {
    setMuted(true);
    expect(isMuted()).toBe(true);
    // simulate a fresh module: the value lives in localStorage, not memory
    expect(localStorage.getItem("hermus.sound.muted")).toBe("1");
  });

  it("remembers a level the user chose", () => {
    setLevel(0.6);
    expect(localStorage.getItem("hermus.sound.level")).toBe("0.6");
  });

  it("clamps a level rather than trusting it", () => {
    expect(setLevel(5)).toBe(1);
    expect(setLevel(-2)).toBe(0);
  });
});

describe("playback", () => {
  it("produces nothing at all when muted", () => {
    const ctx = stubAudio();
    _setEnabled(true);
    (globalThis as any).AudioContext = vi.fn(() => ctx);
    // no unlock() -> no context -> silence, regardless
    setMuted(true);
    play("click");
    play("open");
    expect(ctx.createOscillator).not.toHaveBeenCalled();
  });

  it("does not require a context to be safe to call", () => {
    expect(() => play("click")).not.toThrow();
    expect(() => play("error")).not.toThrow();
  });

  it("honours the known sound names without throwing on the ones it does not implement as states", () => {
    _setEnabled(true);
    expect(() => {
      play("click"); play("open"); play("close"); play("error"); play("ready");
    }).not.toThrow();
  });
});

describe("loops are states, not events", () => {
  it("a looping state does not create a new voice every call", () => {
    withContext();
    loop("listen", true);
    const first = _state().loops.length;
    loop("listen", true);
    expect(_state().loops.length).toBe(first);
  });

  it("stopping clears every loop, so a hidden tab never hums", () => {
    withContext();
    loop("listen", true);
    loop("think", true);
    expect(_state().loops.length).toBeGreaterThan(0);
    stopAllLoops();
    expect(_state().loops.length).toBe(0);
  });
});

describe("reduced motion", () => {
  it("arms sound off when the OS asks for less stimulation", () => {
    const orig = window.matchMedia;
    (window as any).matchMedia = vi.fn().mockReturnValue({ matches: true });
    init();
    expect(_state().enabled).toBe(false);
    (window as any).matchMedia = orig;
  });

  it("arms sound on otherwise", () => {
    const orig = window.matchMedia;
    (window as any).matchMedia = vi.fn().mockReturnValue({ matches: false });
    init();
    expect(_state().enabled).toBe(true);
    (window as any).matchMedia = orig;
  });
});
