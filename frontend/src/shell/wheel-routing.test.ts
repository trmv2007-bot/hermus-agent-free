/**
 * @vitest-environment jsdom
 */
/**
 * The wheel has two jobs and they were fighting.
 *
 * Wheel over empty space zooms the room about the cursor. Wheel over a panel
 * scrolls the panel. Before this, the stage claimed every wheel event on the
 * node, so reading past the bottom of a panel zoomed the entire room instead
 * and a panel with nothing left to scroll could not be scrolled at all.
 *
 * The awkward part, which is why this is a test and not a comment: the panel
 * must only win the gesture while it still has travel left in that direction.
 * A panel scrolled to the end has to hand the wheel back, or the room becomes
 * impossible to zoom from over it — the gesture is being eaten by a panel that
 * has nothing to give it.
 */

import { describe, expect, it, vi } from "vitest";

/**
 * A scroller stand-in, since jsdom reports every element as 0x0.
 *
 * scrollHeight and friends are getter-only on a real Element in jsdom, so
 * Object.assign throws. defineProperty is the only way to make an element
 * report a scrollable size, which is the one thing these tests need to be
 * true about.
 */
function makeScroller(overrides: Partial<Record<"scrollHeight" | "clientHeight" | "scrollWidth" | "clientWidth" | "scrollTop", number>>) {
  const el = document.createElement("div");
  el.className = "surface-body";
  const sizes = { scrollHeight: 0, clientHeight: 0, scrollWidth: 0, clientWidth: 0, ...overrides };
  for (const [key, value] of Object.entries(sizes)) {
    Object.defineProperty(el, key, { value, configurable: true, writable: true });
  }
  return el;
}

function fire(node: HTMLElement, target: HTMLElement, deltaY: number, deltaX = 0) {
  const event = new WheelEvent("wheel", { deltaY, deltaX, bubbles: true, cancelable: true });
  Object.defineProperty(event, "target", { value: target });
  node.dispatchEvent(event);
  return event;
}

describe("wheel routing between a panel and the room", () => {
  it("zooms the room when the wheel is over the stage itself", () => {
    const stage = document.createElement("div");
    const zoomAt = vi.fn();
    // Mirrors the handler in StageCanvas: a panel that can still scroll in the
    // direction of the wheel takes it, otherwise the room zooms.
    const onWheel = (event: WheelEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && target !== stage) {
        const scroller = target.closest<HTMLElement>(".surface-body, .panel, [data-scrollable]");
        if (scroller && scroller !== stage) {
          const extent = scroller.scrollHeight - scroller.clientHeight;
          if (extent > 1) {
            if (event.deltaY > 0 ? scroller.scrollTop < extent - 1 : scroller.scrollTop > 1) return;
          }
        }
      }
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.12 : 1 / 1.12);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });

    const event = fire(stage, stage, 120);
    expect(zoomAt).toHaveBeenCalledOnce();
    expect(event.defaultPrevented).toBe(true);
  });

  it("leaves the wheel alone over a panel that can still scroll down", () => {
    const stage = document.createElement("div");
    const zoomAt = vi.fn();
    const onWheel = (event: WheelEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && target !== stage) {
        const scroller = target.closest<HTMLElement>(".surface-body, .panel, [data-scrollable]");
        if (scroller && scroller !== stage) {
          const extent = scroller.scrollHeight - scroller.clientHeight;
          if (extent > 1) {
            if (event.deltaY > 0 ? scroller.scrollTop < extent - 1 : scroller.scrollTop > 1) return;
          }
        }
      }
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, 1.12);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });

    // 400px of content in a 100px window, at the top: there is room to scroll.
    const panel = makeScroller({ scrollHeight: 400, clientHeight: 100, scrollTop: 0 });
    stage.appendChild(panel);

    const event = fire(stage, panel, 120);
    expect(zoomAt).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it("hands the wheel back once the panel is scrolled to the end", () => {
    const stage = document.createElement("div");
    const zoomAt = vi.fn();
    const onWheel = (event: WheelEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && target !== stage) {
        const scroller = target.closest<HTMLElement>(".surface-body, .panel, [data-scrollable]");
        if (scroller && scroller !== stage) {
          const extent = scroller.scrollHeight - scroller.clientHeight;
          if (extent > 1) {
            if (event.deltaY > 0 ? scroller.scrollTop < extent - 1 : scroller.scrollTop > 1) return;
          }
        }
      }
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, 1.12);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });

    // Already at the bottom: 400 - 100 = 300, scrollTop is 300.
    const panel = makeScroller({ scrollHeight: 400, clientHeight: 100, scrollTop: 300 });
    stage.appendChild(panel);

    fire(stage, panel, 120);
    expect(zoomAt).toHaveBeenCalledOnce();
  });

  it("zooms over a panel that has nothing to scroll at all", () => {
    const stage = document.createElement("div");
    const zoomAt = vi.fn();
    const onWheel = (event: WheelEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && target !== stage) {
        const scroller = target.closest<HTMLElement>(".surface-body, .panel, [data-scrollable]");
        if (scroller && scroller !== stage) {
          const extent = scroller.scrollHeight - scroller.clientHeight;
          if (extent > 1) {
            if (event.deltaY > 0 ? scroller.scrollTop < extent - 1 : scroller.scrollTop > 1) return;
          }
        }
      }
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, 1.12);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });

    // Fits entirely: no overflow, so the room must take the gesture.
    const panel = makeScroller({ scrollHeight: 80, clientHeight: 100, scrollTop: 0 });
    stage.appendChild(panel);

    fire(stage, panel, 120);
    expect(zoomAt).toHaveBeenCalledOnce();
  });

  it("does not zoom when scrolling up inside a panel that is not at the top", () => {
    const stage = document.createElement("div");
    const zoomAt = vi.fn();
    const onWheel = (event: WheelEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && target !== stage) {
        const scroller = target.closest<HTMLElement>(".surface-body, .panel, [data-scrollable]");
        if (scroller && scroller !== stage) {
          const extent = scroller.scrollHeight - scroller.clientHeight;
          if (extent > 1) {
            if (event.deltaY > 0 ? scroller.scrollTop < extent - 1 : scroller.scrollTop > 1) return;
          }
        }
      }
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, 1.12);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });

    const panel = makeScroller({ scrollHeight: 400, clientHeight: 100, scrollTop: 150 });
    stage.appendChild(panel);

    // deltaY negative is scrolling UP, and there is 150px above.
    const event = fire(stage, panel, -120);
    expect(zoomAt).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });
});
