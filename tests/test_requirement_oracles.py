"""Requirements are checked one at a time, not inherited from the verdict."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.evidence import EvidenceRecord
from core.failure_recovery import RecoveryAction, classify, plan
from core.requirement_oracles import BREACHED, CLAIMED, SATISFIED, UNOBSERVED, apply, evaluate


@dataclass
class Req:
    id: str
    description: str = "d"
    satisfied: bool = False
    evidence: list[str] = field(default_factory=list)
    oracle: str = ""
    target: str = ""
    deadline_s: float | None = None
    status: str = "unobserved"
    verified_by: list[str] = field(default_factory=list)
    check_detail: str = ""


def _record(check: str, source: str, summary: str = "seen", rid: str = "ev_1") -> EvidenceRecord:
    return EvidenceRecord(id=rid, mission_id="m1", created_at="now", check=check, source=source, summary=summary)


def test_a_required_file_is_checked_on_disk(tmp_path):
    (tmp_path / "service.py").write_text("print(1)\n", encoding="utf-8")
    hit = evaluate(Req("r1", oracle="file_exists", target="service.py"), workspace_root=tmp_path)
    miss = evaluate(Req("r2", oracle="file_exists", target="ghost.py"), workspace_root=tmp_path)

    assert (hit.satisfied, hit.status) == (True, SATISFIED)
    assert (miss.satisfied, miss.status) == (False, BREACHED)
    assert "ghost.py" in miss.detail


def test_a_requirement_can_demand_content_not_just_a_file(tmp_path):
    (tmp_path / "app.py").write_text("def handler():\n    pass\n", encoding="utf-8")
    yes = evaluate(Req("r", oracle="file_exists", target="app.py::def handler"), workspace_root=tmp_path)
    no = evaluate(Req("r", oracle="file_exists", target="app.py::def missing"), workspace_root=tmp_path)

    assert yes.satisfied and not no.satisfied
    assert no.status == BREACHED and "does not contain" in no.detail


def test_an_empty_file_does_not_satisfy_a_nonempty_oracle(tmp_path):
    (tmp_path / "notes.md").write_text("", encoding="utf-8")
    outcome = evaluate(Req("r", oracle="file_nonempty", target="notes.md"), workspace_root=tmp_path)

    assert not outcome.satisfied and outcome.status == BREACHED
    assert "empty" in outcome.detail


def test_a_deliverable_must_be_one_the_mission_actually_reported(tmp_path):
    (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")
    outcome = evaluate(Req("r", oracle="artifact_present", target="real"), workspace_root=tmp_path, artifacts=[str(tmp_path / "real.py")])
    absent = evaluate(Req("r", oracle="artifact_present", target="imaginary"), workspace_root=tmp_path, artifacts=[str(tmp_path / "real.py")])

    assert outcome.satisfied and absent.status == BREACHED


def test_only_observed_evidence_counts_as_observed():
    grounded = [_record("test_suite", "observed", "34 passed", "ev_a")]
    quoted = [_record("runtime_output", "worker-reported", "the log says passed", "ev_b")]

    assert evaluate(Req("r", oracle="observed_evidence", target="passed"), workspace_root=Path("."), evidence_records=grounded).satisfied
    unmet = evaluate(Req("r", oracle="observed_evidence", target="passed"), workspace_root=Path("."), evidence_records=quoted)
    assert not unmet.satisfied and unmet.status == BREACHED
    assert "no observed evidence" in unmet.detail
    # with no target to match, the absence of anything observed is the finding
    broad = evaluate(Req("r", oracle="observed_evidence"), workspace_root=Path("."), evidence_records=quoted)
    assert not broad.satisfied and "independently" in broad.detail


def test_a_requirement_with_no_oracle_is_reported_as_claimed(tmp_path):
    outcome = evaluate(Req("r", oracle="", target=""), workspace_root=tmp_path)

    assert outcome.status == CLAIMED and not outcome.satisfied
    assert "no oracle" in outcome.detail


def test_an_unknown_oracle_says_what_exists():
    outcome = evaluate(Req("r", oracle="telepathy", target="x"), workspace_root=Path("."))

    assert outcome.status == UNOBSERVED
    assert "file_exists" in outcome.detail


def test_a_closed_window_is_part_of_the_breach(tmp_path):
    outcome = evaluate(Req("r", oracle="file_exists", target="late.py", deadline_s=30), workspace_root=tmp_path, elapsed_s=95)

    assert outcome.status == BREACHED and "95s" in outcome.detail and "30s" in outcome.detail


def test_apply_records_the_check_on_every_requirement(tmp_path):
    (tmp_path / "ok.py").write_text("y = 2\n", encoding="utf-8")

    class Report:
        artifacts: list[str] = []
        requirements = [
            Req("good", oracle="file_exists", target="ok.py"),
            Req("bad", oracle="file_exists", target="missing.py"),
            Req("vague"),
        ]

    report = Report()
    buckets = apply(report, workspace_root=tmp_path)

    assert buckets[SATISFIED] == ["good"]
    assert buckets[BREACHED] == ["bad"]
    assert buckets[CLAIMED] == ["vague"]
    assert report.requirements[0].satisfied is True
    assert "ok.py" in report.requirements[0].evidence[0]
    assert report.requirements[1].satisfied is False
    assert report.requirements[2].status == CLAIMED
    assert report.requirements[0].verified_by


def test_a_breach_diagnoses_as_a_fixable_requirement_problem():
    assert classify(breached_requirements=["r2"], verified=True) == "requirement_breach"
    actions = plan("requirement_breach")
    assert actions[0] is RecoveryAction.PLAN_REPAIR
    assert RecoveryAction.REPLAN in actions


def test_a_breached_requirement_blocks_a_otherwise_clean_mission(tmp_path):
    """The verdict passing overall is not permission to mark everything done."""
    from core.mission import MissionEngine

    def executor(node, ctx):
        return {"success": True, "output": "wrote everything the plan asked for"}

    engine = MissionEngine(executor=executor, storage_dir=tmp_path / "missions")
    report = engine.start_mission(
        "ship the service",
        requirements=[{"description": "service.py must exist", "oracle": "file_exists", "target": "definitely_not_written_by_this_test.py"}],
        budget_steps=6,
        max_repairs=1,
    )

    assert report.state != "completed"
    requirement = report.requirements[0]
    assert requirement.satisfied is False
    assert requirement.status == BREACHED
    assert any(entry.get("failure_class") == "requirement_breach" for entry in report.repair_history)


def test_a_satisfied_oracle_does_not_block_completion(tmp_path):
    """Same engine, same verdict — the difference is that the check passes.

    Requirement targets resolve against the mission workspace, so the file is
    written there and removed afterwards.
    """
    from core.mission import MissionEngine
    from core.workspace import workspace

    name = "hermus_oracle_probe_service.py"
    probe = Path(workspace.root) / name
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("def handler():\n    return 'ok'\n", encoding="utf-8")

    try:
        engine = MissionEngine(
            executor=lambda node, ctx: {"success": True, "output": "created the service module", "evidence": [{"check": "ok", "status": "passed"}]},
            storage_dir=tmp_path / "missions",
        )
        report = engine.start_mission(
            "ship the service",
            requirements=[{"description": "the handler exists", "oracle": "file_exists", "target": f"{name}::def handler"}],
            budget_steps=20,
            max_repairs=2,
        )

        assert report.requirements[0].status == SATISFIED
        assert report.requirements[0].satisfied is True
        assert not any(entry.get("failure_class") == "requirement_breach" for entry in report.repair_history)
        # A checked-and-met requirement is not what stopped the mission. If it did
        # not complete, it is the critic panel: with no model configured it
        # withholds approval, so this test claims nothing about completion.
        if report.state != "completed":
            assert "verified_but_critic_rejected" in [item["kind"] for item in report.disagreements]
    finally:
        probe.unlink(missing_ok=True)
