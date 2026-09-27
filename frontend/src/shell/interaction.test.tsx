// @vitest-environment jsdom
//
// Interaction tests, not equation tests.
//
// Both of the bugs this file exists for were invisible to the pure-math tests
// and obvious in the browser:
//
//   - Zoom anchored to the stage origin instead of the cursor, because
//     `zoomAt` wrote the correction into a field the transform never read.
//   - Every drag of the orb registered as a click, because "did the pointer
//     travel?" was measured against the orb's own position, which moves during
//     the very drag being measured.
//
// A unit test on the arithmetic passes in both cases. So these drive real
// pointer events through real DOM and assert on what a person would see.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useWorkspace } from "../state/workspace-store";
import { getPan } from "../state/pan";
import { Orb } from "./Orb";
import { Launcher } from "./Launcher";
import { StageCanvas } from "./StageCanvas";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const store = () => useWorkspace.getState();

/**
 * jsdom has no layout, so every rect is zero unless one is stubbed — and a
 * zero rect is exactly the condition that hid the stage-origin bug, so it is
 * stubbed deliberately, and with a NON-ZERO origin so that any code assuming
 * client coordinates are stage-relative is caught rather than accidentally
 * correct.
 */
function stageBox(): DOMRect {
  return { left: 64, top: 58, width: 1200, height: 700, right: 1264, bottom: 758, x: 64, y: 58, toJSON: () => ({}) } as DOMRect;
}

beforeEach(() => {
  document.body.innerHTML = "";
  // jsdom implements neither pointer capture nor the animations the orb's
  // canvas runs on. Capture is stubbed so a drag is exercised for its
  // GEOMETRY, which is the part that was wrong, rather than failing on
  // plumbing. requestAnimationFrame is deliberately NOT stubbed: jsdom
  // provides a real one, and panning is coalesced through it, so a no-op stub
  // silently freezes the pan and makes a correct zoom look broken.
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = function setPointerCapture() {};
    Element.prototype.releasePointerCapture = function releasePointerCapture() {};
    Element.prototype.hasPointerCapture = function hasPointerCapture() {
      return false;
    };
  }
  store().resetStage();
  store().setFanOpen(false);
  // StageCanvas is rendered for real, not stubbed: the wheel handler is bound
  // natively inside it, and a test that fires wheel at a hand-made element
  // would pass while proving nothing.
  // The stage renders the activity rail, which queries. A provider with
  // retries off keeps a missing gateway from turning every test into a
  // five-second timeout.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <StageCanvas>
        <Orb />
        <Launcher />
      </StageCanvas>
    </QueryClientProvider>,
  );
  const stage = document.querySelector(".stage") as HTMLElement;
  stage.getBoundingClientRect = () => stageBox();
});

afterEach(cleanup);

/** Press, travel, release — the full sequence a drag is made of. */
function drag(from: { x: number; y: number }, to: { x: number; y: number }, steps = 6) {
  const orb = screen.getByRole("button", { name: /core/i });
  fireEvent.pointerDown(orb, { clientX: from.x, clientY: from.y, pointerId: 1 });
  for (let i = 1; i <= steps; i += 1) {
    fireEvent.pointerMove(orb, {
      clientX: from.x + ((to.x - from.x) * i) / steps,
      clientY: from.y + ((to.y - from.y) * i) / steps,
      pointerId: 1,
    });
  }
  fireEvent.pointerUp(orb, { clientX: to.x, clientY: to.y, pointerId: 1 });
}

describe("the launch fan", () => {
  it("opens when the core is pressed without travelling", () => {
    const orb = screen.getByRole("button", { name: /core/i });
    fireEvent.pointerDown(orb, { clientX: 500, clientY: 400, pointerId: 1 });
    fireEvent.pointerUp(orb, { clientX: 500, clientY: 400, pointerId: 1 });
    expect(store().fanOpen).toBe(true);
    expect(screen.getByLabelText(/surfaces you can open/i)).toBeTruthy();
  });

  it("opens a surface when its icon is clicked", () => {
    const orb = screen.getByRole("button", { name: /core/i });
    fireEvent.pointerDown(orb, { clientX: 500, clientY: 400, pointerId: 1 });
    fireEvent.pointerUp(orb, { clientX: 500, clientY: 400, pointerId: 1 });

    const slot = screen.getByRole("button", { name: /open terminal surface/i });
    // The scrim sits above the fan and covers it. If a click on a slot does
    // not land, it is because something is painted on top of it — which is
    // precisely the bug this assertion was written for.
    fireEvent.click(slot);

    expect(store().order.length).toBe(1);
    expect(store().order[0]).toMatch(/terminal/);
    expect(store().fanOpen).toBe(false);
  });

  it("stays shut when the core is dragged, not clicked", () => {
    drag({ x: 500, y: 400 }, { x: 660, y: 520 });
    expect(store().fanOpen).toBe(false);
  });
});

describe("the orb under the pointer", () => {
  it("moves by the cursor's travel and does not open the fan", () => {
    store().setFanOpen(false);
    const before = getPan();
    drag({ x: 500, y: 400 }, { x: 700, y: 400 });
    // A drag that travels 200 screen px must NOT be read as a click.
    expect(store().fanOpen).toBe(false);
    // And the room must not have scrolled under it as a side effect.
    expect(getPan()).toEqual(before);
  });
});

describe("zooming under the cursor", () => {
  const stageEl = () => document.querySelector(".stage") as HTMLElement;

  function zoomOn(clientX: number, clientY: number, deltaY: number) {
    // React attaches wheel passively, and a passive listener cannot
    // preventDefault, so the handler under test binds natively. Fire the real
    // event rather than going through React's synthetic path.
    fireEvent.wheel(stageEl(), { clientX, clientY, deltaY, bubbles: true, cancelable: true });
  }

  it("keeps the same world point under the cursor", () => {
    store().resetStage();
    const at = (clientX: number, clientY: number, zoom: number) => ({
      // Invert the stage transform: world = (stage-local - pan) / zoom.
      x: (clientX - 64 - getPan().x) / zoom,
      y: (clientY - 58 - getPan().y) / zoom,
    });

    // Zoom in on a point far from the stage origin, the case where anchoring to
    // (0,0) instead of the cursor is visible.
    const probe = { clientX: 1100, clientY: 620 };
    const before = at(probe.clientX, probe.clientY, 1);
    zoomOn(probe.clientX, probe.clientY, -120);
    expect(store().stage.zoom).toBeGreaterThan(1);
    const after = at(probe.clientX, probe.clientY, store().stage.zoom);
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);

    // And again in the other direction, back past the origin.
    zoomOn(probe.clientX, probe.clientY, -120);
    zoomOn(probe.clientX, probe.clientY, 240);
    const back = at(probe.clientX, probe.clientY, store().stage.zoom);
    expect(back.x).toBeCloseTo(before.x, 6);
    expect(back.y).toBeCloseTo(before.y, 6);
  });

  it("writes the pan to where the transform actually reads it", () => {
    // The bug: the correction was stored in the workspace store while the
    // world transform read from state/pan.ts. This asserts the two agree.
    store().resetStage();
    const stage = document.querySelector(".stage") as HTMLElement;
    fireEvent.wheel(stage, { clientX: 1000, clientY: 600, deltaY: -120, bubbles: true, cancelable: true });
    const pan = getPan();
    expect(Number.isFinite(pan.x)).toBe(true);
    expect(Number.isFinite(pan.y)).toBe(true);
    // Anchoring at a point far from the origin must move the pan a long way.
    // If it stayed at zero, the zoom was anchored to the stage origin instead.
    expect(Math.hypot(pan.x, pan.y)).toBeGreaterThan(100);
    expect(store().stage.panX).toBeCloseTo(pan.x, 6);
    expect(store().stage.panY).toBeCloseTo(pan.y, 6);
  });
});
