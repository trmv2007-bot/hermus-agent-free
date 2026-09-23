// Which runtime events are allowed to take over the screen. The default answer
// is "almost none" — the workspace is opt-in and stays calm until something the
// user can act on happens.

import { describe, expect, it } from "vitest";
import { digest, planForEvent } from "./events";

describe("event → workspace planning", () => {
  it("opens a mission surface when a mission finishes, and reveals the space", () => {
    const plan = planForEvent({ type: "mission_finished", data: { mission_id: "msn_1", state: "completed" } });

    expect(plan.reveal).toBe(true);
    expect(plan.ops).toHaveLength(1);
    expect(plan.ops[0]).toMatchObject({ op: "open", surface: { kind: "mission" } });
  });

  it("brings evidence into view when the claim and the verdict disagree", () => {
    const plan = planForEvent({ type: "mission_claim_disagreement", data: { mission_id: "msn_1", disagreements: ["claimed_complete_but_unverified"] } });
    const kinds = plan.ops.flatMap((op) => (op.op === "open" ? [op.surface.kind] : []));

    expect(plan.reveal).toBe(true);
    expect(kinds).toEqual(["mission", "evidence"]);
  });

  it("treats a repair round that ran out of strategies as actionable", () => {
    const plan = planForEvent({ type: "mission_repair_stopped", data: { mission_id: "msn_1", reason: "no distinct recovery action left" } });

    expect(plan.reveal).toBe(true);
    expect(plan.ops.length).toBeGreaterThan(0);
  });

  it("does not open a panel for roster churn", () => {
    const plan = planForEvent({ kind: "agent.spawned", data: { name: "Friday" } });

    expect(plan.reveal).toBe(false);
    expect(plan.ops[0]).toMatchObject({ op: "open", surface: { kind: "worker" } });
    expect(plan.tray[0].label).toBe("agent.spawned");
  });

  it("keeps context and tool telemetry out of the layout", () => {
    for (const kind of ["context_assembled", "tools_selected", "memory_recalled", "step_started"]) {
      const plan = planForEvent({ type: kind, data: {} });
      expect(plan.ops, kind).toEqual([]);
      expect(plan.reveal, kind).toBe(false);
      expect(plan.tray, kind).toHaveLength(1);
    }
  });

  it("surfaces an emergency stop, and does not let the surface act", () => {
    const plan = planForEvent({ type: "emergency_stop", data: {} });

    expect(plan.reveal).toBe(true);
    expect(plan.ops[0]).toMatchObject({ op: "open", surface: { kind: "computer", act: false } });
  });

  it("ignores a frame it cannot read", () => {
    expect(planForEvent({})).toEqual({ ops: [], tray: [], reveal: false });
  });
});

describe("digest", () => {
  it("renders the fewest fields that still identify a frame", () => {
    expect(digest({ mission_id: "msn_1", state: "failed" })).toBe("mission_id=msn_1 state=failed");
    expect(digest({})).toBe("");
    expect(digest(undefined)).toBe("");
  });

  it("keeps nested payloads short instead of dumping them", () => {
    const line = digest({ data: { long: "y".repeat(400) } });

    expect(line.length).toBeLessThan(80);
    expect(line).toContain("data=");
  });

  it("caps how many fields one row shows", () => {
    const line = digest({ a: 1, b: 2, c: 3, d: 4, e: 5, f: 6 });

    expect(line.split(" ")).toHaveLength(4);
    expect(line).not.toContain("f=");
  });
});
