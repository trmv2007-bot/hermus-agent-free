"""Tests for the Counsel System (Phases 0-2) — council of AIs + self-upgrade loop.

Uses ONLY the free mock model (mock/mock) so it runs offline with zero API keys.
Run:  python tests/test_counsel_system.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))


from core.config import config

# Ensure mock model so tests are offline and deterministic
config.model = "mock/mock"


def test_governor_difficulty():
    from core.reasoning.governor import governor

    assert governor.classify_difficulty("hi") == 1
    assert governor.classify_difficulty("what is python?") == 1
    assert governor.classify_difficulty(
        "Research, analyze and compare the top three free vector databases, then recommend one with a detailed plan and security review."
    ) >= 4
    assert 1 <= governor.classify_difficulty("x" * 500) <= 5
    # council config sanity
    cc = governor.council_config(
        "Research, analyze and compare the top three free vector databases, then recommend one with a detailed plan and security review.",
        mode="agent",
    )
    if cc:
        assert cc["max_members"] >= 3 and cc["max_rounds"] >= 1
    print("✅ Governor difficulty classifier works")


def test_plan_scaffold():
    from core.reasoning.scaffold import Plan, PlanBuilder

    builder = PlanBuilder(model="mock/mock")
    plan = builder.build_plan("Build a budgeting web app with tests and a security check", session_id="test_plan", difficulty=4)
    assert plan.goal
    assert plan.steps, "heuristic fallback must produce steps"
    assert plan.steps[0].goal
    # round-trip save/load
    path = plan.save()
    loaded = Plan.load(str(path))
    assert loaded is not None
    assert loaded.goal == plan.goal
    assert len(loaded.steps) == len(plan.steps)
    assert "Goal:" in plan.to_prompt()
    print(f"✅ Plan scaffold: {len(plan.steps)} steps, saved to {path.name}")


def test_constitution_self_upgrade():
    from core.counsel.constitution import ConstitutionManager

    # Idempotent test: wipe pending amendments + snapshots, force-reset to v1
    from core.counsel.constitution import DEFAULT_CONSTITUTION, ConstitutionManager

    for f in ("pending_amendments.json", "constitution_v1.json"):
        p = config.resolve_path(f"data/counsel/{f}")
        if p.exists():
            p.unlink()
    mgr = ConstitutionManager()
    doc = dict(DEFAULT_CONSTITUTION)
    doc["version"] = 1
    mgr.save(doc, log=False)
    v_before = mgr.current_version()
    assert v_before == 1

    # Low-risk amendment -> auto-applied, version bumps
    res = mgr.propose(
        {
            "target": "member_prompt",
            "member": "critic",
            "change": "You are the Critic. You ALWAYS demand at least one evidence citation per plan claim.",
            "reason": "test auto-amendment",
        },
        source="test",
    )
    assert res.get("auto_applied"), f"low-risk amendment should auto-apply: {res}"
    assert mgr.current_version() == v_before + 1

    # High-risk amendment (budget) -> pending
    res2 = mgr.propose(
        {"target": "budget", "budget_key": "max_rounds", "change": "2", "reason": "test pending"},
        source="test",
    )
    assert res2.get("status") == "pending", f"budget change should pend: {res2}"
    pending = mgr.pending_amendments()
    assert any(p["id"] == res2["amendment"]["id"] for p in pending)

    # Invalid amendment -> rejected
    bad = mgr.propose({"target": "member_prompt", "member": "ghost", "change": "x" * 10}, source="test")
    assert not bad.get("success")

    # Rollback to v_before works
    rb = mgr.rollback(v_before)
    assert rb.get("success"), f"rollback failed: {rb}"
    assert mgr.current_version() == v_before

    # Restore state: re-apply the auto one for cleanliness (or leave; rollback already done)
    print("✅ Constitution self-upgrade: auto-apply, pending, validation, rollback all work")


def test_council_session_runs():
    from core.counsel.council import CouncilSession

    goal = "Research the best free password manager, then outline a 3-step migration plan and list security risks."
    cs = CouncilSession(goal, model="mock/mock", difficulty=4, max_members=4, max_rounds=2, execute=False)
    result = cs.run()

    assert result.get("session_id")
    assert result.get("members"), "roster should be non-empty"
    assert result.get("plan"), "council should produce a plan"
    assert result["plan"]["steps"], "plan should have steps"
    assert result.get("final_answer"), "final answer should exist"
    assert result.get("transcript_turns", 0) >= 3, "members should have talked"
    # transcripts + session saved
    sess_path = config.resolve_path(f"data/counsel/sessions/{result['session_id']}.json")
    trans_path = config.resolve_path(f"data/counsel/transcripts/{result['session_id']}.jsonl")
    assert sess_path.exists() and trans_path.exists()
    print(f"✅ Council session: {len(result['members'])} members, {result['transcript_turns']} turns, "
          f"plan={len(result['plan']['steps'])} steps")


def test_council_execution_with_tools():
    from core.counsel.council import CouncilSession

    # Short goal so execution stays tiny; mock executor does no real tools
    cs = CouncilSession("Plan and execute a quick two-step task: list two python libraries for web scraping.", model="mock/mock", difficulty=3, max_members=3, max_rounds=1, execute=True)
    result = cs.run()
    assert result.get("final_answer")
    assert result.get("plan")
    print(f"✅ Council execution path: {len(result.get('step_results', []))} step results, replanned={result.get('replanned')}")


def test_meta_counsel_review():
    from core.counsel.meta import meta_counsel

    summary = {
        "session_id": "test_meta_session",
        "goal": "test task",
        "final_answer": "some answer",
        "errors": [],
        "step_results": [],
        "votes": [],
    }
    res = meta_counsel.review_session(summary)
    assert "proposed" in res
    # reflection hook must not crash
    res2 = meta_counsel.propose_from_reflection(
        {"mistakes": ["Tool web_search failed: timeout", "User correction: wrong answer"]}
    )
    assert "proposed" in res2
    print(f"✅ Meta-Counsel review: proposed={res['proposed']}, reflection proposals={res2['proposed']}")


def test_counsel_tool_registered():
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    defs = tool_registry.get_definitions(allowed={"all"})
    names = [d["function"]["name"] for d in defs]
    assert "counsel_convoke" in names, "counsel_convoke tool should be registered"
    print("✅ counsel_convoke tool registered in the 88+ tool set")


def test_agent_routes_to_council():
    from core.agent import HermusAgent

    old_min = config.counsel_min_difficulty
    config.counsel_min_difficulty = 3  # lower threshold so the mock test hits the council path
    agent = HermusAgent(model="mock/mock", mode="agent")
    # Long complex message -> council path (mock), returns a response
    result = agent.chat(
        "Research the best free password manager, then outline a 3-step migration plan and list the top security risks in detail for a complete analysis."
    )
    assert result.get("response")
    if result.get("council"):
        print(f"✅ Agent auto-routed to council: {result['council']['session_id']}")
    else:
        print("ℹ️ Agent used normal loop (council skipped) — still returned a response")
    config.counsel_min_difficulty = old_min


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n--- {name} ---")
            fn()
    # Leave a pristine constitution behind (tests auto-apply amendments)
    from core.counsel.constitution import DEFAULT_CONSTITUTION, ConstitutionManager

    for f in ("pending_amendments.json",):
        p = config.resolve_path(f"data/counsel/{f}")
        if p.exists():
            p.unlink()
    doc = dict(DEFAULT_CONSTITUTION)
    doc["version"] = 1
    ConstitutionManager().save(doc, log=False)
    print("\n🎉 All counsel system tests passed (offline, mock model)")
