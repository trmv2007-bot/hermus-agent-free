import { describe, expect, it } from "vitest";
import { gridCell } from "./grid";
import { ZOOM_MAX, ZOOM_MIN } from "./surfaces";

describe("gridCell", () => {
  it("scales with the zoom, and steps rather than leaving the band", () => {
    expect(gridCell(1)).toBeCloseTo(52, 5);
    // Zooming IN makes the floor denser, but the 96px ceiling is a ceiling:
    // past it the cell steps down a factor of four so the lines stay a floor
    // rather than a sparse dot grid. So 2x is not simply 2x the cell.
    expect(gridCell(2)).toBeLessThan(gridCell(1));
    expect(gridCell(0.5)).toBeLessThan(gridCell(1));
    // Two steps in, and the band is what matters rather than the raw scale.
    for (const z of [0.35, 0.5, 1, 1.5, 2, 2.2]) {
      expect(gridCell(z)).toBeGreaterThanOrEqual(12);
      expect(gridCell(z)).toBeLessThanOrEqual(96);
    }
  });

  it("stays in a band where 1px lines read as a floor", () => {
    // Below ~12px the lines overlap into a solid wash; above ~96px the floor is
    // too sparse to feel like ground. Both were reported as "the background
    // looks wrong" at the extremes of the zoom range.
    for (let z = ZOOM_MIN; z <= ZOOM_MAX; z += 0.01) {
      const cell = gridCell(z);
      expect(cell).toBeGreaterThanOrEqual(12);
      expect(cell).toBeLessThanOrEqual(96);
    }
  });

  it("never leaves a gap in the zoom range", () => {
    // Consecutive steps must not jump more than one factor of 4, or the floor
    // pops visibly while the wheel is turning.
    let previous = gridCell(ZOOM_MIN);
    for (let z = ZOOM_MIN; z <= ZOOM_MAX; z += 0.005) {
      const cell = gridCell(z);
      const ratio = cell / previous;
      expect(ratio).toBeLessThanOrEqual(4.0001);
      expect(ratio).toBeGreaterThanOrEqual(0.2499);
      previous = cell;
    }
  });

  it("handles a degenerate zoom without hanging or returning junk", () => {
    // The stepping is a loop; a zero or negative value would spin forever or
    // walk off to infinity.
    expect(gridCell(0)).toBeGreaterThanOrEqual(12);
    expect(gridCell(-1)).toBeGreaterThanOrEqual(12);
    expect(Number.isFinite(gridCell(1))).toBe(true);
  });
});
