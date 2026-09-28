// Runtime events become workspace operations here, in one pure function, so the
// rule "what reaches the user's screen" has a single answer.
//
// The workspace is deliberately not an event viewer that opens a panel per
// message: telemetry-class events land in the tray and only the few events a
// user can act on take a surface.

import type { WorkspaceOp } from "../state/workspace-store";
import { validateOp } from "../state/workspace-store";

export interface RuntimeEvent {
  type?: string;
  kind?: string;
  id?: string;
  ts?: number | string;
  data?: Record<string, unknown>;
}

export interface PlanResult {
  /** Operations to apply to the workspace. */
  ops: WorkspaceOp[];
  /** Tray entries worth remembering without taking screen space. */
  tray: { label: string; detail: string; at: number }[];
  /** Whether the event is worth pulling the room back out of full-HUD mode. */
  reveal: boolean;
}

const ACTIONABLE_MISSION_EVENTS = new Set(["mission_claim_disagreement", "mission_requirement_breach", "mission_repair_stopped"]);

function eventKind(event: RuntimeEvent): string {
  return String(event.type ?? event.kind ?? "");
}

/** A compact, honest one-liner for a runtime frame. */
export function digest(data: Record<string, unknown> | undefined): string {
  if (!data) return "";
  return Object.entries(data)
    .slice(0, 4)
    .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value).slice(0, 40) : String(value).slice(0, 40)}`)
    .join(" ");
}

function missionId(event: RuntimeEvent): string | undefined {
  const data = event.data ?? {};
  const value = data.mission_id ?? data.id ?? data.mission;
  return typeof value === "string" && value ? value : undefined;
}

export function planForEvent(event: RuntimeEvent): PlanResult {
  const kind = eventKind(event);
  const data = event.data ?? {};
  const at = Date.now();

  if (!kind) return { ops: [], tray: [], reveal: false };

  if (kind.startsWith("agent.") || kind === "fleet.state_changed" || kind === "agent_updated") {
    const name = String((data as Record<string, unknown>).name ?? (data as Record<string, unknown>).agent_id ?? "worker");
    return {
      ops: [{ op: "open", surface: { kind: "worker", title: `Worker · ${name}`, source: { kind: "event", ref: kind } } }],
      tray: [{ label: kind, detail: name, at }],
      // Roster churn is not something the user asked to watch.
      reveal: false,
    };
  }

  if (ACTIONABLE_MISSION_EVENTS.has(kind)) {
    const id = missionId(event) ?? "unknown";
    return {
      ops: [
        { op: "open", surface: { kind: "mission", title: `Mission · ${id.slice(0, 12)}`, source: { kind: "event", ref: id } } },
        { op: "open", surface: { kind: "evidence", title: `Evidence · ${id.slice(0, 12)}`, source: { kind: "event", ref: id } } },
      ],
      tray: [{ label: kind, detail: id, at }],
      // Something went wrong that the user can act on: show the space.
      reveal: true,
    };
  }

  if (kind === "mission_finished" || kind === "mission_opened" || kind === "mission_state" || kind === "mission_verification") {
    const id = missionId(event) ?? "unknown";
    return {
      ops: [{ op: "open", surface: { kind: "mission", title: `Mission · ${id.slice(0, 12)}`, source: { kind: "event", ref: id } } }],
      tray: [{ label: kind, detail: String((data as Record<string, unknown>).state ?? ""), at }],
      reveal: kind === "mission_finished" || kind === "mission_opened",
    };
  }

  if (kind.startsWith("emergency_stop") || kind === "computer.emergency_stop") {
    return {
      ops: [{ op: "open", surface: { kind: "computer", title: "Computer view · halted", source: { kind: "event", ref: kind }, act: false } }],
      tray: [{ label: "emergency stop", detail: "the computer control path stopped", at }],
      reveal: true,
    };
  }

  // The agent asking the room to do something. These ops are already
  // server-validated by core/workspace_ops.py, but they still go through
  // applyOps -> validateOp on this side: the client is not a trust boundary
  // for its own state, and a stale id (the surface closed a moment ago) has to
  // be refused here where the refusal is visible, not thrown on the floor.
  if (kind === "workspace_op") {
    const payload = (data?.args_redacted ?? data ?? {}) as Record<string, unknown>;
    const raw = (payload.ops ?? []) as unknown;
    const list = Array.isArray(raw) ? raw : [];
    const accepted = list.filter((op): op is WorkspaceOp => validateOp(op).ok);
    return {
      ops: accepted,
      tray: [
        {
          label: "workspace op",
          detail: accepted.length
            ? accepted.map((op) => op.op).join(", ")
            : `refused: ${list.length} op(s) did not pass client validation`,
          at,
        },
      ],
      reveal: false,
    };
  }

  // An unsolicited line from the ambient loop. It is not a tray entry and not
  // a surface: it is a thing the assistant said, and the transcript is where a
  // thing the assistant said belongs. Falling through to the catch-all would
  // file it as telemetry, which is exactly the "unreadable heartbeat" failure
  // the ambient loop exists to avoid — and `reveal: false` is deliberate, since
  // yanking the user out of full-HUD for a quiet note is worse than the note.
  if (kind === "hermus_spoke") {
    return { ops: [], tray: [], reveal: false };
  }

  // Context, tool selection and step telemetry are readouts, not requests.
  return { ops: [], tray: [{ label: kind, detail: "", at }], reveal: false };
}
