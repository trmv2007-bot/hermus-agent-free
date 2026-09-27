// The launch fan has to stay reachable wherever the pod is dragged, which is a
// geometry promise, not a styling one — so it is checked as geometry.

import { describe, expect, it } from "vitest";
import type { Viewport } from "./surfaces";
import { EDGE, fanCenterDeg, fanRadius, fanSlots, HERO_SPAN_DEG, KIND_GLYPH, LAUNCHER_KINDS, POD_SIZE, RING_CLEARANCE, SLOT_SIZE } from "./launcher";

const ROOM: Viewport = { w: 1280, h: 708 };
const KINDS = LAUNCHER_KINDS;

function parkedAt(x: number, y: number) {
  return { x, y };
}

function mean(slots: Array<{ x: number; y: number }>): { x: number; y: number } {
  const total = slots.reduce((sum, slot) => ({ x: sum.x + slot.x + SLOT_SIZE / 2, y: sum.y + slot.y + SLOT_SIZE / 2 }), { x: 0, y: 0 });
  return { x: total.x / slots.length, y: total.y / slots.length };
}

describe("fanSlots", () => {
  it("gives every launchable kind a slot inside the room", () => {
    const slots = fanSlots(parkedAt(EDGE, ROOM.h - POD_SIZE - EDGE), ROOM, KINDS);
    expect(slots.map((slot) => slot.kind)).toEqual(KINDS);
    for (const slot of slots) {
      expect(slot.x).toBeGreaterThanOrEqual(0);
      expect(slot.y).toBeGreaterThanOrEqual(0);
      expect(slot.x + SLOT_SIZE).toBeLessThanOrEqual(ROOM.w);
      expect(slot.y + SLOT_SIZE).toBeLessThanOrEqual(ROOM.h);
    }
  });

  it("opens away from the corner the pod is parked in", () => {
    const bottomLeft = parkedAt(EDGE, ROOM.h - POD_SIZE - EDGE);
    const centre = { x: bottomLeft.x + POD_SIZE / 2, y: bottomLeft.y + POD_SIZE / 2 };
    const fan = mean(fanSlots(bottomLeft, ROOM, KINDS));
    expect(fan.x).toBeGreaterThanOrEqual(centre.x);
    expect(fan.y).toBeLessThanOrEqual(centre.y);

    const topRight = parkedAt(ROOM.w - POD_SIZE - EDGE, EDGE);
    const corner = { x: topRight.x + POD_SIZE / 2, y: topRight.y + POD_SIZE / 2 };
    const other = mean(fanSlots(topRight, ROOM, KINDS));
    expect(other.x).toBeLessThanOrEqual(corner.x);
    expect(other.y).toBeGreaterThanOrEqual(corner.y);
  });

  it("widens the arc until the slots stop touching", () => {
    expect(fanRadius(12, SLOT_SIZE)).toBeGreaterThan(fanRadius(3, SLOT_SIZE));

    // Away from the edges, so the test measures the arc and not the clamp.
    const slots = fanSlots({ x: 300, y: 300 }, ROOM, KINDS);
    const centres = slots.map((slot) => [slot.x + SLOT_SIZE / 2, slot.y + SLOT_SIZE / 2]);
    for (let i = 1; i < centres.length; i += 1) {
      const [x0, y0] = centres[i - 1];
      const [x1, y1] = centres[i];
      expect(Math.hypot(x1 - x0, y1 - y0)).toBeGreaterThanOrEqual(SLOT_SIZE);
    }
  });

  it("keeps the entries clickable in a room barely bigger than the pod", () => {
    const tiny: Viewport = { w: 190, h: 170 };
    const slots = fanSlots(parkedAt(EDGE, tiny.h - POD_SIZE - EDGE), tiny, KINDS);
    expect(slots).toHaveLength(KINDS.length);
    for (const slot of slots) {
      expect(slot.x + SLOT_SIZE).toBeLessThanOrEqual(tiny.w);
      expect(slot.y + SLOT_SIZE).toBeLessThanOrEqual(tiny.h);
    }
  });

  it("rings a hero core evenly instead of pointing at one side", () => {
    // A centred, room-sized core has no "away" to face, so the fan goes all the
    // way round. Eleven entries on the same 100-degree arc would overlap badly.
    const slots = fanSlots({ x: 490, y: 200 }, ROOM, KINDS, POD_SIZE, SLOT_SIZE, HERO_SPAN_DEG);
    expect(slots).toHaveLength(KINDS.length);

    // No two entries may sit closer than one slot width, or they touch.
    const centres = slots.map((slot) => [slot.x + SLOT_SIZE / 2, slot.y + SLOT_SIZE / 2]);
    for (let i = 0; i < centres.length; i += 1) {
      for (let j = i + 1; j < centres.length; j += 1) {
        const [x0, y0] = centres[i];
        const [x1, y1] = centres[j];
        expect(Math.hypot(x1 - x0, y1 - y0)).toBeGreaterThanOrEqual(SLOT_SIZE);
      }
    }
  });

  it("keeps a ring outside the core it is drawn around", () => {
    // A 300px hero core is 150px out from its centre. A ring at the old fixed
    // 92px floor drew every entry INSIDE the orb, where the orb's own hit area
    // swallowed the click and the icons could not be opened at all.
    const core = 300;
    const cx = 1400 / 2;
    const cy = 700 / 2;
    const room: Viewport = { w: 1400, h: 700 };
    const anchor = { x: cx - POD_SIZE / 2, y: cy - POD_SIZE / 2 };
    const inner = core / 2 + RING_CLEARANCE;
    const slots = fanSlots(anchor, room, KINDS, POD_SIZE, SLOT_SIZE, HERO_SPAN_DEG, inner);

    for (const slot of slots) {
      const centreX = slot.x + SLOT_SIZE / 2;
      const centreY = slot.y + SLOT_SIZE / 2;
      expect(Math.hypot(centreX - cx, centreY - cy)).toBeGreaterThan(core / 2 + SLOT_SIZE / 2);
    }
  });

  it("keeps a ring inside the room at every size it is shown at", () => {
    for (const [w, h] of [
      [1280, 708],
      [900, 600],
      [700, 500],
    ] as [number, number][]) {
      const room: Viewport = { w, h };
      const cx = w / 2;
      const cy = h / 2;
      const size = Math.max(56, Math.min(300, Math.min(w, h) - EDGE * 2));
      const roomRadius = Math.min(w, h) / 2 - SLOT_SIZE - EDGE;
      const inner = Math.max(0, Math.min(size / 2 + RING_CLEARANCE, roomRadius));
      const slots = fanSlots({ x: cx - POD_SIZE / 2, y: cy - POD_SIZE / 2 }, room, KINDS, POD_SIZE, SLOT_SIZE, HERO_SPAN_DEG, inner);
      expect(slots).toHaveLength(KINDS.length);
      for (const slot of slots) {
        expect(slot.x).toBeGreaterThanOrEqual(0);
        expect(slot.y).toBeGreaterThanOrEqual(0);
        expect(slot.x + SLOT_SIZE).toBeLessThanOrEqual(w);
        expect(slot.y + SLOT_SIZE).toBeLessThanOrEqual(h);
      }
    }
  });
});

describe("fanCenterDeg", () => {
  it("swings continuously instead of snapping between quadrants", () => {
    // The bug being fixed: the angle was one of four fixed diagonals, so
    // dragging the pod across the screen's horizontal midpoint made all eleven
    // entries teleport at once. Every position now gets its own angle.
    const left = fanCenterDeg({ x: 100, y: 300 }, POD_SIZE, ROOM);
    const nearMid = fanCenterDeg({ x: 600, y: 300 }, POD_SIZE, ROOM);
    const right = fanCenterDeg({ x: 1100, y: 300 }, POD_SIZE, ROOM);

    expect(left).not.toBe(nearMid);
    expect(nearMid).not.toBe(right);
    // And the fan opens away from the middle of the room: a pod hard against the
    // left edge points the arc rightward (0deg), one against the right edge
    // points it leftward (180deg). The midpoint between them is the diagonal.
    expect(left).toBeLessThan(45);
    expect(right).toBeGreaterThan(135);
  });

  it("moves in small steps, not in quadrant jumps", () => {
    const a = fanCenterDeg({ x: 300, y: 400 }, POD_SIZE, ROOM);
    const b = fanCenterDeg({ x: 310, y: 400 }, POD_SIZE, ROOM);
    expect(Math.abs(a - b)).toBeLessThan(10);
  });
});

describe("launcher content", () => {
  it("names and glyphs every kind the operator can open", () => {
    // A glyph with no name is a puzzle, so both are required per kind.
    for (const kind of KINDS) {
      expect(KIND_GLYPH[kind]).toBeTruthy();
      expect(typeof KIND_GLYPH[kind]).toBe("string");
    }
  });
});
