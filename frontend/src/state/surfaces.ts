// The vocabulary of the workspace: what a surface is, and what may be true of it.
//
// Everything the UI can show is a surface with identity, geometry, a source and a
// lifecycle. Keeping that in one place is what lets the agent open and arrange
// views through typed operations instead of the app scattering window state
// across whichever component happened to need a panel.

export type SurfaceKind =
  | "chat"
  | "mission"
  | "model"
  | "worker"
  | "evidence"
  | "memory"
  | "files"
  | "ide"
  | "terminal"
  | "diff"
  | "logs"
  | "telemetry"
  | "computer"
  | "media"
  | "diagnostics";

export type SurfaceLifecycle = "active" | "background" | "minimized" | "closed";

export type DockSide = "left" | "right" | "bottom" | "none";

/** Where a surface's data comes from. A surface with no source is a mock, and
 * the shell says so rather than rendering an empty panel that looks alive. */
export interface SurfaceSource {
  kind: "api" | "event" | "agent" | "user";
  /** Endpoint, event stream or mission id the surface is bound to. */
  ref?: string;
}

export interface SurfacePermissions {
  read: boolean;
  /** Whether this surface may issue actions, or only observe. */
  act: boolean;
  /** Optional mission or agent the surface is scoped to. */
  scope?: string;
}

export interface Geometry {
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
}

export interface Surface {
  id: string;
  kind: SurfaceKind;
  title: string;
  geometry: Geometry;
  /** Present only while maximised: where this surface sat before, so undoing it is exact. */
  restoreGeometry?: Geometry;
  visible: boolean;
  focused: boolean;
  dock: DockSide;
  lifecycle: SurfaceLifecycle;
  source: SurfaceSource;
  permissions: SurfacePermissions;
  /** Pinned surfaces survive a layout reset; temporary ones do not. */
  pinned: boolean;
  persistent: boolean;
  createdAt: number;
  updatedAt: number;
}

/** The region the room may occupy: the stage with the dock rail subtracted,
 * measured from the shell rather than assumed from the window. */
export interface Viewport {
  w: number;
  h: number;
}

/**
 * Where you are looking in the room.
 *
 * `panX`/`panY` are screen pixels applied before the scale; `zoom` scales the
 * world layer. Surfaces keep real world coordinates, so nothing inside a surface
 * has to know about this — the transform lives entirely on the stage.
 */
export interface StageView {
  panX: number;
  panY: number;
  zoom: number;
}

export const ZOOM_MIN = 0.35;
export const ZOOM_MAX = 2.2;

/**
 * Zoom limits.
 *
 * Below ~0.35 a panel is smaller than its own controls, and above ~2.2 a single
 * panel swallows the whole room — past the point where panning is useful.
 */
export function clampZoom(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
}

/** The room's snap grid. Arrow keys move by exactly this, and the floor's
 *  major lines land on it, so a snapped panel edge sits on a visible line. */
export const GRID_STEP = 24;

export const KIND_TITLES: Record<SurfaceKind, string> = {
  chat: "Conversation",
  mission: "Mission",
  model: "Model",
  worker: "Worker",
  evidence: "Evidence",
  memory: "Memory",
  files: "Files",
  ide: "Editor",
  terminal: "Terminal",
  diff: "Changes",
  logs: "Logs",
  telemetry: "Telemetry",
  computer: "Computer view",
  media: "Media",
  diagnostics: "Diagnostics",
};

/** New surfaces open in a cascade rather than exactly on top of each other. */
export const CASCADE_STEP = 28;
export const MIN_W = 320;
export const MIN_H = 200;

export function defaultSize(kind: SurfaceKind, viewport: Viewport): { w: number; h: number } {
  const w = kind === "chat" || kind === "ide" || kind === "computer" ? 0.46 : 0.34;
  const h = kind === "telemetry" || kind === "logs" ? 0.32 : 0.58;
  return { w: clamp(Math.round(viewport.w * w), MIN_W, viewport.w), h: clamp(Math.round(viewport.h * h), MIN_H, viewport.h) };
}

export function clamp(value: number, min: number, max: number): number {
  if (max < min) return min;
  return Math.min(Math.max(value, min), max);
}
