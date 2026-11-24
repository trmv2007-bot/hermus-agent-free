"""One turn end to end, with a scripted model instead of a real one.

Everything here is deterministic: a fake agent returns prepared turns, so what
is under test is the turn path's *logic* — was the tier decided and recorded, did
the plan arrive before the first action, did an unproven claim get its one
repair, and does a refusal stay a refusal.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.runtime import execute


class ScriptedAgent:
    """A stand-in for ``HermusAgent`` that plays back prepared turns."""

    def __init__(self, scripts, model: str = "ollama/llama3.2:1b"):
        self.model_name = model
        self.llm = SimpleNamespace(provider=model.split("/", 1)[0])
        self.session_id = "scripted_session"
        self.plan_override = None
        self._scripts = list(scripts)
        self.calls: list[str] = []

    def chat(self, text, *, on_event=None, stream=False, should_cancel=None, steer_source=None):
        self.calls.append(text)
        assert self._scripts, "the turn path asked for more model calls than the script allows"
        script = self._scripts.pop(0)
        if callable(script):
            script = script()

        tool_results = []
        for name, args, result in script.get("tools", []):
            ok = isinstance(result, dict) and "error" not in result
            if on_event is not None:
                on_event(
                    "tool_result",
                    {"step": 1, "tool": name, "ms": 1, "error": not ok, "preview": str(result)[:120]},
                )
            tool_results.append({"tool": name, "args": args, "result": result, "step": 1})

        return {
            "session_id": self.session_id,
            "response": script.get("response", ""),
            "tool_results": tool_results,
            "tool_calls": [tr["tool"] for tr in tool_results],
            "steps": script.get("steps", 1),
            "plan": None,
        }


@pytest.fixture(autouse=True)
def _isolated_thinking_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMUS_THINKING_DIR", str(tmp_path / "thinking"))
    return tmp_path


def _events():
    recorded: list[tuple[str, dict]] = []

    def on_event(event_type, data=None):
        recorded.append((event_type, data or {}))

    return recorded, on_event


def test_an_easy_question_is_routed_local_with_a_reason_on_the_turn(tmp_path):
    agent = ScriptedAgent([{"response": "Paris.", "steps": 1}])
    recorded, on_event = _events()

    result = execute("what is the capital of France?", agent=agent, mode="agent", prefer="chat", on_event=on_event)

    assert result["run_kind"] == "chat"
    routing = result["routing"]
    assert routing["tier"] == "local"
    assert routing["model"] == "ollama/llama3.2:1b"
    assert "free tier is not spent" in routing["reason"]
    assert routing["outcome"]["over_routed"] is False
    assert any(t == "routing_decision" for t, _ in recorded)


def test_the_plan_is_shown_before_the_first_action(tmp_path):
    agent = ScriptedAgent(
        [
            {
                "response": "Wrote it.",
                "tools": [
                    ("file_write", {"path": "out.txt", "content": "hello"}, {"success": True, "path": str(tmp_path / "out.txt")})
                ],
                "steps": 1,
            }
        ]
    )
    (tmp_path / "out.txt").write_text("hello", encoding="utf-8")
    recorded, on_event = _events()

    result = execute("write the word hello into out.txt", agent=agent, mode="agent", prefer="chat", on_event=on_event)

    kinds = [t for t, _ in recorded]
    assert "plan_ready" in kinds
    assert kinds.index("plan_ready") < kinds.index("tool_result")
    plan_payload = next(d for t, d in recorded if t == "plan_ready")
    assert len(plan_payload["steps"]) >= 2
    assert [s["label"] for s in plan_payload["steps"]][:3] == ["look", "change", "verify"]
    assert result["plan"]["steps"][1]["status"] == "done"
    assert any(t == "plan_step" for t in kinds)


def test_a_claim_with_no_evidence_earns_exactly_one_repair(tmp_path):
    target = tmp_path / "out.txt"

    def repair_turn():
        target.write_text("hello", encoding="utf-8")
        return {
            "response": "Wrote out.txt.",
            "tools": [("file_write", {"path": str(target), "content": "hello"}, {"success": True, "path": str(target)})],
            "steps": 1,
        }

    agent = ScriptedAgent([{"response": "I created out.txt for you.", "steps": 1}, repair_turn])
    recorded, on_event = _events()

    result = execute("write the word hello into out.txt", agent=agent, mode="agent", prefer="chat", on_event=on_event)

    assert len(agent.calls) == 2, "exactly one repair turn"
    assert "not verified" in agent.calls[1].lower()
    assert result.get("repaired") is True
    assert result["receipt"]["verified"] is True
    assert result["receipt"]["evidence"][0]["data"]["size"] == 5
    assert result["steps"] == 2  # both turns' steps are counted
    assert [t for t, _ in recorded].count("receipt") == 2


def test_a_turn_that_stays_unproven_says_so_in_the_answer(tmp_path):
    agent = ScriptedAgent(
        [
            {"response": "I created out.txt.", "steps": 1},
            {"response": "Nothing happened on disk.", "steps": 1},
        ]
    )
    result = execute("write the word hello into out.txt", agent=agent, mode="agent", prefer="chat")

    assert result["receipt"]["verified"] is False
    assert "Not verified:" in result["response"]
    assert result["receipt"]["repair_required"] is False


def test_a_pure_answer_needs_no_receipt_and_gets_no_suffix():
    agent = ScriptedAgent([{"response": "7 times 8 is 56.", "steps": 1}])
    result = execute("what is 7 times 8?", agent=agent, mode="agent", prefer="chat")

    assert result["receipt"]["verdict"] == "not_applicable"
    assert "Verified:" not in result["response"]
    assert "Not verified:" not in result["response"]


def test_a_refusal_is_a_complete_answer_not_a_failed_verification():
    refusal = (
        "I will not delete every file in your home directory. That is irreversible and would destroy "
        "your work. If you meant something narrower, say which folder."
    )
    agent = ScriptedAgent([{"response": refusal, "steps": 1}])
    result = execute("delete every file in my home directory", agent=agent, mode="agent", prefer="chat")

    assert result["response"] == refusal
    assert result["receipt"]["verified"] is True
    assert result["receipt"]["verdict"] == "refused"
    assert result["tool_calls"] == []
    assert len(agent.calls) == 1, "a refusal is not repaired"


def test_read_only_turns_are_never_routed_or_planned():
    agent = ScriptedAgent([{"response": "all good", "steps": 1}])
    recorded, on_event = _events()

    result = execute(
        "write the word hello into out.txt", agent=agent, mode="agent", prefer="chat", read_only=True, on_event=on_event
    )

    assert result.get("routing") is None
    assert result.get("plan") is None
    assert not any(t == "plan_ready" for t, _ in recorded)


def test_an_under_routed_local_turn_escalates_its_repair(tmp_path, monkeypatch):
    """Two failing local calls is the under-routing signal: the repair goes hosted."""
    import core.thinking.turn as turn_module
    from core.thinking.turn import TurnMind

    monkeypatch.setattr(turn_module, "configured_hosted_refs", lambda: ["groq/llama-3.1-8b-instant"])
    # the escalation target is the model the key answers, measured outside this test
    monkeypatch.setattr(turn_module, "answering_hosted_ref", lambda ref: ref)

    agent = ScriptedAgent(
        [
            {
                "response": "I created out.txt.",
                "tools": [
                    ("file_write", {"path": str(tmp_path / "a.txt")}, {"error": "permission denied"}),
                    ("file_write", {"path": str(tmp_path / "b.txt")}, {"error": "permission denied"}),
                ],
                "steps": 2,
            },
            {"response": "I still cannot write files.", "steps": 1},
        ]
    )
    recorded, on_event = _events()
    mind = TurnMind(agent, "write the word hello into out.txt", mode="agent", emit=on_event, base_dir=tmp_path)
    swapped: list[str] = []
    monkeypatch.setattr(mind, "_swap_model", lambda ref: swapped.append(ref) or True)

    mind.prepare()
    first = {
        "response": "I created out.txt.",
        "tool_results": [
            {"tool": "file_write", "args": {}, "result": {"error": "permission denied"}},
            {"tool": "file_write", "args": {}, "result": {"error": "permission denied"}},
        ],
        "steps": 2,
    }
    mind.finish(first)
    assert mind.outcome["under_routed"] is True
    assert mind.escalate_for_repair() is True
    assert swapped == ["groq/llama-3.1-8b-instant"]
    assert any(t == "routing_escalated" for t, _ in recorded)


def test_the_ledger_holds_the_turn_after_it_runs(tmp_path):
    from core.thinking.routing import RoutingLedger

    agent = ScriptedAgent([{"response": "Paris.", "steps": 1}])
    execute("what is the capital of France?", agent=agent, mode="agent", prefer="chat")

    summary = RoutingLedger(limit=50).summary()
    assert summary["turns"] >= 1
    assert summary["tiers"]["local"] >= 1


def test_thinking_can_be_switched_off_entirely(monkeypatch):
    monkeypatch.setenv("HERMUS_THINKING_ENABLED", "0")
    import core.config as config_module

    monkeypatch.setattr(config_module.config, "thinking_enabled", False)
    agent = ScriptedAgent([{"response": "Paris.", "steps": 1}])
    result = execute("what is the capital of France?", agent=agent, mode="agent", prefer="chat")
    assert "routing" not in result
    assert "receipt" not in result


def test_an_escalated_turn_is_repicked_onto_the_model_that_answers(tmp_path, monkeypatch):
    """Routing's catalog pick can 429; the turn follows the measured model instead."""
    import core.thinking.turn as turn_module
    from core.thinking.turn import TurnMind

    monkeypatch.setattr(turn_module, "configured_hosted_refs", lambda: ["mistral/mistral-small-latest"])
    monkeypatch.setattr(turn_module, "answering_hosted_ref", lambda ref: "mistral/ministral-8b-latest")

    agent = ScriptedAgent([{"response": "done", "steps": 1}], model="ollama/llama3.2:1b")
    recorded, on_event = _events()
    mind = TurnMind(agent, "Audit, refactor and migrate the module.", mode="agent", emit=on_event, base_dir=tmp_path)
    swapped: list[str] = []
    monkeypatch.setattr(mind, "_swap_model", lambda ref: swapped.append(ref) or True)
    mind.prepare()
    assert mind.decision.tier == "free_hosted"
    assert mind.decision.model == "mistral/ministral-8b-latest"
    assert "did not answer" in mind.decision.reason
    assert swapped == ["mistral/ministral-8b-latest"]


def test_an_escalation_that_nothing_answers_stays_on_the_local_model(tmp_path, monkeypatch):
    import core.thinking.turn as turn_module
    from core.thinking.turn import TurnMind

    monkeypatch.setattr(turn_module, "configured_hosted_refs", lambda: ["mistral/mistral-small-latest"])
    monkeypatch.setattr(turn_module, "answering_hosted_ref", lambda ref: "")

    agent = ScriptedAgent([{"response": "done", "steps": 1}], model="ollama/llama3.2:1b")
    recorded, on_event = _events()
    mind = TurnMind(agent, "Audit, refactor and migrate the module.", mode="agent", emit=on_event, base_dir=tmp_path)
    swapped: list[str] = []
    monkeypatch.setattr(mind, "_swap_model", lambda ref: swapped.append(ref) or True)
    mind.prepare()
    assert mind.decision.tier == "local"
    assert mind.decision.model == "ollama/llama3.2:1b"
    assert "stays local" in mind.decision.reason
    assert swapped == [], "a tier that answers nothing must not be spent"
