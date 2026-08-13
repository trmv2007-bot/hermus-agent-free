"""Tests for Phase 4 — Eval harness, router, tool fallbacks, project memory,
trajectory tagging, plan resume. Offline (mock model)."""
import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from core.config import config

config.model = "mock/mock"


def test_eval_harness_runs_and_checks():
    from core.reasoning.eval import EvalHarness

    harness = EvalHarness()
    tasks = harness.load_tasks()
    assert len(tasks) >= 15, f"benchmark should have >=15 tasks, got {len(tasks)}"
    cats = harness.list_categories()
    assert set(cats) >= {"fact", "research", "code", "extraction", "math"}

    # check_task: substring
    r = harness.check_task({"checks": [{"type": "substring", "value": "paris"}]}, "The capital is Paris.")
    assert r["success"] and len(r["passed"]) == 1
    r2 = harness.check_task({"checks": [{"type": "substring", "value": "london"}]}, "Paris it is.")
    assert not r2["success"] and len(r2["failed"]) == 1
    # regex + not_substring
    r3 = harness.check_task({"checks": [{"type": "regex", "value": "\\b42\\b"}, {"type": "not_substring", "value": "error"}]}, "answer is 42")
    assert r3["success"]

    # run offline with mock (no council, no network strategies)
    res = harness.run(strategy="none", tasks=tasks[:3], limit=None, model="mock/mock", tag="test")
    assert res.get("runs") == 3
    assert "success_rate" in res and "by_category" in res
    assert len(res.get("results", [])) == 3
    # history persisted
    hist = harness.history(limit=5)
    assert any(h.get("tag") == "test" for h in hist)
    print("✅ Eval harness: tasks, checks, run, history all work")


def test_eval_compare():
    from core.reasoning.eval import EvalHarness

    harness = EvalHarness()
    res = harness.compare("none", "reflexion", tasks=harness.load_tasks()[:2], limit=None, model="mock/mock")
    assert res["winner"] in ("none", "reflexion", "tie")
    assert "a" in res and "b" in res and "success_rate" in res["a"]
    print(f"✅ Eval compare works: winner={res['winner']}")


def test_router_deterministic():
    from core.counsel.router import Router

    r = Router()
    assert r.classify("research the best free password manager") == "research"
    assert r.classify("write a python function to parse json") == "code"
    assert r.classify("hello") == "simple"
    # fleet strategies per mode
    assert r.fleet_strategy("research X and Y", mode="multi-chat") == "fanout"
    assert r.fleet_strategy("anything", mode="multi-agent") == "map"
    assert r.fleet_strategy("compare two things", mode="agent") in ("fanout", "map", "race", "auto")
    # route: council for hard long tasks (counsel enabled)
    rt = r.route(
        "Research and compare three free vector databases, then recommend one with a security review and deployment plan.",
        mode="agent",
        workers=2,
    )
    assert rt["decision"] in ("council", "single")
    # multi mode with workers -> fleet
    rt2 = r.route("Research the best free password manager", mode="multi-chat", workers=3)
    assert rt2["decision"] == "fleet"
    assert rt2["params"]["strategy"] == "fanout"
    print("✅ Router: classify/fleet_strategy/route deterministic")


def test_tool_fallback_chain():
    from core.tool_registry import tool_registry, TOOL_FALLBACK_CHAINS

    def boom(**kwargs):
        raise RuntimeError("simulated failure")

    def ok_tool(**kwargs):
        return {"ok": True, "got": kwargs}

    tool_registry.register("fake_fail", boom, {"type": "object", "properties": {}}, source="test")
    tool_registry.register("fake_ok", ok_tool, {"type": "object", "properties": {}}, source="test")
    old = TOOL_FALLBACK_CHAINS.get("fake_fail")
    TOOL_FALLBACK_CHAINS["fake_fail"] = [{"tool": "fake_ok"}]
    try:
        res = tool_registry.execute("fake_fail", {"x": 1})
        assert res.get("ok"), res
        assert res.get("fallback_trail"), "fallback trail should be attached"
        assert any("fake_ok" in t for t in res["fallback_trail"])
    finally:
        if old is None:
            TOOL_FALLBACK_CHAINS.pop("fake_fail", None)
        else:
            TOOL_FALLBACK_CHAINS["fake_fail"] = old
    # cleanup test tools
    tool_registry.executors.pop("fake_fail", None)
    tool_registry.executors.pop("fake_ok", None)
    print("✅ Tool fallback chain: error -> alternate tool -> trail attached")


def test_retry_fallback():
    from core.tool_registry import tool_registry, TOOL_FALLBACK_CHAINS

    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"error": "transient failure"}
        return {"ok": True}

    tool_registry.register("fake_flaky", flaky, {"type": "object", "properties": {}}, source="test")
    old = TOOL_FALLBACK_CHAINS.get("fake_flaky")
    TOOL_FALLBACK_CHAINS["fake_flaky"] = [{"retry": True}]
    try:
        res = tool_registry.execute("fake_flaky", {})
        assert res.get("ok")
        assert calls["n"] == 2
        assert res.get("fallback_trail")
    finally:
        if old is None:
            TOOL_FALLBACK_CHAINS.pop("fake_flaky", None)
        else:
            TOOL_FALLBACK_CHAINS["fake_flaky"] = old
    tool_registry.executors.pop("fake_flaky", None)
    print("✅ Retry fallback: transient error -> retry succeeds")


def test_project_scoped_memory():
    import uuid

    from core.memory import memory

    sid_a = f"proj_a_{uuid.uuid4().hex[:6]}"
    sid_b = f"proj_b_{uuid.uuid4().hex[:6]}"
    memory.add_session_message(sid_a, "user", "Alpha project: pricing plan discussion", project="alpha")
    memory.add_session_message(sid_b, "user", "Alpha project: unrelated note", project="beta")

    found_alpha = memory.search_sessions("pricing plan", limit=5, project="alpha")
    found_beta = memory.search_sessions("pricing plan", limit=5, project="beta")
    found_all = memory.search_sessions("pricing plan", limit=5)
    assert any(s["session_id"] == sid_a for s in found_alpha)
    assert not any(s["session_id"] == sid_a for s in found_beta)
    assert any(s["session_id"] == sid_a for s in found_all)
    print("✅ Project-scoped memory: project filter works")


def test_trajectory_tagging():
    import uuid

    from core.memory import memory
    from core.config import config

    sid = f"tag_{uuid.uuid4().hex[:6]}"
    memory.add_session_message(
        sid, "assistant", "tagged answer",
        tag={"strategy": "reflexion", "difficulty": 3, "council": False},
    )
    traj_path = config.resolve_path(config.trajectory_path)
    lines = [json.loads(l) for l in traj_path.read_text().splitlines() if l.strip()]
    tagged = [l for l in lines if l.get("session_id") == sid]
    assert tagged, "trajectory line should exist"
    assert tagged[-1].get("tag", {}).get("strategy") == "reflexion"
    print("✅ Trajectory tagging: strategy/difficulty attached to JSONL")


def test_plan_list_show_resume():
    import uuid

    from core.reasoning.scaffold import Plan, PlanStep, list_plans, resume_plan, show_plan

    sid = f"resume_{uuid.uuid4().hex[:6]}"
    plan = Plan(
        goal="test plan",
        session_id=sid,
        steps=[
            PlanStep(goal="step one", action="investigate", status="done"),
            PlanStep(goal="step two remaining", action="investigate", status="pending"),
        ],
        difficulty=3,
    )
    plan.save()
    plans = list_plans(limit=10)
    assert any(p["session_id"] == sid for p in plans), "plan should be listed"
    shown = show_plan(sid)
    assert shown and len(shown.steps) == 2
    res = resume_plan(sid, model="mock/mock")
    assert res.get("success"), res
    assert res.get("remaining_before") == 1
    assert res.get("response")
    print("✅ Plan persistence: list/show/resume all work (mock run)")


def test_agent_returns_strategy_and_plan():
    from core.agent import HermusAgent

    agent = HermusAgent(model="mock/mock", mode="agent")
    r = agent.chat("Explain in detail why python async is faster for io bound tasks with examples.")
    assert r.get("response")
    assert "strategy" in r
    assert r.get("strategy") in ("reflexion", "none")
    print(f"✅ Agent chat end-to-end with Phase 4 machinery: strategy={r.get('strategy')}")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n--- {name} ---")
            fn()
    print("\n🎉 All Phase 4 tests passed (offline, mock model)")
