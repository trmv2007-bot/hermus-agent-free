import { describe, expect, it } from "vitest";
import {
  IDLE_POOL,
  advance,
  breath,
  initialPresence,
  makeRandom,
  moveDuration,
  nextBlink,
  pickIdleMove,
  type PresenceState,
} from "./presence";

// What makes a presence read as alive is statistical, not visual: a pure
// spinner is one loop with a period you can count, and the eye finds it in a
// second. These tests assert the statistics. A test that asserted "the canvas
// called fillRect" would pass on a loading spinner.

/** Run the presence forward from a seed, sampling every step. */
function run(seed: number, states: PresenceState[], steps = 4000, dt = 16.667) {
  const rand = makeRandom(seed);
  let p = initialPresence(seed);
  const kinds: string[] = [];
  const activations: number[] = [];
  for (let i = 0; i < steps; i += 1) {
    const target = states[i % states.length];
    p = advance(p, dt, target, rand);
    if (p.move) kinds.push(p.move.kind);
    activations.push(p.activation);
  }
  return { kinds, activations, final: p };
}

describe("idle behaviour", () => {
  it("uses more than one move, so the eye cannot count a loop", () => {
    const { kinds } = run(7, ["idle"]);
    const distinct = new Set(kinds);
    expect(distinct.size).toBeGreaterThanOrEqual(4);
  });

  it("honours the weights — glancing dominates, yawning is rare", () => {
    const rand = makeRandom(11);
    const counts = new Map<string, number>();
    for (let i = 0; i < 20000; i += 1) {
      const move = pickIdleMove(rand);
      counts.set(move.kind, (counts.get(move.kind) ?? 0) + 1);
    }
    const share = (kind: string) => (counts.get(kind) ?? 0) / 20000;
    // The pool weights glance 33 and yawn 3, an 11x spread. Sampling noise is
    // far tighter than that, so these bounds are loose on purpose but still
    // fail if the weighting is dropped entirely.
    expect(share("glance")).toBeGreaterThan(0.25);
    expect(share("glance")).toBeLessThan(0.42);
    expect(share("yawn")).toBeLessThan(0.08);
  });

  it("keeps every move inside its declared duration", () => {
    const rand = makeRandom(3);
    for (const move of IDLE_POOL) {
      for (let i = 0; i < 200; i += 1) {
        const d = moveDuration(move, rand);
        expect(d).toBeGreaterThanOrEqual(move.minMs);
        expect(d).toBeLessThanOrEqual(move.maxMs);
      }
    }
  });

  it("blinks irregularly, never on a metronome", () => {
    const rand = makeRandom(5);
    const gaps = Array.from({ length: 200 }, () => nextBlink(rand));
    // If blinks were on a timer these would all be equal. They are not.
    expect(new Set(gaps.map((g) => Math.round(g))).size).toBeGreaterThan(100);
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(2000);
    expect(Math.max(...gaps)).toBeLessThanOrEqual(5000);
  });
});

describe("the breath", () => {
  it("is continuous, bounded, and scales with activation", () => {
    for (let t = 0; t < 20000; t += 137) {
      const awake = breath(t, 1);
      const asleep = breath(t, 0);
      expect(Math.abs(awake)).toBeLessThanOrEqual(0.016);
      // Asleep is not just "shallower", it is perfectly still.
      expect(asleep).toBe(0);
    }
  });

  it("returns to where it started, so it never drifts", () => {
    // An integral that does not close is a slow drift the eye catches.
    expect(breath(4200, 1)).toBeCloseTo(breath(0, 1), 6);
  });
});

describe("activation", () => {
  it("eases up when busy and down when idle, instead of flipping", () => {
    const rand = makeRandom(13);
    let p = initialPresence(13);
    const seen: number[] = [];
    for (let i = 0; i < 200; i += 1) {
      p = advance(p, 16.667, "thinking", rand);
      seen.push(p.activation);
    }
    expect(p.activation).toBeGreaterThan(0.9);
    // Monotonic while working — no flicker.
    for (let i = 1; i < seen.length; i += 1) expect(seen[i]).toBeGreaterThanOrEqual(seen[i - 1] - 1e-9);

    for (let i = 0; i < 400; i += 1) p = advance(p, 16.667, "idle", rand);
    expect(p.activation).toBeLessThan(0.1);
  });

  it("stays inside 0..1 under any state churn", () => {
    const states: PresenceState[] = ["idle", "thinking", "speaking", "compacting", "asleep", "listening"];
    const { activations } = run(21, states, 8000);
    for (const a of activations) {
      expect(a).toBeGreaterThanOrEqual(0);
      expect(a).toBeLessThanOrEqual(1);
    }
  });
});

describe("state changes", () => {
  it("injects energy so a change is never ambiguous", () => {
    const rand = makeRandom(17);
    let p = initialPresence(17);
    for (let i = 0; i < 50; i += 1) p = advance(p, 16.667, "thinking", rand);
    const before = p.transitionEnergy;
    p = advance(p, 16.667, "blocked", rand);
    expect(p.state).toBe("blocked");
    expect(p.transitionEnergy).toBeGreaterThan(before);
    // And it decays back, or the orb would sit permanently hot.
    for (let i = 0; i < 200; i += 1) p = advance(p, 16.667, "blocked", rand);
    expect(p.transitionEnergy).toBeLessThan(0.1);
  });

  it("does not idle while it is working", () => {
    const rand = makeRandom(23);
    let p = initialPresence(23);
    for (let i = 0; i < 300; i += 1) {
      p = advance(p, 16.667, "thinking", rand);
      expect(p.move).toBeNull();
    }
  });
});

describe("determinism", () => {
  it("replays exactly from a seed", () => {
    const a = run(99, ["idle", "thinking", "speaking"], 500);
    const b = run(99, ["idle", "thinking", "speaking"], 500);
    expect(a.kinds).toEqual(b.kinds);
    expect(a.activations).toEqual(b.activations);
  });

  it("produces a different timeline for a different seed", () => {
    // If every seed produced the same motion, presence would be a canned
    // animation with extra steps.
    const a = run(1, ["idle"], 600);
    const b = run(2, ["idle"], 600);
    expect(a.kinds).not.toEqual(b.kinds);
  });
});
