"""A worker's claim is not the system's verified result.

The engine used to collapse "every node said DONE" and "the verifier confirmed
the outcome" into a single boolean and discard the inputs. These tests pin that
both survive, that the gap between them is recorded as a measurement, and that
the record persists across a restart.
"""

from __future__ import annotations

from pathlib import Path

from core.mission import MissionReport, MissionState, assess_claim_vs_verified


class FakeVerification:
    def __init__(self, *, verified=True, score=0.9, errors=None, evidence=None, structural=True, behavioral=True):
        self.verified = verified
        self.score = score
        self.errors = errors or []
        self.evidence = evidence if evidence is not None else [{"kind": "file", "path": "a.py"}]
        self.structural_verified = structural
        self.behavioral_verified = behavioral


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
