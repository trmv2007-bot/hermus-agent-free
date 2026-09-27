// The core's placement rules, held without a browser.
//
// These are the behaviours that make the orb read as an instrument rather than a
// decoration: it sits beside the work, it gets out of the way when the room is
// full, and it never hides behind the dock.

import { describe, expect, it } from "vitest";
import type { Surface, Viewport } from "./surfaces";
import { ORB_MIN, ORB_SIZE, orbStateFor, placeOrb } from "./orb";

// The shell hands placeOrb the room it measured: the stage minus the dock rail.
const VIEWPORT: Viewport = { w: 1280, h: 800 };

function surface(id: string, x: number, y: number, w: number, h: number, z = 10): Surface {
  return {
    id,
    kind: "mission",
    title: id,
    geometry: { x, y, w, h, z },
    visible: true,
    focused: false,
    dock: "none",
    lifecycle: "active",
    source: { kind: "user" },
    permissions: { read: true, act: false },
    pinned: false,
    persistent: true,
    createdAt: 0,
    updatedAt: 0,
  };
}

describe("placeOrb", () => {
  it("takes the middle of the room at hero size when nothing is on screen", () => {
    // An empty room is the one moment the core can BE the room. It used to hold
    // the lower-right corner at ORB_SIZE, which read as an ornament in a 1280px
    // stage rather than as the subject.
    const placed = placeOrb([], VIEWPORT);
    expect(placed.anchor).toBe("empty");
    expect(placed.size).toBeGreaterThan(ORB_SIZE);
    // Dead centre, both axes.
    expect(placed.x + placed.size / 2).toBeCloseTo(VIEWPORT.w / 2, 0);
    expect(placed.y + placed.size / 2).toBeCloseTo(VIEWPORT.h / 2, 0);
  });

  it("never grows past what the room can hold", () => {
    const tiny = placeOrb([], { w: 200, h: 200 });
    expect(tiny.size).toBeLessThanOrEqual(200 - 18 * 2);
    expect(tiny.size).toBeGreaterThanOrEqual(56);
  });

  it("sits outside the focused surface on whichever side has room", () => {
    const left = surface("a", 40, 60, 500, 400, 12);
    const placed = placeOrb([left], VIEWPORT);
    expect(placed.anchor).toBe("focused");
    expect(placed.x).toBe(40 + 500 + 18);

    const right = surface("b", 700, 60, 500, 400, 12);
    const flipped = placeOrb([right], VIEWPORT);
    expect(flipped.x).toBe(700 - ORB_SIZE - 18);
  });

  it("shrinks and finds a corner rather than covering a full room", () => {
    // ~68% of the viewport is spoken for, and the only free square is above the
    // dock at the bottom right — where a crowded orb has to go.
    const tiles = [
      surface("big", 0, 0, 800, 700, 12),
      surface("wide", 820, 0, 460, 300, 11),
    ];
    const placed = placeOrb(tiles, VIEWPORT);
    expect(placed.anchor).toBe("crowded");
    expect(placed.size).toBe(ORB_MIN);
    expect(placed.x).toBeGreaterThanOrEqual(0);
    expect(placed.y + placed.size).toBeLessThanOrEqual(VIEWPORT.h);
    const overlap = tiles.reduce((sum, tile) => {
      const g = tile.geometry;
      const dx = Math.max(0, Math.min(placed.x + placed.size, g.x + g.w) - Math.max(placed.x, g.x));
      const dy = Math.max(0, Math.min(placed.y + placed.size, g.y + g.h) - Math.max(placed.y, g.y));
      return sum + dx * dy;
    }, 0);
    expect(overlap).toBe(0);
  });

  it("still shrinks when a fully tiled room leaves no clear corner", () => {
    const tiles = [
      surface("tl", 0, 0, 640, 400, 12),
      surface("tr", 640, 0, 640, 400, 11),
      surface("bl", 0, 400, 640, 400, 10),
      surface("br", 640, 400, 640, 400, 10),
    ];
    const placed = placeOrb(tiles, VIEWPORT);
    expect(placed.anchor).toBe("crowded");
    expect(placed.size).toBe(ORB_MIN);
  });

  it("stays inside the usable band on a viewport barely larger than itself", () => {
    const big = surface("a", 0, 0, 200, 200, 12);
    const placed = placeOrb([big], { w: 300, h: 260 });
    expect(placed.x).toBeGreaterThanOrEqual(0);
    expect(placed.y).toBeGreaterThanOrEqual(0);
    expect(placed.x + placed.size).toBeLessThanOrEqual(300);
  });
});

describe("orbStateFor", () => {
  it("shows the newest signal, not the loudest older one", () => {
    // The tray is newest-first, which is the order the store keeps it in.
    expect(orbStateFor([{ label: "mission_state" }, { label: "mission_verification" }])).toBe("working");
    expect(orbStateFor([{ label: "emergency_stop" }, { label: "mission_state" }])).toBe("blocked");
    expect(orbStateFor([{ label: "mission_claim_disagreement" }])).toBe("attention");
  });

  it("rests when the runtime is quiet", () => {
    expect(orbStateFor([])).toBe("idle");
  });
});
