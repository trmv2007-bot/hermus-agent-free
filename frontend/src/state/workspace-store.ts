// The one owner of workspace state.
//
// Every surface's position, size, order, focus and lifecycle lives here, and is
// changed only through the operations below. The agent reaches the same
// operations through `applyOps` — it cannot inject markup or run code in the
// shell, it can only ask for a surface to open, move or close, which is what
// makes self-modification observable and reversible.

import { create } from "zustand";
import {
  CASCADE_STEP,
  KIND_TITLES,
  MIN_H,
  MIN_W,
  clamp,
  defaultSize,
  type DockSide,
  type Geometry,
  type Surface,
  type SurfaceKind,
  type SurfaceSource,
  type Viewport,
} from "./surfaces";

export const LAYOUT_VERSION = 1;
const STORAGE_KEY = "hermus.workspace.layout";
const TITLE_MAX = 80;

export interface OpenRequest {
  kind: SurfaceKind;
  title?: string;
  source?: SurfaceSource;
  scope?: string;
  /** Temporary surfaces are not restored from storage and are dropped on reset. */
  temporary?: boolean;
  act?: boolean;
}

/** The typed operations the agent and the shell both use. */
export type WorkspaceOp =
  | { op: "open"; surface: OpenRequest }
  | { op: "close"; id: string }
  | { op: "focus"; id: string }
  | { op: "move"; id: string; x: number; y: number }
  | { op: "resize"; id: string; w: number; h: number }
  | { op: "dock"; id: string; side: DockSide }
  | { op: "minimize"; id: string }
  | { op: "maximize"; id: string }
  | { op: "unmaximize"; id: string }
  | { op: "hide"; id: string }
  | { op: "show"; id: string }
  | { op: "restore"; id: string }
  | { op: "pin"; id: string; pinned: boolean }
  | { op: "rename"; id: string; title: string };

interface WorkspaceState {
  surfaces: Record<string, Surface>;
  order: string[];
  zTop: number;
  focusedId: string | null;
  /** Full-HUD mode: the topbar and dock slide to the edges and the room owns the screen. */
  immersive: boolean;
  viewport: Viewport;
  /**
   * Where the core ended up this frame. The orb paints itself from its own
   * placement maths, but the pod needs the same answer to decide whether to
   * dock against it or stand off — two components guessing independently is how
   * they drift apart, so the orb publishes the result here.
   */
  orbPlacement: { x: number; y: number; size: number } | null;
  setOrbPlacement: (placement: { x: number; y: number; size: number } | null) => void;
  /**
   * Whether the launch fan is unfolded.
   *
   * This lives in the store, not in the Launcher, because the fan has two
   * controls in two different components: the core opens it and the core closes
   * it. Local component state would mean the core could not open a fan it does
   * not render, and the two would disagree about whether it is open.
   */
  fanOpen: boolean;
  /** Accepts a value or an updater, so a click handler can toggle without
   *  reading state it does not own. */
  setFanOpen: (open: boolean | ((previous: boolean) => boolean)) => void;
  /** Readouts that arrived but were not given screen space, newest first. */
  tray: { label: string; detail: string; at: number }[];
  /** Operations that were refused, with the reason — never silently dropped. */
  rejected: { op: string; reason: string; at: number }[];
  pushTray: (entry: { label: string; detail: string; at: number }) => void;
  openSurface: (request: OpenRequest) => string;
  closeSurface: (id: string) => void;
  focusSurface: (id: string) => void;
  moveSurface: (id: string, x: number, y: number) => void;
  resizeSurface: (id: string, w: number, h: number) => void;
  dockSurface: (id: string, side: DockSide) => void;
  minimizeSurface: (id: string) => void;
  restoreSurface: (id: string) => void;
  maximizeSurface: (id: string) => void;
  unmaximizeSurface: (id: string) => void;
  hideSurface: (id: string) => void;
  showSurface: (id: string) => void;
  togglePin: (id: string) => void;
  renameSurface: (id: string, title: string) => void;
  /** Full-HUD mode: chrome recedes to the edges and the room owns the screen. */
  setImmersive: (on: boolean) => void;
  toggleImmersive: () => void;
  setViewport: (viewport: Viewport) => void;
  applyOps: (ops: unknown) => { applied: number; rejected: number };
  resetLayout: () => void;
  persist: () => void;
}

function newId(kind: SurfaceKind): string {
  return `${kind}_${Math.random().toString(36).slice(2, 8)}${Date.now().toString(36).slice(-3)}`;
}

function dockGeometry(side: DockSide, viewport: Viewport, existing: Geometry): Geometry {
  const { w, h } = viewport;
  const half = Math.round(w / 2);
  switch (side) {
    case "left":
      return { x: 0, y: 0, w: half, h, z: existing.z };
    case "right":
      return { x: half, y: 0, w: w - half, h, z: existing.z };
    case "bottom":
      return { x: 0, y: Math.round(h * 0.6), w, h: Math.round(h * 0.4), z: existing.z };
    default:
      return existing;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const KINDS: SurfaceKind[] = [
  "chat",
  "mission",
  "model",
  "worker",
  "evidence",
  "memory",
  "files",
  "ide",
  "terminal",
  "diff",
  "logs",
  "telemetry",
  "computer",
  "media",
  "diagnostics",
];

/** An operation from outside the app is untrusted input: it may name a surface
 * that does not exist or a kind nothing renders. Refusing is the default. */
export function validateOp(value: unknown): { ok: true; op: WorkspaceOp } | { ok: false; reason: string } {
  if (!isRecord(value) || typeof value.op !== "string") return { ok: false, reason: "not an operation object" };
  const id = typeof value.id === "string" ? value.id : "";
  const num = (v: unknown, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

  switch (value.op) {
    case "open": {
      if (!isRecord(value.surface)) return { ok: false, reason: "open needs a surface" };
      const kind = value.surface.kind;
      if (typeof kind !== "string" || !KINDS.includes(kind as SurfaceKind)) {
        return { ok: false, reason: `unknown surface kind ${String(kind)}` };
      }
      return { ok: true, op: { op: "open", surface: value.surface as unknown as OpenRequest } };
    }
    case "close":
    case "focus":
    case "minimize":
    case "maximize":
    case "unmaximize":
    case "hide":
    case "show":
    case "restore":
      return id ? { ok: true, op: { op: value.op, id } } : { ok: false, reason: "no surface id" };
    case "move":
      return id ? { ok: true, op: { op: "move", id, x: num(value.x, 0), y: num(value.y, 0) } } : { ok: false, reason: "no surface id" };
    case "resize":
      return id ? { ok: true, op: { op: "resize", id, w: num(value.w, MIN_W), h: num(value.h, MIN_H) } } : { ok: false, reason: "no surface id" };
    case "dock": {
      const side = value.side;
      if (side !== "left" && side !== "right" && side !== "bottom" && side !== "none") {
        return { ok: false, reason: `unknown dock side ${String(side)}` };
      }
      return id ? { ok: true, op: { op: "dock", id, side } } : { ok: false, reason: "no surface id" };
    }
    case "pin":
      return id && typeof value.pinned === "boolean"
        ? { ok: true, op: { op: "pin", id, pinned: value.pinned } }
        : { ok: false, reason: "pin needs an id and a boolean" };
    case "rename":
      return id && typeof value.title === "string"
        ? { ok: true, op: { op: "rename", id, title: value.title.slice(0, TITLE_MAX) } }
        : { ok: false, reason: "rename needs an id and a title" };
    default:
      return { ok: false, reason: `unsupported operation ${value.op}` };
  }
}

function storedLayout(): { surfaces: Record<string, Surface>; order: string[] } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.version !== LAYOUT_VERSION) return null;
    const surfaces = isRecord(parsed.surfaces) ? (parsed.surfaces as Record<string, Surface>) : {};
    const order = Array.isArray(parsed.order) ? (parsed.order as string[]).filter((id) => id in surfaces) : [];
    return Object.keys(surfaces).length ? { surfaces, order } : null;
  } catch {
    return null;
  }
}

function initial(): Pick<WorkspaceState, "surfaces" | "order" | "zTop"> {
  const stored = storedLayout();
  if (!stored) return { surfaces: {}, order: [], zTop: 10 };
  const zTop = Object.values(stored.surfaces).reduce((max, surface) => Math.max(max, surface.geometry.z), 10);
  const order = stored.order.filter((id) => stored.surfaces[id]?.persistent);
  return { surfaces: Object.fromEntries(order.map((id) => [id, stored.surfaces[id]])), order, zTop };
}

export const useWorkspace = create<WorkspaceState>((set, get) => ({
  ...initial(),
  focusedId: null,
  immersive: false,
  viewport: { w: 1280, h: 800 },
  orbPlacement: null,
  fanOpen: false,
  tray: [],
  rejected: [],

  pushTray: (entry) => set({ tray: [entry, ...get().tray].slice(0, 24) }),

  openSurface: (request) => {
    const state = get();
    const kind = request.kind;
    // One surface per non-chat kind is reused, so the workspace does not fill
    // with duplicate mission panels as events arrive.
    if (kind !== "chat") {
      const existingId = state.order.find((id) => state.surfaces[id]?.kind === kind && state.surfaces[id]?.source.ref === request.source?.ref);
      if (existingId) {
        get().focusSurface(existingId);
        return existingId;
      }
    }

    const id = newId(kind);
    const now = Date.now();
    const size = defaultSize(kind, state.viewport);
    const step = (state.order.length % 8) * CASCADE_STEP;
    const surface: Surface = {
      id,
      kind,
      title: (request.title ?? KIND_TITLES[kind]).slice(0, TITLE_MAX) || KIND_TITLES[kind],
      geometry: { x: 24 + step, y: 24 + step, w: size.w, h: size.h, z: state.zTop + 1 },
      visible: true,
      focused: true,
      dock: "none",
      lifecycle: "active",
      source: request.source ?? { kind: "user" },
      permissions: { read: true, act: request.act === true, scope: request.scope },
      pinned: false,
      persistent: request.temporary !== true,
      createdAt: now,
      updatedAt: now,
    };

    set({
      surfaces: { ...state.surfaces, [id]: surface },
      order: [...state.order, id],
      zTop: state.zTop + 1,
      focusedId: id,
    });
    get().persist();
    return id;
  },

  closeSurface: (id) => {
    const state = get();
    if (!state.surfaces[id]) return;
    const surfaces = { ...state.surfaces };
    delete surfaces[id];
    set({
      surfaces,
      order: state.order.filter((other) => other !== id),
      focusedId: state.focusedId === id ? (state.order.filter((other) => other !== id).slice(-1)[0] ?? null) : state.focusedId,
    });
    get().persist();
  },

  focusSurface: (id) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    const zTop = state.zTop + 1;
    set({
      zTop,
      focusedId: id,
      surfaces: {
        ...state.surfaces,
        [id]: { ...surface, geometry: { ...surface.geometry, z: zTop }, focused: true, visible: true, lifecycle: "active", updatedAt: Date.now() },
      },
    });
  },

  moveSurface: (id, x, y) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    set({
      surfaces: {
        ...state.surfaces,
        [id]: {
          ...surface,
          geometry: {
            ...surface.geometry,
            x: clamp(Math.round(x), -surface.geometry.w + 80, state.viewport.w - 80),
            y: clamp(Math.round(y), 0, Math.max(0, state.viewport.h - 48)),
          },
          dock: "none",
          updatedAt: Date.now(),
        },
      },
    });
    get().persist();
  },

  resizeSurface: (id, w, h) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    set({
      surfaces: {
        ...state.surfaces,
        [id]: {
          ...surface,
          geometry: {
            ...surface.geometry,
            w: clamp(Math.round(w), MIN_W, state.viewport.w),
            h: clamp(Math.round(h), MIN_H, state.viewport.h),
          },
          updatedAt: Date.now(),
        },
      },
    });
    get().persist();
  },

  dockSurface: (id, side) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    const geometry = dockGeometry(side, state.viewport, surface.geometry);
    set({
      surfaces: { ...state.surfaces, [id]: { ...surface, geometry, dock: side, visible: true, lifecycle: "active", updatedAt: Date.now() } },
    });
    get().persist();
  },

  minimizeSurface: (id) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    set({
      surfaces: { ...state.surfaces, [id]: { ...surface, visible: false, focused: false, lifecycle: "minimized", updatedAt: Date.now() } },
      focusedId: state.focusedId === id ? null : state.focusedId,
    });
    get().persist();
  },

  restoreSurface: (id) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    const zTop = state.zTop + 1;
    set({
      zTop,
      focusedId: id,
      surfaces: {
        ...state.surfaces,
        [id]: { ...surface, visible: true, focused: true, lifecycle: "active", geometry: { ...surface.geometry, z: zTop }, updatedAt: Date.now() },
      },
    });
  },

  /** Fill the stage, remembering exactly where the surface came from. */
  maximizeSurface: (id) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface || surface.restoreGeometry) return;
    const geometry: Geometry = {
      x: 0,
      y: 0,
      w: Math.max(MIN_W, state.viewport.w),
      h: Math.max(MIN_H, state.viewport.h),
      z: state.zTop + 1,
    };
    set({
      zTop: geometry.z,
      focusedId: id,
      surfaces: {
        ...state.surfaces,
        [id]: {
          ...surface,
          restoreGeometry: surface.geometry,
          geometry,
          dock: "none",
          visible: true,
          focused: true,
          lifecycle: "active",
          updatedAt: Date.now(),
        },
      },
    });
    get().persist();
  },

  unmaximizeSurface: (id) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface?.restoreGeometry) return;
    set({
      surfaces: {
        ...state.surfaces,
        [id]: { ...surface, geometry: surface.restoreGeometry, restoreGeometry: undefined, updatedAt: Date.now() },
      },
    });
    get().persist();
  },

  /** Hidden, not closed: its data and identity survive, it just takes no space. */
  hideSurface: (id) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    set({
      surfaces: { ...state.surfaces, [id]: { ...surface, visible: false, focused: false, lifecycle: "background", updatedAt: Date.now() } },
      focusedId: state.focusedId === id ? null : state.focusedId,
    });
    get().persist();
  },

  showSurface: (id) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface || surface.visible) return;
    const z = state.zTop + 1;
    set({
      zTop: z,
      focusedId: id,
      surfaces: { ...state.surfaces, [id]: { ...surface, visible: true, focused: true, lifecycle: "active", geometry: { ...surface.geometry, z }, updatedAt: Date.now() } },
    });
  },

  togglePin: (id) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    set({ surfaces: { ...state.surfaces, [id]: { ...surface, pinned: !surface.pinned, updatedAt: Date.now() } } });
    get().persist();
  },

  renameSurface: (id, title) => {
    const state = get();
    const surface = state.surfaces[id];
    if (!surface) return;
    set({ surfaces: { ...state.surfaces, [id]: { ...surface, title: title.slice(0, TITLE_MAX), updatedAt: Date.now() } } });
    get().persist();
  },

  setImmersive: (on) => set({ immersive: on }),
  toggleImmersive: () => set({ immersive: !get().immersive }),
  setViewport: (viewport) => set({ viewport }),
  setOrbPlacement: (orbPlacement) => set({ orbPlacement }),
  setFanOpen: (fanOpen) =>
    set((state) => ({ fanOpen: typeof fanOpen === "function" ? fanOpen(state.fanOpen) : fanOpen })),

  /**
   * Apply agent- or shell-supplied operations. Ids are resolved against the state
   * at the start of the batch, so a list cannot name a surface an earlier entry
   * creates: send the open, then the arrangement.
   */
  applyOps: (ops) => {
    const list = Array.isArray(ops) ? ops : [ops];
    let applied = 0;
    let rejected = 0;
    const refusals: { op: string; reason: string; at: number }[] = [];
    for (const candidate of list) {
      const checked = validateOp(candidate);
      if (!checked.ok) {
        rejected += 1;
        refusals.push({ op: isRecord(candidate) ? String(candidate.op) : "?", reason: checked.reason, at: Date.now() });
        continue;
      }
      applyOne(get(), set, checked.op);
      applied += 1;
    }
    if (refusals.length) set({ rejected: [...get().rejected, ...refusals].slice(-20) });
    get().persist();
    return { applied, rejected };
  },

  resetLayout: () => {
    const state = get();
    const kept = Object.fromEntries(Object.entries(state.surfaces).filter(([, surface]) => surface.pinned));
    set({ surfaces: kept, order: Object.keys(kept), focusedId: null, zTop: 10 });
    get().persist();
  },

  persist: () => {
    const { surfaces, order } = get();
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          version: LAYOUT_VERSION,
          order: order.filter((id) => surfaces[id]?.persistent),
          surfaces: Object.fromEntries(order.filter((id) => surfaces[id]?.persistent).map((id) => [id, surfaces[id]])),
        }),
      );
    } catch {
      // A full or unavailable storage costs a layout, not a session.
    }
  },
}));

function applyOne(state: WorkspaceState, set: (partial: Partial<WorkspaceState>) => void, op: WorkspaceOp): void {
  switch (op.op) {
    case "open":
      state.openSurface(op.surface);
      return;
    case "close":
      state.closeSurface(op.id);
      return;
    case "focus":
      state.focusSurface(op.id);
      return;
    case "move":
      state.moveSurface(op.id, op.x, op.y);
      return;
    case "resize":
      state.resizeSurface(op.id, op.w, op.h);
      return;
    case "dock":
      state.dockSurface(op.id, op.side);
      return;
    case "minimize":
      state.minimizeSurface(op.id);
      return;
    case "maximize":
      state.maximizeSurface(op.id);
      return;
    case "unmaximize":
      state.unmaximizeSurface(op.id);
      return;
    case "hide":
      state.hideSurface(op.id);
      return;
    case "show":
      state.showSurface(op.id);
      return;
    case "restore":
      state.restoreSurface(op.id);
      return;
    case "pin": {
      const surface = state.surfaces[op.id];
      if (!surface) return;
      set({ surfaces: { ...state.surfaces, [op.id]: { ...surface, pinned: op.pinned, updatedAt: Date.now() } } });
      return;
    }
    case "rename":
      state.renameSurface(op.id, op.title);
      return;
  }
}

export function visibleSurfaces(state: { surfaces: Record<string, Surface>; order: string[] }): Surface[] {
  return state.order
    .map((id) => state.surfaces[id])
    .filter((surface): surface is Surface => Boolean(surface) && surface.visible)
    .sort((a, b) => a.geometry.z - b.geometry.z);
}

export function minimizedSurfaces(state: { surfaces: Record<string, Surface>; order: string[] }): Surface[] {
  return state.order
    .map((id) => state.surfaces[id])
    .filter((surface): surface is Surface => Boolean(surface) && surface.lifecycle === "minimized");
}

/** Everything out of sight but still alive: minimised or hidden, both restorable. */
export function stowedSurfaces(state: { surfaces: Record<string, Surface>; order: string[] }): Surface[] {
  return state.order
    .map((id) => state.surfaces[id])
    .filter((surface): surface is Surface => Boolean(surface) && !surface.visible);
}
