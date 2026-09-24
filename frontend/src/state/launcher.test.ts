// The launch fan has to stay reachable wherever the pod is dragged, which is a
// geometry promise, not a styling one — so it is checked as geometry.

import { describe, expect, it } from "vitest";
import type { Viewport } from "./surfaces";
import { EDGE, fanRadius, fanSlots, LAUNCHER_KINDS, POD_SIZE, SLOT_SIZE } from "./launcher";

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
});
