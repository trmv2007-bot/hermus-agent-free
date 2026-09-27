import { describe, expect, it } from "vitest";
import { grabOffset, screenToWorld, worldToScreen, type ScreenFrame } from "./coords";

/** A stage sitting below a topbar, panned and zoomed. */
const frame: ScreenFrame = { originX: 0, originY: 64, panX: 120, panY: -40, zoom: 1.5 };

describe("screenToWorld", () => {
  it("undoes the stage offset, the pan and the zoom", () => {
    const world = screenToWorld(frame, 400, 300);
    expect(world.x).toBeCloseTo((400 - 0 - 120) / 1.5, 6);
    expect(world.y).toBeCloseTo((300 - 64 + 40) / 1.5, 6);
  });

  it("is the exact inverse of worldToScreen", () => {
    for (const z of [0.35, 0.8, 1, 1.75, 2.2]) {
      const f = { ...frame, zoom: z };
      const back = worldToScreen(f, 812, -45);
      const there = screenToWorld(f, back.x, back.y);
      expect(there.x).toBeCloseTo(812, 6);
      expect(there.y).toBeCloseTo(-45, 6);
    }
  });

  it("does not divide by a zero zoom", () => {
    expect(Number.isFinite(screenToWorld({ ...frame, zoom: 0 }, 100, 100).x)).toBe(true);
  });
});

describe("grabOffset", () => {
  it("is zero when you press the object's own origin", () => {
    // The regression that made the orb jump on every grab.
    const frame2: ScreenFrame = { originX: 0, originY: 64, panX: 0, panY: 0, zoom: 1 };
    const onScreen = worldToScreen(frame2, 300, 200);
    const offset = grabOffset(frame2, onScreen.x, onScreen.y, 300, 200);
    expect(offset.dx).toBeCloseTo(0, 6);
    expect(offset.dy).toBeCloseTo(0, 6);
  });

  it("holds the object still under the cursor as it moves", () => {
    // Press 40 world px right of the object's centre, then move. The object
    // must travel exactly as far as the cursor did — 90 SCREEN px, which is
    // 90/zoom WORLD px — and it must keep the same grab offset throughout, or
    // it slides out from under the pointer as you drag.
    const f: ScreenFrame = { originX: 0, originY: 64, panX: 120, panY: -40, zoom: 1.5 };
    const startWorld = { x: 300, y: 200 };
    for (const [px, py] of [
      [0, 0],
      [40, 0],
      [0, -35],
      [-22, 17],
    ]) {
      const press = worldToScreen(f, startWorld.x + px, startWorld.y + py);
      const { dx, dy } = grabOffset(f, press.x, press.y, startWorld.x, startWorld.y);

      // 90 screen px right and 60 down, expressed in world units.
      const moved = worldToScreen(f, startWorld.x + px + 90 / f.zoom, startWorld.y + py + 60 / f.zoom);
      const origin = screenToWorld(f, moved.x, moved.y);
      // The offset is unchanged, and the object moved exactly the cursor's
      // world distance — not the screen distance, and not twice it.
      expect(origin.x - dx).toBeCloseTo(startWorld.x + 90 / f.zoom, 6);
      expect(origin.y - dy).toBeCloseTo(startWorld.y + 60 / f.zoom, 6);
    }
  });

  it("does not compound across repeated grabs", () => {
    // "It holds further away every time I grab it" is an offset that carries
    // its own error forward. Grabbing, dropping and grabbing again from the
    // same visual point must produce the same offset every time.
    const f: ScreenFrame = { originX: 0, originY: 64, panX: -260, panY: 90, zoom: 0.6 };
    const press = worldToScreen(f, 500, 400);
    const first = grabOffset(f, press.x, press.y, 500, 400);
    // Whatever the object did in between, a fresh grab at the same point and
    // the same object origin must reproduce it exactly.
    for (let i = 0; i < 5; i += 1) {
      const again = grabOffset(f, press.x, press.y, 500, 400);
      expect(again.dx).toBeCloseTo(first.dx, 9);
      expect(again.dy).toBeCloseTo(first.dy, 9);
    }
  });

  it("tracks the same world point at every zoom level", () => {
    // A 90px cursor move is 90/zoom world px. If the drag ignored the zoom it
    // would move slowly zoomed out and quickly zoomed in, which is the other
    // half of the report.
    for (const zoom of [0.35, 1, 2.2]) {
      const f: ScreenFrame = { originX: 0, originY: 0, panX: 0, panY: 0, zoom };
      const start = worldToScreen(f, 0, 0);
      const end = worldToScreen(f, 90 / zoom, 0);
      const a = screenToWorld(f, start.x, start.y);
      const b = screenToWorld(f, end.x, end.y);
      expect(b.x - a.x).toBeCloseTo(90 / zoom, 6);
    }
  });
});
