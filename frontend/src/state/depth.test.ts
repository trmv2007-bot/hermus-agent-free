import { describe, expect, it } from "vitest";

import { area, coverage, overlapArea, recessionFor, type Rect } from "./depth";

const core: Rect = { x: 100, y: 100, w: 200, h: 200 };

describe("overlap", () => {
  it("is zero for rects that miss", () => {
    expect(overlapArea(core, { x: 400, y: 400, w: 50, h: 50 })).toBe(0);
  });

  it("is zero when they only touch on an edge", () => {
    expect(overlapArea(core, { x: 300, y: 100, w: 50, h: 50 })).toBe(0);
  });

  it("is the smaller shape when one contains the other", () => {
    expect(overlapArea(core, { x: 0, y: 0, w: 1000, h: 1000 })).toBe(200 * 200);
  });

  it("counts a corner clip, because a corner is already in the way", () => {
    expect(overlapArea(core, { x: 250, y: 250, w: 100, h: 100 })).toBe(50 * 50);
  });
});

describe("coverage", () => {
  it("is zero when the room is clear", () => {
    expect(coverage(core, [])).toBe(0);
  });

  it("is one when fully covered", () => {
    expect(coverage(core, [{ x: 0, y: 0, w: 1000, h: 1000 }])).toBeCloseTo(1, 5);
  });

  it("does not double-count overlapping surfaces", () => {
    const a = { x: 0, y: 0, w: 1000, h: 1000 };
    expect(coverage(core, [a, a])).toBeCloseTo(1, 5);
  });

  it("rises monotonically, so more covering always means more recession", () => {
    // The property that matters, rather than a particular number for a
    // particular rectangle. An earlier version of this test asserted a
    // threshold I had picked by eye, which tested the guess rather than the
    // behaviour.
    const steps = [0, 50, 100, 150, 200].map(
      (w) => coverage(core, [{ x: 100, y: 100, w, h: 200 }]),
    );
    for (let i = 1; i < steps.length; i++) {
      expect(steps[i]).toBeGreaterThanOrEqual(steps[i - 1]);
    }
    expect(steps[0]).toBe(0);
    expect(steps[steps.length - 1]).toBeCloseTo(1, 5);
  });

  it("reads a partial cover as partial, never as all-or-nothing", () => {
    const quarter = coverage(core, [{ x: 100, y: 100, w: 100, h: 100 }]);
    expect(quarter).toBeGreaterThan(0.1);
    expect(quarter).toBeLessThan(0.9);
  });

  it("survives a zero-sized rect without dividing by zero", () => {
    expect(() => coverage({ x: 0, y: 0, w: 0, h: 0 }, [])).not.toThrow();
    expect(area({ x: 0, y: 0, w: 0, h: 0 })).toBe(1);
  });
});

describe("recession", () => {
  it("is a no-op when nothing is covered", () => {
    const r = recessionFor(0);
    expect(r.glow).toBe(1);
    expect(r.blur).toBe(0);
    expect(r.scale).toBe(1);
  });

  it("dims, softens and recedes together", () => {
    const r = recessionFor(1);
    expect(r.glow).toBeLessThan(1);
    expect(r.blur).toBeGreaterThan(0);
    expect(r.scale).toBeLessThan(1);
  });

  it("clamps a nonsense value rather than producing negative blur", () => {
    expect(recessionFor(-4).cover).toBe(0);
    expect(recessionFor(9).cover).toBe(1);
    expect(recessionFor(-4).blur).toBe(0);
  });
});
