"""Tests for Phase 3 — DeepThink strategies + Lessons loop.

Offline: uses ONLY the free mock model (mock/mock); verification searches are
tolerated even if the network is unavailable (any failure falls back gracefully).
Run:  python tests/test_deepthink.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from core.config import config

config.model = "mock/mock"


def test_lessons_store_add_and_relevant():
    from core.reasoning.lessons import LessonsStore

    store = LessonsStore()
    # fresh table for determinism
    with store._conn() as conn:
        conn.execute("DELETE FROM lessons")
    r1 = store.add(
        "Tool web_search failed: timeout. Prefer a fallback path (web_read / browser_navigate).",
        category="tool_failure",
        keywords="web_search failed timeout fallback",
        source="test",
    )
    assert r1.get("success"), r1
    r2 = store.add(
        "User correction: 'that is not what I asked for'. Verify this area carefully before answering.",
        category="user_correction",
        keywords="correction verify carefully",
        source="test",
    )
    assert r2.get("success")
    # dedupe: same lesson again -> duplicate
    r3 = store.add(
        "Tool web_search failed: timeout. Prefer a fallback path (web_read / browser_navigate).",
        category="tool_failure",
        keywords="web_search failed timeout fallback",
        source="test",
    )
    assert r3.get("duplicate"), "identical recent lesson should be deduped"

    relevant = store.relevant("web_search keeps timing out, use a fallback")
    assert relevant, "should find the web_search lesson"
    assert any("web_search" in r["lesson"] for r in relevant)
    assert all(r["score"] > 0 for r in relevant)
    # mark applied increments
    rid = relevant[0]["id"]
    before = store.relevant("web_search keeps timing out, use a fallback")[0]["times_applied"]
    store.mark_applied(rid)
    after = store.relevant("web_search keeps timing out, use a fallback")[0]["times_applied"]
    assert after == before + 1
    stats = store.stats()
    assert stats["total"] >= 2
    print(f"✅ Lessons store: add/relevant/dedupe/applied/stats all work ({stats['total']} lessons)")


def test_lessons_distillers():
    from core.reasoning.lessons import LessonsStore

    store = LessonsStore()
    # correction detected
    res = store.distill_user_correction("That's wrong, the price is 49 not 99")
    assert res and res.get("success")
    # non-correction -> None
    assert store.distill_user_correction("Please summarize the doc") is None
    # tool failure
    res2 = store.distill_tool_failure("browser_navigate", "TimeoutError: page did not load")
    assert res2 and res2.get("success")
    assert store.distill_tool_failure("x", "") is None
    # reflection
    res3 = store.distill_reflection({"mistakes": ["Tool file_read failed: not found", "User correction: wrong output"]})
    assert len(res3) >= 1
    # skill failure
    res4 = store.distill_skill_failure("pdf_qa", "module import error")
    assert res4 and res4.get("success")
    print("✅ Lessons distillers: correction/tool/reflection/skill all work")


def test_governor_strategy_and_budget():
    from core.reasoning.governor import governor

    # strategy mapping
    assert governor.strategy_for("hi there", mode="agent") == "none"
    assert governor.strategy_for(
        "Explain in detail why python async is faster for io bound tasks with examples.",
        mode="agent",
    ) == "reflexion"
    assert governor.strategy_for(
        "Research and compare three free vector databases, verify pricing claims, and give a detailed recommendation.",
        mode="agent",
    ) == "verify"
    assert governor.strategy_for(
        "Design a complete architecture for a self-hosted AI assistant with browser automation, custom API integrations, secure auth, deployment strategy, cost analysis and a 12-month roadmap.",
        mode="agent",
    ) == "self_consistency"
    # override honored
    config.think_strategy = "none"
    assert governor.strategy_for("Explain in detail why python async is faster.", mode="agent") == "none"
    config.think_strategy = "auto"
    # council already deliberated -> no strategy
    assert governor.strategy_for("Research and compare three free vector databases.", mode="agent", council_used=True) == "none"
    # step budget: easy stays cheap, hard gets room, capped by config.
    # Pin every governor input this asserts on — a developer's local .env may
    # raise HERMUS_MAX_TOOL_STEPS / _CHAT_MAX_STEPS / _STEP_BUDGET_FULL, and this
    # test is about the default share-scaling behaviour, not ambient config.
    old = (config.max_tool_steps, config.step_budget_full, config.chat_max_steps)
    config.max_tool_steps = 32
    config.step_budget_full = False
    config.chat_max_steps = 2
    try:
        assert governor.step_budget("hi") <= 2
        assert 1 <= governor.step_budget("hi") <= config.max_tool_steps
        assert governor.step_budget("x" * 400) <= config.max_tool_steps
        assert governor.step_budget("anything", mode="chat") <= 2
    finally:
        (config.max_tool_steps, config.step_budget_full, config.chat_max_steps) = old
    print("✅ Governor: strategy mapping, overrides, step budgets all work")


def test_governor_budget_caps_are_liftable_without_code_edits():
    """The two caps that used to be hard-coded are now env-driven.

    ``step_budget`` previously returned ``min(2, max_tool_steps)`` for chat and a
    difficulty-scaled share otherwise, with no way to lift either. Both are now
    configurable, and neither can collapse to 0 (which would make the agent's
    ``while steps < budget_steps`` loop never execute and silently disable tool
    use instead of raising a limit).
    """
    from core.reasoning.governor import governor

    old = (config.max_tool_steps, config.step_budget_full, config.chat_max_steps)
    config.max_tool_steps = 200
    try:
        # Defaults: chat capped at 2, easy tasks get a small share of the budget.
        config.step_budget_full = False
        config.chat_max_steps = 2
        assert governor.step_budget("anything", mode="chat") == 2
        assert governor.step_budget("hi") < 200

        # HERMUS_CHAT_MAX_STEPS lifts the chat ceiling, still clamped to budget.
        config.chat_max_steps = 50
        assert governor.step_budget("anything", mode="chat") == 50
        assert governor.step_budget("anything", mode="multi-chat") == 50
        config.chat_max_steps = 9999
        assert governor.step_budget("anything", mode="chat") == 200

        # HERMUS_STEP_BUDGET_FULL grants the whole budget at every difficulty.
        config.step_budget_full = True
        assert governor.step_budget("hi") == 200
        assert governor.step_budget("x" * 400) == 200

        # A zero/negative chat cap must not disable tool use entirely.
        config.step_budget_full = False
        for bad in (0, -5):
            config.chat_max_steps = bad
            assert governor.step_budget("anything", mode="chat") >= 1
    finally:
        (config.max_tool_steps, config.step_budget_full, config.chat_max_steps) = old


def test_governor_budget_knobs_default_to_previous_behaviour():
    """Without the new env vars set, nothing about the budget changes."""
    from core.config import Config

    assert Config.model_fields["chat_max_steps"].default == 2
    assert Config.model_fields["step_budget_full"].default is False


def test_strategy_reflexion():
    from core.reasoning.strategies import apply_strategy

    content, meta = apply_strategy(
        "reflexion",
        "Explain python async",
        [{"tool": "web_search", "args": {"query": "python async"}, "result": {"results": [{"title": "x"}]}}],
        "Draft answer about async.",
        model="mock/mock",
    )
    assert content, "strategy must return content"
    assert meta.get("strategy") == "reflexion"
    print(f"✅ reflexion_in_loop ran: meta={meta}")


def test_strategy_self_consistency():
    from core.reasoning.strategies import apply_strategy

    content, meta = apply_strategy(
        "self_consistency",
        "What is the capital of France?",
        [],
        "Draft.",
        model="mock/mock",
        k=2,
    )
    assert content
    assert meta.get("strategy") == "self_consistency"
    assert meta.get("drafts", 0) >= 1
    print(f"✅ self_consistency ran: drafts={meta.get('drafts')} fallback_merge={meta.get('fallback_merge')}")


def test_strategy_verify():
    from core.reasoning.strategies import apply_strategy

    content, meta = apply_strategy(
        "verify",
        "Compare free password managers",
        [],
        "Draft claiming Bitwarden costs 10$.",
        model="mock/mock",
    )
    assert content
    assert meta.get("strategy") == "verify"
    print(f"✅ verify_with_tools ran: claims={meta.get('claims')} searches={meta.get('searches')}")


def test_strategy_unknown_degrades():
    from core.reasoning.strategies import apply_strategy

    content, meta = apply_strategy("tot", "task", [], "draft", model="mock/mock")
    assert content == "draft" and meta.get("skipped")
    print("✅ unknown strategy degrades to original draft")


def test_agent_lessons_in_prompt_and_chat():
    from core.reasoning.lessons import LessonsStore
    from core.agent import HermusAgent

    store = LessonsStore()
    with store._conn() as conn:
        conn.execute("DELETE FROM lessons")
    store.add(
        "User correction: 'that is not what I asked'. Verify this area carefully.",
        category="user_correction",
        keywords="correction verify",
        source="test",
    )

    agent = HermusAgent(model="mock/mock", mode="agent")
    # system prompt includes relevant lessons
    prompt = agent._build_system_prompt("verify the correction i mentioned")
    assert "Lessons learned" in prompt
    assert "correction" in prompt.lower()

    # a chat turn still works end to end with strategy machinery active
    # (difficulty 3 -> reflexion; difficulty 4+ would convene the council instead)
    result = agent.chat(
        "Explain in detail why python async is faster for io bound tasks with examples."
    )
    assert result.get("response")
    assert "strategy" in result
    assert result["strategy"] in ("reflexion", "none")
    print(f"✅ Agent lessons injection + strategy in chat: strategy={result.get('strategy')}")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n--- {name} ---")
            fn()
    print("\n🎉 All DeepThink (Phase 3) tests passed (offline, mock model)")
