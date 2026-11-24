"""A turn's thinking is recorded on the turn, not only drawn by the page.

The room shows the plan, the receipt and the routing reason while a turn runs.
A reload has to bring them back, which means the server has to hold them: the
run's own events are folded into the assistant turn they belong to, and
``GET /conversation/{id}`` returns them beside the answer.

These tests drive the bus directly — no model, no gateway — so they prove the
record itself: what it keeps, what it refuses to keep, and that a plan emitted
before the session claims its run is not lost.
"""

from __future__ import annotations

import uuid

import pytest

from core.conversation import ConversationManager
from core.run_events import run_bus

PLAN_STEPS = [
    {"n": 1, "goal": "Read the current notes", "action": "investigate", "label": "look", "status": "pending", "evidence": []},
    {"n": 2, "goal": "Write the notes", "action": "edit", "label": "change", "status": "pending", "evidence": []},
    {"n": 3, "goal": "Check the work", "action": "verify", "label": "verify", "status": "pending", "evidence": []},
]

RECEIPT = {
    "verified": True,
    "verdict": "verified",
    "changed": True,
    "claim": "verified 1 file",
    "problems": [],
    "evidence": [
        {
            "kind": "file",
            "ok": True,
            "detail": "notes.md sha256:4f2a9c1d 312B",
            "data": {"path": "notes.md", "sha256": "4f2a9c1d", "size": 312, "tool": "file_write"},
        }
    ],
}


@pytest.fixture()
def manager():
    made = ConversationManager(max_sessions=4)
    try:
        yield made
    finally:
        made._sink_remove()
        made._live.clear()


def _asked(manager: ConversationManager, question: str = "write the notes and then check them") -> tuple[str, str]:
    session_id = f"room_{uuid.uuid4().hex[:8]}"
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    manager.get_or_create(session_id, user_id="local", platform="web")
    manager.add_turn(session_id, "user", question)
    manager.attach_run(session_id, run_id)
    return session_id, run_id


def _thinking(manager: ConversationManager, session_id: str) -> dict:
    turns = manager.snapshot(session_id)["turns"]
    assert turns and turns[-1]["role"] == "assistant"
    return turns[-1].get("thinking") or {}


def test_a_turn_keeps_the_plan_it_followed_and_the_receipt_it_earned(manager):
    session_id, run_id = _asked(manager)
    run_bus.publish(run_id, "plan_ready", {"goal": "write the notes", "steps": PLAN_STEPS, "difficulty": 2})
    run_bus.publish(run_id, "plan_step", {"step": 2, "goal": "Write the notes", "status": "done", "evidence": "file_write ok"})
    run_bus.publish(run_id, "agent_response", {"text": "Notes written.\n\nVerified: notes.md sha256:4f2a9c1d 312B"})
    run_bus.publish(
        run_id,
        "plan_finished",
        {
            "steps": [
                dict(PLAN_STEPS[0], status="skipped"),
                dict(PLAN_STEPS[1], status="done"),
                dict(PLAN_STEPS[2], status="skipped"),
            ]
        },
    )
    run_bus.publish(run_id, "receipt", RECEIPT)
    run_bus.publish(run_id, "run_finished", {"status": "finished"})

    thinking = _thinking(manager, session_id)
    assert [step["status"] for step in thinking["plan"]["steps"]] == ["skipped", "done", "skipped"]
    assert thinking["receipt"]["verified"] is True
    assert thinking["receipt"]["evidence"][0]["data"]["sha256"] == "4f2a9c1d"
    # The rail needs the goal and the state, not the engine's internal fields.
    assert set(thinking["plan"]["steps"][0]) == {"n", "goal", "action", "label", "status"}


def test_the_routing_reason_is_kept_beside_the_turn(manager):
    session_id, run_id = _asked(manager)
    run_bus.publish(
        run_id,
        "routing_decision",
        {
            "tier": "local",
            "model": "ollama/llama3.2:1b",
            "reason": "difficulty 1: the local model's ceiling covers this, so the free tier is not spent",
            "signals": ["difficulty:1"],
        },
    )
    run_bus.publish(run_id, "agent_response", {"text": "Hello."})
    run_bus.publish(run_id, "run_finished", {"status": "finished"})

    thinking = _thinking(manager, session_id)
    assert thinking["routing"]["model"] == "ollama/llama3.2:1b"
    assert thinking["routing"]["tier"] == "local"
    assert "ceiling covers this" in thinking["routing"]["reason"]


def test_a_plan_emitted_before_the_session_claimed_the_run_is_not_lost(manager):
    """The job is submitted first, so the plan can be on the bus before attach."""
    session_id = f"room_{uuid.uuid4().hex[:8]}"
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    manager.get_or_create(session_id, user_id="local", platform="web")
    manager.add_turn(session_id, "user", "write the notes")
    run_bus.publish(run_id, "plan_ready", {"goal": "write the notes", "steps": PLAN_STEPS, "difficulty": 2})
    manager.attach_run(session_id, run_id)
    run_bus.publish(run_id, "agent_response", {"text": "Done."})
    run_bus.publish(run_id, "run_finished", {"status": "finished"})

    thinking = _thinking(manager, session_id)
    assert len(thinking["plan"]["steps"]) == 3


def test_a_turn_with_nothing_to_show_carries_no_thinking_key(manager):
    session_id, run_id = _asked(manager, "what can you do?")
    run_bus.publish(run_id, "agent_response", {"text": "I can read your files and run commands."})
    run_bus.publish(run_id, "run_finished", {"status": "finished"})

    turns = manager.snapshot(session_id)["turns"]
    assert turns[-1]["role"] == "assistant"
    assert "thinking" not in turns[-1]


def test_a_turn_that_answered_nothing_leaves_no_turn_behind(manager):
    session_id, run_id = _asked(manager)
    run_bus.publish(run_id, "plan_ready", {"goal": "write the notes", "steps": PLAN_STEPS, "difficulty": 2})
    run_bus.publish(run_id, "run_error", {"error": "the model stopped"})

    turns = manager.snapshot(session_id)["turns"]
    assert [turn["role"] for turn in turns] == ["user"]
    assert run_id not in manager._live


def test_a_receipt_is_kept_to_what_a_person_is_shown(manager):
    session_id, run_id = _asked(manager)
    noisy = dict(RECEIPT)
    noisy["evidence"] = [
        dict(
            RECEIPT["evidence"][0],
            detail="x" * 900,
            data={"path": "p" * 900, "sha256": "a" * 900, "stdout": "not kept", "size": 1},
        )
    ]
    run_bus.publish(run_id, "agent_response", {"text": "Done."})
    run_bus.publish(run_id, "receipt", noisy)
    run_bus.publish(run_id, "run_finished", {"status": "finished"})

    evidence = _thinking(manager, session_id)["receipt"]["evidence"][0]
    assert len(evidence["detail"]) == 300
    assert len(evidence["data"]["sha256"]) == 300
    assert "stdout" not in evidence["data"]


def test_a_malformed_event_costs_one_field_not_the_record(manager):
    """A producer that sends junk must not take the whole turn's thinking with it."""
    session_id, run_id = _asked(manager)
    manager._on_run_event(run_id, {"type": "plan_ready", "data": {"goal": None, "steps": "not a list"}})
    manager._on_run_event(run_id, {"type": "plan_step", "data": {"step": "\x00", "status": None}})
    manager._on_run_event(
        run_id,
        {
            "type": "receipt",
            "data": {
                "verified": True,
                "problems": 2,
                "evidence": [{"kind": "file", "ok": True, "detail": "a.md", "data": "not a dict"}, 7],
            },
        },
    )
    manager._on_run_event(run_id, {"type": "routing_decision", "data": {"model": object(), "tier": None}})
    manager._on_run_event(run_id, {"type": "agent_response", "data": {"text": "Done."}})
    manager._on_run_event(run_id, {"type": "run_finished", "data": "not a dict"})

    thinking = _thinking(manager, session_id)
    assert thinking["plan"]["steps"] == []
    assert thinking["receipt"]["problems"] == ["2"]
    assert thinking["receipt"]["evidence"][0]["data"] == {}
    assert thinking["routing"]["tier"] == ""
