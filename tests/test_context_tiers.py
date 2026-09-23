"""The tiered context contract: minimum sufficient prompt, measured.

These tests exist so context growth is a failing build rather than a slow
surprise. They pin three things the product spec asks for: the core tier is
always small, explanatory material never rides along by default, and everything
that was removed stays reachable through ``context_read``.
"""

from __future__ import annotations

from core.context import ContextBudget, ContextRequest, Tier, build_system_prompt, fit
from core.context.assembler import _memory
from core.context.ondemand import REPO_ROOT, read_context

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _req(**kw):
    base = dict(
        user_message="fix the failing login test",
        session_id="s1",
        mode="agent",
        project="hermus",
        model_name="mock/mock",
        max_steps=8,
        capability_line="21 tools of 171 registered — call `expand_tools` for more (browser, vision, voice)",
    )
    base.update(kw)
    return ContextRequest(**base)


def _render(req, emit=None):
    text, report = build_system_prompt(req, emit=emit)
    return text, report


# --------------------------------------------------------------------------- #
# core tier
# --------------------------------------------------------------------------- #


def test_core_tier_carries_identity_constraints_and_state_only():
    text, report = _render(_req())
    kinds = {b["kind"]: b["tier"] for b in report["blocks"]}
    assert kinds["identity"] == "core"
    assert kinds["constraints"] == "core"
    assert kinds["state"] == "core"
    assert "Hermus" in text
    assert "max_steps=8" not in text  # budget is phrased for the model, not the code
    assert "8 steps" in text


def test_core_tier_stays_under_budget_even_with_a_full_roster_of_blocks():
    """A bloated task tier must never push the core tier out."""
    huge = _req(budget=ContextBudget(core_chars=1600, task_chars=200))
    text, report = _render(huge)
    core = report["tiers"].get("core", 0)
    assert core <= 1600
    assert {"identity", "constraints", "state"} <= {b["kind"] for b in report["blocks"]}


def test_untruncatable_blocks_are_omitted_and_recorded():
    from core.context import ContextBlock

    blocks = [
        ContextBlock(kind="constraints", tier=Tier.CORE, text="rules " * 40, source="t", priority=80),
        ContextBlock(kind="continuity", tier=Tier.CORE, text="presence " * 60, source="t", priority=40),
        ContextBlock(kind="identity", tier=Tier.CORE, text="you are hermus", source="t", priority=100),
    ]
    plan = fit(blocks, ContextBudget(core_chars=400, task_chars=0))
    kept = {b.kind for b in plan.blocks}
    assert "identity" in kept and "constraints" in kept
    assert "continuity" not in kept
    assert any(kind == "continuity" and "not truncatable" in reason for kind, reason, _ in plan.omitted)


def test_truncatable_block_is_cut_not_dropped():
    from core.context import ContextBlock

    blocks = [ContextBlock(kind="memory", tier=Tier.TASK, text="x" * 5000, source="t", priority=60, truncate_at=2200)]
    plan = fit(blocks, ContextBudget(core_chars=0, task_chars=1000))
    assert [b.chars for b in plan.blocks] == [1000]
    assert plan.omitted and plan.omitted[0][0] == "memory"


# --------------------------------------------------------------------------- #
# what must NOT be in the prompt
# --------------------------------------------------------------------------- #


def test_endpoint_inventory_and_architecture_prose_are_not_injected():
    text, _ = _render(_req())
    for needle in ("GET /", "POST /", "canonical owner", "The architecture consists", "SQLite FTS5", "this dashboard is only"):
        assert needle not in text, f"{needle!r} should be on-demand only"


def test_removed_material_is_still_reachable_on_demand():
    listing = read_context("list")
    assert listing["success"] and set(listing["topics"]) >= {"architecture", "endpoints", "tools", "memory", "docs"}

    endpoints = read_context("endpoints", "fleet")
    assert endpoints["success"] and "GET /" in endpoints["text"]

    tools = read_context("tools", "memory_search")
    assert tools["success"] and tools["catalog_size"] > 50
    assert any(s["name"] == "memory_search" for s in tools["schemas"])


def test_docs_topic_refuses_paths_outside_the_allowlist():
    for attempt in ("../../etc/passwd", "../../../Windows/win.ini", "secrets"):
        result = read_context("docs", attempt)
        assert not result.get("text") or "not readable" in str(result.get("error", ""))
    assert REPO_ROOT.name == "hermus-agent-free"


def test_on_demand_context_is_offered_from_the_first_step():
    """A retrieval tool nobody can call is the same as no retrieval path."""
    from core.tool_select import CORE_TOOLS

    assert "context_read" in CORE_TOOLS


# --------------------------------------------------------------------------- #
# memory dedupe
# --------------------------------------------------------------------------- #


def test_exactly_one_memory_block_per_turn(monkeypatch):
    from core.memory import memory

    monkeypatch.setattr(memory, "recall_context", lambda *a, **k: {"text": "Typed recall:\n- user likes terse answers", "kept": [1], "mode": "hybrid"}, raising=False)
    monkeypatch.setattr(memory, "get_curated_memory", lambda *a, **k: [{"key": "pref", "value": "terse"}], raising=False)
    text, report = _render(_req())
    kinds = [b["kind"] for b in report["blocks"]]
    assert kinds.count("memory") == 1
    assert "Typed recall" in text
    assert "Curated memory" not in text  # the fallback never stacks on the winner


def test_curated_memory_is_the_fallback_when_typed_recall_is_empty(monkeypatch):
    from core.memory import memory

    monkeypatch.setattr(memory, "recall_context", lambda *a, **k: {"text": "", "kept": []}, raising=False)
    monkeypatch.setattr(memory, "get_curated_memory", lambda *a, **k: [{"key": "pref", "value": "terse"}], raising=False)
    text, _ = _render(_req())
    assert "Curated memory" in text and "pref" in text


def test_lean_mission_node_carries_no_conversation_scaffolding(monkeypatch):
    from core.memory import memory

    monkeypatch.setattr(memory, "recall_context", lambda *a, **k: {"text": "recall noise", "kept": [1]}, raising=False)
    text, report = _render(_req(lean=True))
    kinds = {b["kind"] for b in report["blocks"]}
    assert not kinds & {"memory", "continuity", "user", "skills", "lessons"}
    assert "recall noise" not in text
    assert len(text) < 900


def test_task_brief_outranks_incidental_context():
    _text, report = _render(_req(task_brief="NODE GOAL: patch auth.py and run pytest"))
    priorities = {b["kind"]: b["priority"] for b in report["blocks"]}
    assert priorities["task"] > priorities.get("memory", 0)
    assert priorities["task"] > priorities.get("continuity", 0)


def test_report_is_emitted_for_observability():
    events = []
    _render(_req(), emit=lambda kind, payload: events.append((kind, payload)))
    context_events = [p for k, p in events if k == "context_assembled"]
    assert context_events
    assert "total_chars" in context_events[0] and "on_demand" in context_events[0]


def test_memory_helper_returns_none_without_any_recall(monkeypatch):
    from core.memory import memory

    monkeypatch.setattr(memory, "recall_context", lambda *a, **k: {"text": "", "kept": []}, raising=False)
    monkeypatch.setattr(memory, "get_curated_memory", lambda *a, **k: [], raising=False)
    assert _memory(_req()) is None
