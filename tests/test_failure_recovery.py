"""Recovery diagnosis: bounded, typed, and not allowed to repeat itself."""

from __future__ import annotations

from core.contracts import FailureClass
from core.failure_recovery import (
    ACTION_LIMITS,
    RecoveryAction,
    classify,
    count_attempts,
    diagnose,
    nodes_to_reset,
    plan,
    repeat_of_last,
)


class FakeNode:
    def __init__(self, status):
        self.status = status
        self.inputs = {}
        self.retries = 0


class FakeDag:
    def __init__(self, statuses):
        self.nodes = {name: FakeNode(status) for name, status in statuses.items()}


def test_structured_codes_beat_text_matching():
    assert classify(error_code="rate_limited", errors=["weird prose"]) == FailureClass.RATE_LIMIT.value
    assert classify(error_code="approval_required") == FailureClass.POLICY_DENIED.value
    assert classify(error_code="no_model_backend") == FailureClass.PROVIDER_UNAVAILABLE.value


def test_text_fallback_is_still_typed():
    assert classify(errors=["HTTP 429 too many requests"]) == FailureClass.RATE_LIMIT.value
    assert classify(errors=["Error: 401 unauthorized"]) == FailureClass.AUTH.value
    assert classify(errors=["Context length exceeded for model"]) == FailureClass.CONTEXT_OVERFLOW.value
    assert classify(errors=["getaddrinfo failed"]) == FailureClass.NETWORK.value


def test_a_missing_deliverable_is_a_distinct_failure():
    assert classify(missing_artifacts=["a.py"], errors=["looked fine"], verified=True) == "missing_artifact"


def test_unverified_without_any_signal_says_so():
    assert classify(errors=["nothing matched"], verified=False) == "not_verified"
    assert classify(errors=[], verified=True) == FailureClass.UNKNOWN.value


def test_denied_policy_escalates_instead_of_retrying():
    actions = plan(FailureClass.POLICY_DENIED.value)
    assert actions == [RecoveryAction.ESCALATE_HITL]


def test_exhausted_strategies_are_dropped_not_repeated():
    spent = {RecoveryAction.ALTERNATE_KEY.value: 2, RecoveryAction.RETRY_NODE.value: 1}
    actions = plan(FailureClass.RATE_LIMIT.value, attempts=spent)
    assert RecoveryAction.ALTERNATE_KEY not in actions
    assert RecoveryAction.RETRY_NODE not in actions
    assert actions == [RecoveryAction.ESCALATE_HITL]


def test_a_class_with_nothing_left_aborts():
    spent = {action.value: limit for action, limit in ACTION_LIMITS.items()}
    assert plan(FailureClass.RATE_LIMIT.value, attempts=spent) == [RecoveryAction.ABORT]


def test_diagnosis_records_what_it_depending_on():
    from_structured = diagnose(error_code="rate_limited", verified=False)
    from_text = diagnose(errors=["429 too many requests"], verified=False)
    assert from_structured["basis"] == "structured_error_code"
    assert from_text["basis"] == "text_match"
    assert from_structured["failure_class"] == from_text.failure_class == FailureClass.RATE_LIMIT.value
    assert from_structured.actions[0] is RecoveryAction.ALTERNATE_KEY


def test_attempts_are_read_back_out_of_history_so_a_restart_respects_them():
    history = [
        {"recovery_actions": [RecoveryAction.ALTERNATE_KEY.value, RecoveryAction.RETRY_NODE.value]},
        {"recovery_actions": [RecoveryAction.ALTERNATE_KEY.value]},
    ]
    counts = count_attempts(history)
    assert counts[RecoveryAction.ALTERNATE_KEY.value] == 2
    assert counts[RecoveryAction.RETRY_NODE.value] == 1
    # at the limit now, so the next round cannot pick it again
    assert RecoveryAction.ALTERNATE_KEY not in plan(FailureClass.RATE_LIMIT.value, attempts=counts)


def test_repeating_an_identical_round_is_recognised():
    diagnosis = diagnose(errors=["429 rate limited"], verified=False)
    history = [
        {
            "failure_class": diagnosis.failure_class,
            "recovery_actions": [a.value for a in diagnosis.actions],
            "errors": diagnosis["errors"],
        }
    ]
    assert repeat_of_last(history, diagnosis) is True
    # new complaint from the verifier means the round is worth spending
    diagnosis["errors"] = diagnosis["errors"] + ["artifact app.py is still empty"]
    assert repeat_of_last(history, diagnosis) is False
    assert repeat_of_last([], diagnosis) is False


def test_transport_failures_keep_completed_work():
    dag = FakeDag({"research": "completed", "write": "failed", "test": "skipped"})
    diagnosis = diagnose(error_code="rate_limited", verified=False)
    reset = nodes_to_reset(dag, diagnosis)
    assert sorted(reset) == ["test", "write"]


def test_a_plan_shaped_failure_rewinds_everything():
    dag = FakeDag({"research": "completed", "write": "failed"})
    reset = nodes_to_reset(dag, diagnose(errors=["context length exceeded"], verified=False, attempts={}))
    # the playbook's last resort is in range, so the finished work is discarded
    assert reset is None


def test_cancelled_never_repairs():
    dag = FakeDag({"a": "completed", "b": "running"})
    assert nodes_to_reset(dag, diagnose(errors=[], cancelled=True)) == []


def test_the_engine_bounds_its_own_repair_attempts(tmp_path):
    """A repair round is a diagnosis with actions, and spent actions disappear."""
    from core.mission import MissionEngine

    def executor(node, ctx):
        return {"success": True, "output": "did it\nTraceback (most recent call last):\n  still broken"}

    engine = MissionEngine(executor=executor, storage_dir=tmp_path / "missions")
    report = engine.start_mission("keep failing the same way", budget_steps=8, max_repairs=3)

    rounds = report.repair_history
    assert rounds, "the engine diagnosed at least one repair round"
    assert rounds[0]["failure_class"] == "not_verified"
    assert rounds[0]["recovery_actions"], "the round chose actions"
    assert rounds[0]["whole_dag"] is True
    assert rounds[0]["nodes_rewound"], "the first round rewound the failed work"

    # The accounting is real: strategies spent in round one are gone in round two.
    assert set(rounds[0]["recovery_actions"]) > set(rounds[1]["recovery_actions"])
    assert set(rounds[1]["recovery_actions"]) <= set(rounds[0]["recovery_actions"])

    # And the loop ends on a stated reason instead of grinding to the cap.
    assert any(entry.get("outcome") for entry in rounds)
    assert len(rounds) < 3, f"repairs stopped early, got {rounds}"
    assert report.state == "failed"
