// The workspace manager is the single owner of surface state, so these tests
// hold the line that nothing else may scatter window state: opening, arranging,
// minimizing and agent-issued operations all resolve here, and an operation that
// is not on the typed list is refused rather than obeyed.

import { beforeEach, describe, expect, it } from "vitest";
import { minimizedSurfaces, stowedSurfaces, useWorkspace, visibleSurfaces } from "./workspace-store";

function reset() {
  useWorkspace.setState({ surfaces: {}, order: [], zTop: 10, focusedId: null, immersive: false, rejected: [], tray: [] });
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

  it("opens diagnostics as a real surface, not a link out of the room", () => {
    // PRODUCT.md §3 lists diagnostics as a surface backed by control.html. The
    // old topbar entry was an <a href="/control">, which made the operator leave
    // the workspace to inspect anything — the inversion §2 calls the biggest
    // structural mistake here. The agent must be able to open it too, so it has
    // to survive validateOp as a known kind.
    const outcome = useWorkspace.getState().applyOps([{ op: "open", surface: { kind: "diagnostics" } }]);

    expect(outcome.rejected).toBe(0);
    const opened = Object.values(useWorkspace.getState().surfaces).find((s) => s.kind === "diagnostics");
    expect(opened).toBeDefined();
    // The kind is still `diagnostics` — /control is still the drawer the
    // architecture gates require — but the surface the operator opens is
    // titled Settings, because it is no longer an embedded read-only dump of
    // the control room. It is the page you change things on.
    expect(opened?.title).toBe("Settings");
  });
});

describe("maximise, hide and stow", () => {
  it("fills the stage and returns to the exact geometry it came from", () => {
    useWorkspace.getState().setViewport({ w: 1400, h: 900 });
    const id = useWorkspace.getState().openSurface({ kind: "mission", source: { kind: "user" } });
    const before = useWorkspace.getState().surfaces[id].geometry;

    useWorkspace.getState().maximizeSurface(id);
    const maximised = useWorkspace.getState().surfaces[id];
    expect(maximised.geometry.x).toBe(0);
    expect(maximised.geometry.w).toBe(1400);
    // The shell measures the room and hands it over, so filling it is exact: the
    // dock rail is already off the bottom of this number.
    expect(maximised.geometry.h).toBe(900);
    expect(maximised.dock).toBe("none");

    useWorkspace.getState().unmaximizeSurface(id);
    expect(useWorkspace.getState().surfaces[id].geometry).toEqual(before);
    expect(useWorkspace.getState().surfaces[id].restoreGeometry).toBeUndefined();
  });

  it("does not lose the original geometry when maximised twice", () => {
    const id = useWorkspace.getState().openSurface({ kind: "logs", source: { kind: "user" } });
    const before = useWorkspace.getState().surfaces[id].geometry;

    useWorkspace.getState().maximizeSurface(id);
    useWorkspace.getState().maximizeSurface(id);

    expect(useWorkspace.getState().surfaces[id].restoreGeometry).toEqual(before);
  });

  it("hides without closing, and stows both kinds for the tray", () => {
    const hidden = useWorkspace.getState().openSurface({ kind: "model", source: { kind: "user" } });
    const minimised = useWorkspace.getState().openSurface({ kind: "worker", source: { kind: "user" } });
    useWorkspace.getState().hideSurface(hidden);
    useWorkspace.getState().minimizeSurface(minimised);

    const state = useWorkspace.getState();
    expect(visibleSurfaces(state).map((s) => s.id)).not.toContain(hidden);
    expect(state.surfaces[hidden]).toBeDefined();
    expect(state.surfaces[hidden].lifecycle).toBe("background");
    expect(stowedSurfaces(state).map((s) => s.id).sort()).toEqual([hidden, minimised].sort());

    useWorkspace.getState().showSurface(hidden);
    const shown = useWorkspace.getState();
    expect(shown.surfaces[hidden].visible).toBe(true);
    expect(shown.surfaces[hidden].geometry.z).toBeGreaterThan(state.surfaces[hidden].geometry.z);
  });

  it("reaches the new operations through the typed list only", () => {
    const id = useWorkspace.getState().openSurface({ kind: "evidence", source: { kind: "user" } });

    expect(useWorkspace.getState().applyOps([{ op: "maximize", id }])).toEqual({ applied: 1, rejected: 0 });
    expect(useWorkspace.getState().surfaces[id].restoreGeometry).toBeDefined();
    expect(useWorkspace.getState().applyOps([{ op: "unmaximize", id }, { op: "hide", id }])).toEqual({ applied: 2, rejected: 0 });
    expect(useWorkspace.getState().surfaces[id].visible).toBe(false);
    expect(useWorkspace.getState().applyOps([{ op: "destroy_world", id }])).toEqual({ applied: 0, rejected: 1 });
  });
});
