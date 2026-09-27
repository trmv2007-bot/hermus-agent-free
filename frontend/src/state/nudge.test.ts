import { beforeEach, describe, expect, it } from "vitest";
import { useWorkspace } from "./workspace-store";
import { GRID_STEP } from "./surfaces";

const store = () => useWorkspace.getState();

function openOne() {
  store().applyOps([{ op: "open", surface: { kind: "terminal", title: "T" } }]);
}

/** A focused, on-grid surface, which is what the arrow keys operate on. */
function ready() {
  useWorkspace.setState({
    surfaces: {},
    order: [],
    focusedId: null,
    stage: { panX: 0, panY: 0, zoom: 1 },
    viewport: { w: 1400, h: 900 },
  });
  openOne();
  const id = store().order[0];
  if (!id) throw new Error("could not open a surface");
  store().focusSurface(id);
  // Start ON the grid. Nudge is a magnet, not a conveyor: it snaps the target
  // to the grid, so a panel parked off-grid lines up on the first press and
  // moves a whole cell from then on. Tests that measure exact cell distances
  // have to begin on a cell, and `snaps a panel parked off the grid` covers
  // the off-grid case explicitly rather than leaving it implied.
  store().moveSurface(id, 3 * GRID_STEP, 2 * GRID_STEP);
  return id;
}

const at = (id: string) => store().surfaces[id].geometry;

describe("nudgeSurface", () => {
  beforeEach(() => {
    useWorkspace.setState({
      surfaces: {},
      order: [],
      focusedId: null,
      stage: { panX: 0, panY: 0, zoom: 1 },
      viewport: { w: 1400, h: 900 },
    });
  });

  it("moves exactly one grid cell per press", () => {
    const id = ready();
    const before = { ...at(id) };
    store().nudgeSurface(id, 1, 0);
    expect(at(id).x - before.x).toBe(GRID_STEP);
    expect(at(id).y).toBe(before.y);
  });

  it("lands on the same place however many times you press", () => {
    // The failure this guards: ten presses Right and you are back where you
    // started, or one pixel further along than last time.
    const id = ready();
    const startX = at(id).x;
    for (let i = 0; i < 10; i += 1) store().nudgeSurface(id, 1, 0);
    expect(at(id).x).toBe(startX + GRID_STEP * 10);
    for (let i = 0; i < 10; i += 1) store().nudgeSurface(id, -1, 0);
    // And back to the START, not to the start rounded to the grid. Because
    // `ready` put it on a cell, the two happen to agree — which is the point:
    // a round trip is the identity, so the nudge is reversible.
    expect(at(id).x).toBe(startX);
  });

  it("pulls an off-grid surface onto the grid, and stays there", () => {
    // A surface the mouse left at an arbitrary pixel. Accumulating a raw step
    // from there leaves it permanently one pixel off the grid; snapping the
    // target brings it in on the first press and it then behaves normally.
    const id = ready();
    store().moveSurface(id, 103, 97);
    store().nudgeSurface(id, 1, 1);
    expect(at(id).x % GRID_STEP).toBe(0);
    expect(at(id).y % GRID_STEP).toBe(0);
    const settled = { ...at(id) };
    store().nudgeSurface(id, 1, 1);
    expect(at(id).x - settled.x).toBe(GRID_STEP);
    expect(at(id).y - settled.y).toBe(GRID_STEP);
  });

  it("moves in all four directions", () => {
    const id = ready();
    const start = { ...at(id) };
    store().nudgeSurface(id, 0, -1);
    expect(at(id).y - start.y).toBe(-GRID_STEP);
    store().nudgeSurface(id, 0, 1);
    expect(at(id).y).toBe(start.y);
    store().nudgeSurface(id, -1, 0);
    expect(at(id).x).toBe(start.x - GRID_STEP);
  });

  it("respects the room rather than walking a panel off the edge", () => {
    const id = ready();
    for (let i = 0; i < 200; i += 1) store().nudgeSurface(id, 0, 1);
    expect(at(id).y).toBeLessThanOrEqual(900);
    for (let i = 0; i < 400; i += 1) store().nudgeSurface(id, -1, 0);
    expect(at(id).x).toBeGreaterThanOrEqual(-store().surfaces[id].geometry.w + 80);
  });

  it("ignores an unknown surface instead of throwing", () => {
    expect(() => store().nudgeSurface("no-such-id", 1, 0)).not.toThrow();
  });

  it("nudging does not change size or focus", () => {
    const id = ready();
    const before = { ...at(id) };
    store().nudgeSurface(id, 1, 1);
    expect(at(id).w).toBe(before.w);
    expect(at(id).h).toBe(before.h);
    expect(store().focusedId).toBe(id);
  });
});
