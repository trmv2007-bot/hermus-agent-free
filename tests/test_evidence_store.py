"""Evidence storage: durable, ref-addressed, and honest about its source."""

from __future__ import annotations

import json

from core.evidence import EvidenceStore, hash_artifact
from core.verifier_registry import SOURCE_OBSERVED, SOURCE_UNCLASSIFIED, SOURCE_WORKER_REPORTED


def _store(tmp_path):
    return EvidenceStore(base_dir=tmp_path / "evidence")


def test_record_is_retrievable_by_reference(tmp_path):
    store = _store(tmp_path)
    entry = store.record(
        mission_id="m1",
        kind="behavioral",
        check="test_suite",
        summary="34 passed",
        payload={"output": "x" * 4000},
    )
    assert entry.id.startswith("ev_")
    assert store.get(entry.id).summary == "34 passed"
    assert [e.id for e in store.list("m1")] == [entry.id]


def test_a_repeated_check_stores_one_record(tmp_path):
    """Repair rounds re-run the same checks; the log must not imply more
    independent confirmation than there was."""
    store = _store(tmp_path)
    first = store.record(mission_id="m1", check="test_suite", summary="34 passed")
    again = store.record(mission_id="m1", check="test_suite", summary="34 passed")
    assert first.id == again.id
    assert len(store.list("m1")) == 1
    # a different outcome is a different record
    failed = store.record(mission_id="m1", check="test_suite", summary="2 failed")
    assert failed.id != first.id


def test_source_comes_from_the_verifier_vocabulary_not_the_caller(tmp_path):
    store = _store(tmp_path)
    observed = store.record(mission_id="m1", check="test_suite", summary="ran the suite")
    reported = store.record(mission_id="m1", check="runtime_output", summary="log says passed")
    unknown = store.record(mission_id="m1", check="brand_new_check", summary="?")
    assert (observed.source, reported.source, unknown.source) == (
        SOURCE_OBSERVED,
        SOURCE_WORKER_REPORTED,
        SOURCE_UNCLASSIFIED,
    )
    assert observed.grounded and not reported.grounded and not unknown.grounded


def test_recheck_looks_at_the_file_instead_of_the_claim(tmp_path):
    artifact = tmp_path / "app.py"
    artifact.write_text("print('a')\n", encoding="utf-8")
    store = _store(tmp_path)
    entry = store.record(mission_id="m1", check="ast_syntax", summary="parses", artifact_path=artifact)

    assert store.recheck(entry.id)["state"] == "unchanged"

    artifact.write_text("print('changed after the observation')\n", encoding="utf-8")
    assert store.recheck(entry.id)["state"] == "drifted"

    artifact.unlink()
    assert store.recheck(entry.id)["state"] == "missing"
    assert hash_artifact(artifact) == (None, None)


def test_digest_gives_references_and_not_the_payload(tmp_path):
    store = _store(tmp_path)
    store.record(mission_id="m1", kind="behavioral", check="test_suite", summary="34 passed", payload={"stdout": "NOISE" * 2000})
    store.record(mission_id="m1", kind="behavioral", check="runtime_output", summary="log claims success")
    text = store.digest("m1", max_chars=400)
    assert "1 observed, 1 worker-reported" in text
    assert "NOISE" not in text
    assert "context_read" in text
    assert len(text) <= 400


def test_digest_counts_across_the_whole_log_not_just_the_shown_slice(tmp_path):
    store = _store(tmp_path)
    for index in range(20):
        store.record(mission_id="m1", check="file_exists", summary=f"present {index}")
    text = store.digest("m1", limit=3)
    assert "20 observed" in text
    assert text.count("- ev_") == 3


def test_mission_id_cannot_escape_the_evidence_directory(tmp_path):
    store = _store(tmp_path)
    entry = store.record(mission_id="../../escape", check="file_exists", summary="x")
    path = store._path("../../escape")
    assert path.parent == store.base_dir.resolve() or store.base_dir in path.parents
    assert path.exists()
    assert entry.id


def test_record_many_accepts_verifier_evidence_directly(tmp_path):
    store = _store(tmp_path)
    entries = store.record_many(
        "m1",
        [
            {"type": "structural", "check": "ast_syntax", "file": str(tmp_path / "a.py"), "status": "valid"},
            {"type": "behavioral", "check": "test_suite", "status": "passed", "output": "34 passed"},
            "not-a-dict",
        ],
    )
    assert len(entries) == 2
    assert {e.check for e in entries} == {"ast_syntax", "test_suite"}
    assert all(e.grounded for e in entries)


def test_a_missing_log_reads_as_no_evidence_not_an_error(tmp_path):
    store = _store(tmp_path)
    assert store.list("nobody") == []
    assert store.digest("nobody") == ""
    assert store.get("ev_missing") is None
    assert store.recheck("ev_missing")["state"] == "unknown"


def test_records_round_trip_through_json(tmp_path):
    store = _store(tmp_path)
    entry = store.record(mission_id="m1", check="git_status", summary="clean", payload={"uncommitted_files": 0})
    raw = store._path("m1").read_text(encoding="utf-8").strip()
    assert json.loads(raw)["id"] == entry.id


def test_a_run_mission_files_every_item_its_report_carries(tmp_path):
    """The references must cover the whole evidence list, not one slice of it."""
    from core.mission import MissionEngine

    def executor(goal, ctx):
        return {"success": True, "output": f"Output for: {goal}", "evidence": [{"step": goal, "status": "ok"}]}

    storage = tmp_path / "missions"
    storage.mkdir()
    engine = MissionEngine(executor=executor, storage_dir=storage)
    report = engine.start_mission(
        goal="Build Python microservice",
        requirements=["Create service"],
        domain="generic",
        subgoals=["Setup", "Implement", "Test"],
        budget_steps=10,
    )
    assert report.evidence, "the run produced evidence"
    assert len(report.evidence_refs) == len(report.evidence)
    assert all(engine.evidence.get(ref) is not None for ref in report.evidence_refs)
    # scoped to the engine's own storage, so a test engine cannot write into the
    # real workspace's evidence log
    assert engine.evidence.base_dir == storage / "evidence"


def test_completion_does_not_imply_verification(tmp_path):
    """Pinned on a real run: the lifecycle said completed, the outcome says less.

    The generic verifier's only check scans the text the worker handed over, so
    nothing was observed independently and the mission must not read as verified.
    """
    from core.mission import MissionEngine

    def executor(goal, ctx):
        return {"success": True, "output": f"Output for: {goal}", "evidence": [{"step": goal, "status": "ok"}]}

    engine = MissionEngine(executor=executor, storage_dir=tmp_path / "missions")
    report = engine.start_mission(
        goal="Build Python microservice",
        requirements=["Create service"],
        domain="generic",
        subgoals=["Setup", "Implement"],
        budget_steps=10,
    )
    assert report.state == "completed"
    assert report.verified_result["provenance"]["grounded"] is False
    assert report.outcome_state != "verified"


def test_evidence_is_reachable_through_the_on_demand_tier(tmp_path, monkeypatch):
    import core.evidence as evidence_module
    from core.context.ondemand import TOPICS, read_context

    assert "evidence" in TOPICS
    store = _store(tmp_path)
    entry = store.record(mission_id="m9", kind="behavioral", check="test_suite", summary="34 passed", payload={"stdout": "ok"})
    monkeypatch.setattr(evidence_module, "evidence_store", store)

    by_ref = read_context("evidence", entry.id)
    assert by_ref["success"] and by_ref["record"]["check"] == "test_suite"
    assert by_ref["recheck"]["state"] == "no_artifact"

    by_mission = read_context("evidence", "m9")
    assert "1 observed" in by_mission["text"] and entry.id in by_mission["text"]

    assert read_context("evidence", "ev_nope")["success"] and not read_context("evidence", "ev_nope")["text"]
