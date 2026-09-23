"""A worker's claim is not the system's verified result.

The engine used to collapse "every node said DONE" and "the verifier confirmed
the outcome" into a single boolean and discard the inputs. These tests pin that
both survive, that the gap between them is recorded as a measurement, and that
the record persists across a restart.
"""

from __future__ import annotations

from pathlib import Path

from core.contracts import OutcomeState
from core.mission import MissionReport, MissionState, assess_claim_vs_verified, derive_outcome_state
from core.verifier_registry import VerificationResult, classify_check


class FakeVerification:
    """Mirrors VerificationResult, including how it derives provenance."""

    def __init__(self, *, verified=True, score=0.9, errors=None, evidence=None, structural=True, behavioral=True):
        self.verified = verified
        self.score = score
        self.errors = errors or []
        self.evidence = evidence if evidence is not None else [{"type": "behavioral", "check": "test_suite", "status": "passed"}]
        self.structural_verified = structural
        self.behavioral_verified = behavioral

    @property
    def provenance(self) -> dict:
        # Reuse the real dataclass so this fake cannot drift from it.
        return VerificationResult(
            verified=self.verified,
            score=self.score,
            domain="python",
            evidence=self.evidence,
        ).provenance


def _report(tmp_path: Path, **kw) -> MissionReport:
    report = MissionReport(mission_id=kw.pop("mission_id", "m1"), goal=kw.pop("goal", "ship it"), **kw)
    report.artifacts = [str(tmp_path / "present.txt")]
    (tmp_path / "present.txt").write_text("here", encoding="utf-8")
    return report


def test_a_claim_of_completion_that_did_not_verify_is_recorded(tmp_path):
    report = _report(tmp_path)
    found = assess_claim_vs_verified(
        report,
        dag_all_completed=True,
        verification=FakeVerification(verified=False, errors=["no test evidence"]),
        critic={"approved": False, "overall_score": 40},
        node_count=3,
    )
    assert [d["kind"] for d in found] == ["claimed_complete_but_unverified"]
    assert report.agent_claim["dag_all_completed"] is True
    assert report.verified_result["verified"] is False
    # The claim and the verdict are both still on the report, side by side.
    assert report.disagreements[0]["severity"] == "blocking"
    assert report.disagreements[0]["fact"] == "the domain verifier did not confirm the outcome"


def test_a_reported_artifact_that_is_not_on_disk_is_a_disagreement(tmp_path):
    report = _report(tmp_path)
    report.artifacts.append(str(tmp_path / "phantom.py"))
    found = assess_claim_vs_verified(
        report,
        dag_all_completed=True,
        verification=FakeVerification(),
        critic={"approved": True, "overall_score": 90},
        missing_artifacts=["phantom.py"],
    )
    assert "claimed_artifacts_missing_on_disk" in [d["kind"] for d in found]
    assert "phantom.py" in report.agent_claim["artifacts_missing_on_disk"]


def test_verified_but_critic_rejected_is_not_silently_a_pass(tmp_path):
    found = assess_claim_vs_verified(
        _report(tmp_path),
        dag_all_completed=True,
        verification=FakeVerification(),
        critic={"approved": False, "overall_score": 30, "summary": "requirements not met"},
    )
    assert [d["kind"] for d in found] == ["verified_but_critic_rejected"]


def test_agreement_still_leaves_both_sides_on_the_record(tmp_path):
    report = _report(tmp_path)
    found = assess_claim_vs_verified(
        report,
        dag_all_completed=True,
        verification=FakeVerification(),
        critic={"approved": True, "overall_score": 95},
        node_count=2,
    )
    assert found == []
    assert report.disagreements == []
    assert report.agent_claim["nodes"] == 2
    assert report.verified_result["critic_approved"] is True
    assert "verifier_registry" in report.verified_result["source"]
    assert report.outcome_state == OutcomeState.VERIFIED.value


def test_a_verdict_from_worker_supplied_text_alone_is_not_verification(tmp_path):
    """A log that says "tests passed" was written by the party being graded."""
    report = _report(tmp_path)
    found = assess_claim_vs_verified(
        report,
        dag_all_completed=True,
        verification=FakeVerification(evidence=[{"type": "behavioral", "check": "runtime_output", "status": "clean"}]),
        critic={"approved": True, "overall_score": 95},
        node_count=2,
    )
    assert [d["kind"] for d in found] == ["verified_only_on_worker_supplied_text"]
    assert report.verified_result["provenance"]["grounded"] is False
    assert report.outcome_state == OutcomeState.PARTIALLY_VERIFIED.value


def test_an_unclassified_check_never_counts_as_grounding():
    """A new verifier check cannot upgrade an outcome just by existing."""
    assert classify_check("brand_new_check") == "unclassified"
    provenance = VerificationResult(verified=True, score=1.0, domain="python", evidence=[{"check": "brand_new_check"}]).provenance
    assert provenance["grounded"] is False and provenance["certifiable"] is False


def test_outcome_state_ladder():
    grounded = {"counts": {"observed": 2, "worker-reported": 1, "unclassified": 0}, "certifiable": True}
    weak = {"counts": {"observed": 0, "worker-reported": 3, "unclassified": 0}, "certifiable": False}
    assert derive_outcome_state(claim_complete=True, verified=True, provenance=grounded) == OutcomeState.VERIFIED.value
    assert (
        derive_outcome_state(claim_complete=True, verified=True, provenance=weak) == OutcomeState.PARTIALLY_VERIFIED.value
    )
    assert derive_outcome_state(claim_complete=True, verified=False, provenance=weak) == OutcomeState.CLAIMED.value
    assert (
        derive_outcome_state(claim_complete=True, verified=False, provenance=grounded) == OutcomeState.FAILED.value
    )
    assert (
        derive_outcome_state(claim_complete=False, verified=False, provenance=grounded) == OutcomeState.OBSERVED.value
    )
    assert (
        derive_outcome_state(claim_complete=False, verified=False, provenance=weak, nodes_ran=True)
        == OutcomeState.EXECUTED.value
    )
    assert (
        derive_outcome_state(claim_complete=False, verified=False, provenance=weak, nodes_ran=False)
        == OutcomeState.UNKNOWN.value
    )
    # A deliverable that is not on disk outranks every softer reading.
    assert (
        derive_outcome_state(claim_complete=True, verified=True, provenance=grounded, missing_artifacts=["a.py"])
        == OutcomeState.FAILED.value
    )


def test_an_incomplete_dag_that_verified_is_a_warning_not_a_block(tmp_path):
    found = assess_claim_vs_verified(
        _report(tmp_path),
        dag_all_completed=False,
        verification=FakeVerification(),
        critic={"approved": True, "overall_score": 95},
    )
    assert [d["kind"] for d in found] == ["incomplete_dag_but_verified"]
    assert found[0]["severity"] == "warning"


def test_disagreements_round_trip_through_persistence(tmp_path):
    report = _report(tmp_path, mission_id="m-rt", state=MissionState.FAILED.value)
    assess_claim_vs_verified(
        report,
        dag_all_completed=True,
        verification=FakeVerification(verified=False),
        critic={"approved": False},
        repair_round=2,
    )
    restored = MissionReport.from_dict(report.to_dict())
    assert restored.agent_claim["dag_all_completed"] is True
    assert restored.verified_result["verified"] is False
    assert [d["kind"] for d in restored.disagreements] == ["claimed_complete_but_unverified"]
    assert restored.disagreements[0]["repair_round"] == 2


def test_repeated_repair_rounds_do_not_grow_the_record_without_bound(tmp_path):
    report = _report(tmp_path)
    for round_number in range(10):
        assess_claim_vs_verified(
            report,
            dag_all_completed=True,
            verification=FakeVerification(verified=False),
            critic={"approved": False},
            repair_round=round_number,
        )
    assert len(report.disagreements) <= 24
    # the most recent round is always retained
    assert report.disagreements[-1]["repair_round"] == 9
