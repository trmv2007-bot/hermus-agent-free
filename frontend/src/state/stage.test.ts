import { describe, expect, it } from "vitest";
import { useWorkspace } from "../state/workspace-store";
import { clampZoom, ZOOM_MAX, ZOOM_MIN } from "./surfaces";
import { getPan } from "./pan";

// The pan lives in state/pan.ts now and the zoom in the store, so a test that
// reads both has to read both from where they actually are. Reading pan from
// the store silently tests a value nothing renders, which is how a wrong zoom
// formula can pass here and fail under the cursor.
function view() {
  const { zoom } = useWorkspace.getState().stage;
  return { panX: getPan().x, panY: getPan().y, zoom };
}

/** Where a world point lands on screen. Inverse of the zoom maths. */
function toScreen(worldX: number, worldY: number, stage: { panX: number; panY: number; zoom: number }) {
  return { x: worldX * stage.zoom + stage.panX, y: worldY * stage.zoom + stage.panY };
}
/** What was under the cursor before the zoom. */
function under(sx: number, sy: number, stage: { panX: number; panY: number; zoom: number }) {
  return { x: (sx - stage.panX) / stage.zoom, y: (sy - stage.panY) / stage.zoom };
}

const store = () => useWorkspace.getState();
const stage = () => view();

describe("stage zoom", () => {
  it("clamps to a range where panels stay usable", () => {
    expect(clampZoom(0.01)).toBe(ZOOM_MIN);
    expect(clampZoom(99)).toBe(ZOOM_MAX);
    expect(clampZoom(Number.NaN)).toBe(1);
    expect(clampZoom(1)).toBe(1);
  });

  it("keeps the world point under the cursor fixed while zooming in", () => {
    // This is the whole reason zoomAt exists rather than `zoom *= f`.
    store().resetStage();
    store().zoomAt(640, 360, 1.2);
    store().zoomAt(640, 360, 1.2);

    const before = under(640, 360, { panX: 0, panY: 0, zoom: 1 });
    const after = toScreen(before.x, before.y, stage());

    expect(after.x).toBeCloseTo(640, 6);
    expect(after.y).toBeCloseTo(360, 6);
    // And it really did zoom, rather than the maths being a no-op.
    expect(store().stage.zoom).toBeCloseTo(1.44, 6);
  });

  it("keeps the cursor point fixed when zooming out from an off-centre point", () => {
    store().resetStage();
    store().panBy(200, -60);
    const anchor = under(1200, 200, stage());
    store().zoomAt(1200, 200, 1 / 1.12);
    const after = toScreen(anchor.x, anchor.y, stage());
    expect(after.x).toBeCloseTo(1200, 6);
    expect(after.y).toBeCloseTo(200, 6);
    expect(store().stage.zoom).toBeLessThan(1);
  });

  it("does not drift when you zoom in and back out at the same point", () => {
    store().resetStage();
    const origin = { ...stage() };
    store().zoomAt(500, 400, 1.12);
    store().zoomAt(500, 400, 1 / 1.12);
    const s = stage();
    // Floating point, not a broken inverse.
    expect(s.zoom).toBeCloseTo(origin.zoom, 6);
    expect(s.panX).toBeCloseTo(origin.panX, 6);
    expect(s.panY).toBeCloseTo(origin.panY, 6);
  });

  it("pans by whole screen pixels", () => {
    store().resetStage();
    store().panBy(30, -12);
    expect(getPan().x).toBe(30);
    expect(getPan().y).toBe(-12);
    store().panBy(5, 5);
    expect(getPan().x).toBe(35);
  });

  it("refuses to zoom past the limits instead of inverting or stretching", () => {
    store().resetStage();
    for (let i = 0; i < 40; i += 1) store().zoomAt(400, 300, 1.4);
    expect(store().stage.zoom).toBe(ZOOM_MAX);
    for (let i = 0; i < 80; i += 1) store().zoomAt(400, 300, 1 / 1.4);
    expect(store().stage.zoom).toBe(ZOOM_MIN);
  });

  it("resets to the origin and 1:1", () => {
    store().resetStage();
    store().panBy(500, 500);
    store().zoomAt(100, 100, 1.5);
    store().resetStage();
    expect(stage()).toEqual({ panX: 0, panY: 0, zoom: 1 });
  });
});
