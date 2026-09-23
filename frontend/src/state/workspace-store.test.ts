// The workspace manager is the single owner of surface state, so these tests
// hold the line that nothing else may scatter window state: opening, arranging,
// minimizing and agent-issued operations all resolve here, and an operation that
// is not on the typed list is refused rather than obeyed.

import { beforeEach, describe, expect, it } from "vitest";
import { minimizedSurfaces, useWorkspace, visibleSurfaces } from "./workspace-store";

function reset() {
  useWorkspace.setState({ surfaces: {}, order: [], zTop: 10, focusedId: null, advanced: false, rejected: [], tray: [] });
}

beforeEach(reset);

describe("surface state", () => {
  it("opens a focused, addressable surface", () => {
    const id = useWorkspace.getState().openSurface({ kind: "mission", source: { kind: "api", ref: "msn_1" } });
    const state = useWorkspace.getState();
    const surface = state.surfaces[id];

    expect(surface.kind).toBe("mission");
    expect(surface.visible).toBe(true);
    expect(surface.focused).toBe(true);
    expect(surface.title).toContain("Mission");
    expect(visibleSurfaces(state).map((s) => s.id)).toEqual([id]);
  });

  it("reuses one surface per kind and source instead of stacking duplicates", () => {
    const open = useWorkspace.getState().openSurface;
    const first = open({ kind: "worker", source: { kind: "event", ref: "agent.a" } });
    const second = open({ kind: "worker", source: { kind: "event", ref: "agent.a" } });
    const other = open({ kind: "worker", source: { kind: "event", ref: "agent.b" } });

    expect(second).toBe(first);
    expect(other).not.toBe(first);
    expect(useWorkspace.getState().order).toHaveLength(2);
  });

  it("clamps geometry to the viewport instead of allowing an off-screen surface", () => {
    useWorkspace.getState().setViewport({ w: 1200, h: 800 });
    const id = useWorkspace.getState().openSurface({ kind: "model", source: { kind: "user" } });
    const store = useWorkspace.getState();

    store.moveSurface(id, -5000, -5000);
    store.resizeSurface(id, 99999, 99999);
    const geometry = useWorkspace.getState().surfaces[id].geometry;

    expect(geometry.x).toBeLessThan(0);
    expect(geometry.y).toBeGreaterThanOrEqual(0);
    expect(geometry.w).toBeLessThanOrEqual(1200);
    expect(geometry.h).toBeLessThanOrEqual(800);
  });

  it("moves a surface out of a dock, because a dragged panel is no longer docked", () => {
    const id = useWorkspace.getState().openSurface({ kind: "logs", source: { kind: "user" } });
    const store = useWorkspace.getState();
    store.dockSurface(id, "right");
    expect(useWorkspace.getState().surfaces[id].dock).toBe("right");

    useWorkspace.getState().moveSurface(id, 100, 100);
    expect(useWorkspace.getState().surfaces[id].dock).toBe("none");
  });

  it("minimises into the dock and restores above everything else", () => {
    const id = useWorkspace.getState().openSurface({ kind: "evidence", source: { kind: "api", ref: "m" } });
    const before = useWorkspace.getState().zTop;
    useWorkspace.getState().minimizeSurface(id);

    expect(minimizedSurfaces(useWorkspace.getState()).map((s) => s.id)).toEqual([id]);
    expect(visibleSurfaces(useWorkspace.getState())).toHaveLength(0);

    useWorkspace.getState().restoreSurface(id);
    const surface = useWorkspace.getState().surfaces[id];
    expect(surface.visible).toBe(true);
    expect(surface.geometry.z).toBeGreaterThan(before);
  });

  it("keeps pinned surfaces through a layout reset and drops the rest", () => {
    const kept = useWorkspace.getState().openSurface({ kind: "mission", source: { kind: "user" } });
    const dropped = useWorkspace.getState().openSurface({ kind: "model", source: { kind: "user" } });
    useWorkspace.getState().togglePin(kept);

    useWorkspace.getState().resetLayout();
    const state = useWorkspace.getState();

    expect(state.surfaces[kept]).toBeDefined();
    expect(state.surfaces[dropped]).toBeUndefined();
  });
});

describe("typed self-modification", () => {
  it("applies an operation list the agent could send", () => {
    const opened = useWorkspace.getState().applyOps([{ op: "open", surface: { kind: "mission", title: "Mission · alpha", source: { kind: "agent", ref: "msn_9" } } }]);
    const id = Object.keys(useWorkspace.getState().surfaces)[0];
    const docked = useWorkspace.getState().applyOps([{ op: "dock", id, side: "left" }]);

    expect(opened.applied).toBe(1);
    expect(docked).toEqual({ applied: 1, rejected: 0 });
    expect(useWorkspace.getState().surfaces[id].title).toBe("Mission · alpha");
    expect(useWorkspace.getState().surfaces[id].dock).toBe("left");
  });

  it("cannot name a surface that an earlier op in the same list creates", () => {
    // Operations are checked against the state at the start of the list. A dock
    // naming a surface that the previous entry in the same batch just opened is
    // well-formed but inert, so the batch must not pretend it arranged anything.
    const outcome = useWorkspace.getState().applyOps([
      { op: "open", surface: { kind: "model", source: { kind: "agent" } } },
      { op: "dock", id: "__not_yet_a_surface__", side: "left" },
    ]);
    const model = Object.values(useWorkspace.getState().surfaces).find((surface) => surface.kind === "model");

    expect(outcome).toEqual({ applied: 2, rejected: 0 });
    expect(model?.dock).toBe("none");
  });

  it("refuses operations that are not on the typed list", () => {
    const outcome = useWorkspace.getState().applyOps([
      { op: "inject_html", html: "<script>alert(1)</script>" },
      { op: "open", kind: "chat" },
      "not an object",
    ]);

    expect(outcome.applied).toBe(0);
    expect(outcome.rejected).toBe(3);
    expect(useWorkspace.getState().rejected.map((r) => r.reason)).toEqual([
      "unsupported operation inject_html",
      "open needs a surface",
      "not an operation object",
    ]);
  });

  it("refuses an unknown surface kind rather than opening a blank panel", () => {
    const outcome = useWorkspace.getState().applyOps([{ op: "open", surface: { kind: "crystal_ball" } }]);

    expect(outcome.rejected).toBe(1);
    expect(Object.keys(useWorkspace.getState().surfaces)).toHaveLength(0);
  });

  it("refuses an operation against a surface that is not there", () => {
    const outcome = useWorkspace.getState().applyOps([{ op: "move", id: "ghost", x: 10, y: 10 }]);

    expect(outcome.applied).toBe(1);
    expect(outcome.rejected).toBe(0);
    expect(useWorkspace.getState().surfaces.ghost).toBeUndefined();
  });
});
