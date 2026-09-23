// A request should arrange the room. These pin which asks produce which surfaces,
// and — just as important — which produce nothing.

import { describe, expect, it } from "vitest";
import { planForRequest, unbuiltKinds } from "./task-surfaces";

const kinds = (text: string) => planForRequest(text).surfaces.map((surface) => surface.kind);

describe("planForRequest", () => {
  it("brings evidence and workers when something failed", () => {
    const plan = planForRequest("check why this mission failed");

    expect(plan.intent).toBe("failure-triage");
    expect(kinds("check why this mission failed")).toEqual(["mission", "evidence", "worker"]);
    expect(plan.reason).toContain("went wrong");
  });

  it("lays out an engineering set for a fix", () => {
    expect(kinds("fix the failing login test")).toEqual(["ide", "terminal", "diff", "mission"]);
  });

  it("shows the model and key picture for a capability question", () => {
    expect(kinds("which model is handling this and does the key have quota")).toEqual(["model", "worker"]);
  });

  it("opens the computer view only when asked to look at the screen", () => {
    expect(kinds("show me what the computer agent currently sees")).toEqual(["computer"]);
  });

  it("does not arrange anything for a request it cannot place", () => {
    const plan = planForRequest("good morning");

    expect(plan.intent).toBe("general");
    expect(plan.surfaces).toHaveLength(1);
    expect(plan.surfaces[0].kind).toBe("chat");
    expect(plan.reason).toContain("no specific layout");
  });

  it("treats empty input as no request at all", () => {
    expect(planForRequest("   ").intent).toBe("general");
  });

  it("says which surfaces it cannot render yet", () => {
    const built = ["mission", "evidence", "worker", "chat"] as never;

    expect(unbuiltKinds(planForRequest("fix the test"), built)).toEqual(["ide", "terminal", "diff"]);
    expect(unbuiltKinds(planForRequest("why did it fail"), built)).toEqual([]);
  });

  it("is deterministic — the same ask always arranges the same way", () => {
    const first = planForRequest("debug the build");
    const second = planForRequest("DEBUG THE BUILD");

    expect(second.surfaces.map((s) => s.kind)).toEqual(first.surfaces.map((s) => s.kind));
  });
});
