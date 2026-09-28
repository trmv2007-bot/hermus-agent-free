/**
 * Attention is the cheapest thing in the room and the easiest to get wrong by
 * overdoing. These tests are mostly about it not becoming a nuisance: a system
 * that follows you around the screen is worse than one that ignores you.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Attention } from "./attention";
import {
  advanceAttention,
  attention,
  clearAttention,
  noteInput,
  releaseAttention,
  resetAttentionForTest,
  subscribeAttention,
  TIMING,
} from "./attention";

beforeEach(() => resetAttentionForTest());
afterEach(() => vi.restoreAllMocks());

describe("noting that someone is typing", () => {
  it("records where they are and holds the look", () => {
    noteInput(0.25, 0.8, 1000);
    const a = attention();
    expect(a.target).toEqual({ x: 0.25, y: 0.8 });
    expect(a.hold).toBe(1);
  });

  it("clamps nonsense rather than looking off into the void", () => {
    noteInput(9, -4, 1000);
    expect(attention().target).toEqual({ x: 1, y: 0 });
    noteInput(NaN, 0.5, 1000);
    expect(attention().target).toEqual({ x: 0, y: 0.5 });
  });

  it("notifies subscribers so a renderer can follow without polling", () => {
    const fn = vi.fn();
    const off = subscribeAttention(fn);
    noteInput(0.1, 0.1, 1);
    expect(fn).toHaveBeenCalled();
    off();
    fn.mockClear();
    noteInput(0.2, 0.2, 2);
    expect(fn).not.toHaveBeenCalled();
  });
});

describe("letting go", () => {
  it("releases the hold but keeps the target for the ease-out", () => {
    noteInput(0.5, 0.5, 1000);
    releaseAttention();
    expect(attention().hold).toBe(0);
    // The point is kept so the gaze can travel home instead of snapping.
    expect(attention().target).toEqual({ x: 0.5, y: 0.5 });
  });

  it("is a no-op when nothing was held, so it emits nothing", () => {
    const fn = vi.fn();
    subscribeAttention(fn);
    releaseAttention();
    expect(fn).not.toHaveBeenCalled();
  });
});

describe("decay", () => {
  const held: Attention = { target: { x: 0.5, y: 0.5 }, hold: 1, lastAt: 0 };

  it("fades rather than cutting", () => {
    const a = advanceAttention(held, 16, 16);
    expect(a.hold).toBeLessThan(1);
    expect(a.hold).toBeGreaterThan(0.9);
  });

  it("reaches fully released within a few multiples of the release time", () => {
    let a = held;
    for (let t = 0; t < TIMING.RELEASE_MS * 8; t += 50) {
      a = advanceAttention(a, 50, t);
    }
    expect(a.hold).toBe(0);
  });

  it("is monotonic -- it never comes back stronger than it was", () => {
    let a = held;
    let last = a.hold;
    for (let t = 0; t < 5000; t += 50) {
      a = advanceAttention(a, 50, t);
      expect(a.hold).toBeLessThanOrEqual(last + 1e-12);
      last = a.hold;
    }
  });

  it("eventually forgets the target too, so an abandoned field is not watched forever", () => {
    let a = { ...held, hold: 0 };
    a = advanceAttention(a, 16, TIMING.RELEASE_MS + 1000);
    expect(a.target).toBeNull();
  });

  it("is a no-op for a zero delta, so a paused frame does not decay", () => {
    expect(advanceAttention(held, 0, 0).hold).toBe(1);
  });
});

describe("frame-rate independence", () => {
  it("one big step and many small ones land in the same place", () => {
    // The same class of bug as the existing approach() easing: a per-frame
    // constant would decay twice as fast at 120 Hz as at 60.
    const oneBig = advanceAttention({ target: { x: 0.5, y: 0.5 }, hold: 1, lastAt: 0 }, 500, 500);
    let many: Attention = { target: { x: 0.5, y: 0.5 }, hold: 1, lastAt: 0 };
    for (let t = 0; t < 500; t += 10) many = advanceAttention(many, 10, t);
    expect(oneBig.hold).toBeCloseTo(many.hold, 3);
  });
});

describe("clearing", () => {
  it("forgets everything", () => {
    noteInput(0.9, 0.9, 1);
    clearAttention();
    expect(attention()).toEqual({ target: null, hold: 0, lastAt: 0 });
  });
});
