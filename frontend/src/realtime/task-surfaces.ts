// The workspace arranging itself around a request.
//
// "Check why this mission failed" should not require opening six panels by hand.
// This maps what was asked to the set of surfaces that answers it, and where they
// go, so a request arrives with the room already arranged.

import type { OpenRequest } from "../state/workspace-store";
import type { DockSide, SurfaceKind } from "../state/surfaces";

export interface SurfacePlan {
  surfaces: Array<OpenRequest & { dock?: DockSide }>;
  /** Why this set was chosen — shown to the user, because a guess should be legible. */
  reason: string;
  intent: TaskIntent;
}

export type TaskIntent = "failure-triage" | "engineering" | "observation" | "model-usage" | "general";

const RULES: Array<{ intent: TaskIntent; test: RegExp; surfaces: Array<OpenRequest & { dock?: DockSide }>; reason: string }> = [
  {
    intent: "failure-triage",
    test: /\b(why|fail|failed|failure|broken|stuck|error|wrong|regress|didn'?t work|not working)\b/i,
    reason: "you asked what went wrong, so the mission, its evidence and the worker that ran it are shown together",
    surfaces: [
      { kind: "mission", title: "Mission", source: { kind: "agent", ref: "task" }, dock: "left" },
      { kind: "evidence", title: "Evidence", source: { kind: "agent", ref: "task" }, dock: "right" },
      { kind: "worker", title: "Workers", source: { kind: "agent", ref: "task" }, dock: "bottom" },
    ],
  },
  {
    intent: "engineering",
    test: /\b(fix|patch|code|edit|refactor|test|tests|build|implement|debug|commit|diff|merge)\b/i,
    reason: "this is engineering work, so the editor, terminal and change set are laid out side by side",
    surfaces: [
      { kind: "ide", title: "Editor", source: { kind: "agent", ref: "workspace" }, dock: "left" },
      { kind: "terminal", title: "Terminal", source: { kind: "agent", ref: "workspace" }, dock: "bottom" },
      { kind: "diff", title: "Changes", source: { kind: "agent", ref: "workspace" }, dock: "right" },
      { kind: "mission", title: "Mission", source: { kind: "agent", ref: "task" } },
    ],
  },
  {
    intent: "observation",
    test: /\b(screen|computer|watch|observe|look at|show me what|display|desktop)\b/i,
    reason: "you asked to see what the computer agent sees",
    surfaces: [{ kind: "computer", title: "Computer view", source: { kind: "agent", ref: "computer" }, dock: "right" }],
  },
  {
    intent: "model-usage",
    test: /\b(model|models|provider|key|keys|quota|rate limit|token|which ai|cost)\b/i,
    reason: "this is about which model is doing the work and what it can spend",
    surfaces: [
      { kind: "model", title: "Model centre", source: { kind: "agent", ref: "models" }, dock: "left" },
      { kind: "worker", title: "Workers", source: { kind: "agent", ref: "roster" }, dock: "right" },
    ],
  },
];

const GENERAL: SurfacePlan = {
  intent: "general",
  reason: "no specific layout fits this request, so the conversation stays primary",
  surfaces: [{ kind: "chat", title: "Conversation", source: { kind: "user", ref: "command" } }],
};

/**
 * Choose the surfaces a request implies. Deliberately conservative: an unclear
 * ask gets the chat surface rather than a room full of panels nobody wanted.
 */
export function planForRequest(text: string): SurfacePlan {
  const utterance = String(text || "").trim();
  if (!utterance) return GENERAL;
  for (const rule of RULES) {
    if (rule.test.test(utterance)) return { intent: rule.intent, reason: rule.reason, surfaces: rule.surfaces };
  }
  return GENERAL;
}

/** Kinds in this plan that no renderer exists for yet, so the shell can say so. */
export function unbuiltKinds(plan: SurfacePlan, available: Iterable<SurfaceKind>): string[] {
  const built = new Set(available);
  return plan.surfaces.filter((surface) => !built.has(surface.kind)).map((surface) => surface.kind);
}
