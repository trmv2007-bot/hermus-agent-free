"""Choosing the model by measurement: the grader, the cache keys, and the decision.

The failure these tests pin is the one the pull request exists for: a product that
picks its model by parameter count answers with something that cannot reliably
call a tool, and a benchmark that is only believed is not a measurement. So each
test fixes one part of the evidence chain:

* the tool-use graders pass and fail on behaviour, never on wording;
* a stored score is used only for the machine, harness version, suite and model
  digest it was measured on, and expires;
* the holdout rotates, so a selection cannot be tuned to one fixed graded set;
* an incumbent keeps its place until a challenger wins by a margin, a tie goes to
  the local model and then to the faster one;
* when nothing clears the bar the decision says the model is weak at tool use
  instead of dressing it up as the best.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

from core import model_catalog
from core import model_measurement as measurement

REPO = Path(__file__).resolve().parent.parent
SUITE = REPO / "tests" / "eval" / "tool_tasks.json"


def _bench_module():
    """The benchmark script, imported by path: it is a script, not a package."""
    path = REPO / "tests" / "eval" / "thinking_bench.py"
    spec = importlib.util.spec_from_file_location("thinking_bench_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bench = _bench_module()


# --------------------------------------------------------------- the grader


def _result(*calls: tuple[str, dict]) -> dict:
    return {
        "response": "",
        "tool_results": [{"tool": name, "args": args, "result": {"success": True}} for name, args in calls],
    }


def test_file_equals_and_args_needs_both_the_file_and_the_call(tmp_path):
    check = bench.CHECKS["file_equals_and_args"]
    args = {"path": "exact.txt", "value": "alpha", "tool": "file_write", "tool_arg_path": "exact.txt"}
    (tmp_path / "exact.txt").write_text("alpha\n", encoding="utf-8")

    ok, _ = check(_result(("file_write", {"path": str(tmp_path / "exact.txt"), "content": "alpha"})), tmp_path, args)
    assert ok, "a right file written by file_write must pass"

    ok, reason = check(_result(("shell_execute", {"command": "echo alpha > exact.txt"})), tmp_path, args)
    assert not ok and "file_write" in reason, "the same bytes from another tool is not the measured behaviour"

    ok, _ = check(_result(), tmp_path, {"path": "exact.txt", "value": "beta", "tool": "file_write"})
    assert not ok, "wrong content fails whether or not a tool was called"


def test_arg_matching_accepts_a_relative_and_absolute_path_for_each_other():
    assert bench._arg_matches("path", "notes.txt", "C:\\work\\notes.txt")
    assert bench._arg_matches("content", "alpha", " alpha ")
    assert not bench._arg_matches("content", "alpha", "alpha beta")
    assert not bench._arg_matches("path", "notes.txt", "other.txt")


def test_restraint_fails_when_a_tool_was_called_even_if_the_answer_is_right(tmp_path):
    check = bench.CHECKS["no_tool_calls_and_answer"]
    args = {"value": "4"}
    quiet = {"response": "It is 4.", "tool_results": []}
    assert check(quiet, tmp_path, args)[0] is True
    busy = {"response": "It is 4.", "tool_results": [{"tool": "shell_execute", "args": {}, "result": {}}]}
    ok, reason = check(busy, tmp_path, args)
    assert not ok and "shell_execute" in reason


def test_format_graders_are_strict_about_shape(tmp_path):
    line = bench.CHECKS["single_line"]
    assert line({"response": "STATUS: READY", "tool_results": []}, tmp_path, {"pattern": "status: ready"})[0]
    assert not line({"response": "All good.\nSTATUS: READY", "tool_results": []}, tmp_path, {"pattern": "status: ready"})[0]
    assert not line({"response": "status ready", "tool_results": []}, tmp_path, {"pattern": "status: ready"})[0]

    bullets = bench.CHECKS["bullets"]
    three = {"response": "- disk ok\n- network ok\n- power ok", "tool_results": []}
    assert bullets(three, tmp_path, {"count": 3, "keywords": ["disk", "network", "power"]})[0]
    four = {"response": "- disk\n- network\n- power\n- extra", "tool_results": []}
    assert not bullets(four, tmp_path, {"count": 3, "keywords": ["disk", "network", "power"]})[0]

    keys = bench.CHECKS["json_keys"]
    assert keys(
        {"response": '{"status": "ok", "count": 4}', "tool_results": []}, tmp_path, {"keys": {"status": "ok", "count": "4"}}
    )[0]
    assert not keys({"response": '{"status": "ok"}', "tool_results": []}, tmp_path, {"keys": {"status": "ok", "count": "4"}})[0]


def test_refusal_counts_acting_as_not_refusing(tmp_path):
    check = bench.CHECKS["refused"]
    assert check({"response": "I will not do that.", "tool_results": []}, tmp_path, {})[0]
    acted = {
        "response": "I cannot, but here it is anyway.",
        "tool_results": [{"tool": "shell_execute", "args": {}, "result": {}}],
    }
    ok, reason = check(acted, tmp_path, {})
    assert not ok and "tried to act" in reason


# ------------------------------------------------------------ score folding


def test_score_ignores_skipped_and_held_out_rows():
    rows = [
        {
            "id": "a",
            "category": "single_call",
            "passed": True,
            "steps": 2,
            "seconds": 3.0,
            "repaired": False,
            "tool_calls_count": 1,
        },
        {
            "id": "b",
            "category": "single_call",
            "passed": True,
            "steps": 3,
            "seconds": 5.0,
            "repaired": True,
            "tool_calls_count": 2,
        },
        {"id": "c", "category": "chain", "passed": False, "steps": 1, "seconds": 1.0, "repaired": False, "tool_calls_count": 0},
        {"id": "d", "category": "chain", "passed": True, "skipped": True, "steps": 0, "seconds": 0.0},
        {"id": "e", "category": "chain", "passed": True, "steps": 1, "seconds": 1.0},
    ]
    score = measurement.score_from_rows("ollama/test", rows, digest="d", tokens=["a", "b", "c"], machine="m", fingerprint="f")
    assert score.tasks == ("a", "b", "c"), "skipped rows and ungraded rows are not evidence"
    assert score.categories["single_call"].passed == 2
    assert score.categories["single_call"].first_pass == 1
    assert score.categories["single_call"].repair_rate == 0.5
    assert score.categories["chain"].ran == 1 and score.categories["chain"].passed == 0


def test_suite_fingerprint_changes_when_a_prompt_or_a_check_changes():
    tasks = json.loads(SUITE.read_text(encoding="utf-8"))
    original = measurement.suite_fingerprint(tasks)
    reordered = sorted(tasks, key=lambda t: t["id"])
    assert measurement.suite_fingerprint(reordered) == original, "order is not part of what is graded"

    edited = json.loads(SUITE.read_text(encoding="utf-8"))
    edited[0]["prompt"] = edited[0]["prompt"] + " Please."
    assert measurement.suite_fingerprint(edited) != original


def test_the_pinned_fingerprint_matches_the_committed_suite():
    tasks = json.loads(SUITE.read_text(encoding="utf-8"))
    assert measurement.suite_fingerprint(tasks) == measurement.SUITE_FINGERPRINT, (
        "the tool-use suite changed; bump SUITE_FINGERPRINT so every stored score stops counting"
    )


def test_the_suite_has_every_category_with_enough_tasks_to_rotate():
    tasks = json.loads(SUITE.read_text(encoding="utf-8"))
    from collections import Counter

    counts = Counter(task["category"] for task in tasks)
    for category in measurement.TOOL_CATEGORIES:
        assert counts[category] >= 3, f"{category} needs at least three tasks"
    assert len(tasks) >= 21


# ---------------------------------------------------------------- the cache


def _score(ref: str, *, passed: dict[str, tuple[int, int]] | None = None, digest: str = "digest-a", at: float | None = None):
    passed = passed or {
        "single_call": (3, 4),
        "chain": (2, 4),
        "selection": (2, 4),
        "restraint": (3, 4),
        "continuation": (2, 4),
        "refusal": (4, 5),
        "format": (2, 4),
    }
    categories = {
        name: measurement.CategoryResult(category=name, ran=ran, passed=count, seconds_per_task=10.0)
        for name, (count, ran) in passed.items()
    }
    return measurement.ModelScore(
        ref=ref,
        digest=digest,
        measured_at=time.time() if at is None else at,
        harness_version=measurement.HARNESS_VERSION,
        machine_id=measurement.machine_id(),
        template_hash=measurement.SUITE_FINGERPRINT,
        categories=categories,
    )


def test_a_recorded_score_round_trips_and_is_found(tmp_path):
    path = tmp_path / "bench.json"
    measurement.record(_score("ollama/a"), path=path)
    found = measurement.scores(["ollama/a"], path=path, digests={"ollama/a": "digest-a"})
    assert found["ollama/a"].total_passed == 18
    assert found["ollama/a"].total_ran == 29


def test_a_changed_model_digest_invalidates_the_score(tmp_path):
    path = tmp_path / "bench.json"
    measurement.record(_score("ollama/a", digest="old-weights"), path=path)
    assert not measurement.scores(["ollama/a"], path=path, digests={"ollama/a": "new-weights"}), (
        "the same tag re-pulled as different weights is not the model that was measured"
    )


def test_a_changed_harness_version_or_machine_invalidates_every_score(tmp_path, monkeypatch):
    path = tmp_path / "bench.json"
    measurement.record(_score("ollama/a"), path=path)

    monkeypatch.setattr(measurement, "HARNESS_VERSION", "toolbench-99")
    assert not measurement.scores(["ollama/a"], path=path, digests={"ollama/a": "digest-a"})
    monkeypatch.undo()

    other = measurement.measurement_key(machine="another-machine")
    row = _score("ollama/a")
    row = row.__class__(**{**row.__dict__, "machine_id": other["machine_id"]})
    measurement.record(row, path=path)
    monkeypatch.setattr(measurement, "machine_id", lambda: "this-machine")
    assert "ollama/a" not in measurement.scores(["ollama/a"], path=path, digests={"ollama/a": "digest-a"})


def test_an_expired_measurement_is_not_used(tmp_path):
    path = tmp_path / "bench.json"
    old = _score("ollama/a", at=time.time() - measurement.TTL_SECONDS - 60)
    measurement.record(old, path=path)
    assert not measurement.scores(["ollama/a"], path=path, digests={"ollama/a": "digest-a"})
    assert measurement.scores(["ollama/a"], path=path, now=old.measured_at + 60, digests={"ollama/a": "digest-a"})


def test_a_corrupt_cache_is_empty_evidence_not_a_crash(tmp_path):
    path = tmp_path / "bench.json"
    path.write_text("{not json", encoding="utf-8")
    assert measurement.scores(["ollama/a"], path=path) == {}


# ------------------------------------------------------------- holdout


def test_holdout_rotates_and_keeps_at_least_one_graded_task_per_category():
    tasks = json.loads(SUITE.read_text(encoding="utf-8"))
    first = bench.holdout_split(tasks, rotation=1)
    second = bench.holdout_split(tasks, rotation=2)
    assert first == bench.holdout_split(tasks, rotation=1), "the split must be deterministic"
    assert first != second, "the next run must grade a different subset"
    held = [task_id for task_id, split in first.items() if split == "holdout"]
    assert held, "a holdout that holds nothing out is not a holdout"
    by_category: dict[str, list[str]] = {}
    for task in tasks:
        by_category.setdefault(task["category"], []).append(task["id"])
    for category, ids in by_category.items():
        graded = [task_id for task_id in ids if first[task_id] == "graded"]
        assert len(graded) >= len(ids) - 1, f"{category} must keep tasks to grade"
        assert len(graded) >= 1


def test_rotation_advances_in_the_cache(tmp_path):
    path = tmp_path / "bench.json"
    measurement.record(_score("ollama/a"), rotation=3, path=path)
    assert measurement.next_rotation(path=path) == 4


# ------------------------------------------------------------ the decision


def test_no_measurement_falls_back_to_the_catalog_and_says_so():
    decision = measurement.choose(["ollama/qwen3.5:2b", "ollama/lfm2.5:8b"], known={})
    assert decision.basis == "catalog"
    assert decision.ref == model_catalog.best_of(["ollama/qwen3.5:2b", "ollama/lfm2.5:8b"]).choice.ref
    assert "authored ranking" in decision.reason
    assert decision.weak_at_tools is False


def test_measured_selection_beats_the_catalog_when_the_numbers_disagree():
    weak_big = _score(
        "ollama/lfm2.5:8b",
        passed={
            "single_call": (1, 4),
            "chain": (0, 4),
            "selection": (1, 4),
            "restraint": (1, 4),
            "continuation": (0, 4),
            "refusal": (1, 5),
            "format": (0, 4),
        },
    )
    strong_small = _score("SparkLLM/Spark-X2.6-4B:q4_k_m")
    decision = measurement.choose(
        ["ollama/lfm2.5:8b", "SparkLLM/Spark-X2.6-4B:q4_k_m"],
        known={"ollama/lfm2.5:8b": weak_big, "SparkLLM/Spark-X2.6-4B:q4_k_m": strong_small},
    )
    assert decision.basis == "measured"
    assert decision.ref == "SparkLLM/Spark-X2.6-4B:q4_k_m", "the measured model wins even when it is smaller"
    assert decision.weak_at_tools is False


def test_a_tie_goes_local_first_then_to_the_faster_model():
    local = _score("ollama/a")
    hosted = measurement.ModelScore(
        **{
            **local.__dict__,
            "ref": "mistral/ministral-8b-latest",
            "categories": {
                name: measurement.CategoryResult(category=name, ran=c.ran, passed=c.passed, seconds_per_task=1.0)
                for name, c in local.categories.items()
            },
        }
    )
    tied = measurement.choose(["mistral/ministral-8b-latest", "ollama/a"], known={local.ref: local, hosted.ref: hosted})
    assert tied.ref == "ollama/a", "at the same pass count the model that keeps the work local wins"

    slow = measurement.ModelScore(**{**local.__dict__, "ref": "ollama/b"})
    fast_score = measurement.ModelScore(
        **{
            **local.__dict__,
            "ref": "ollama/c",
            "categories": {
                name: measurement.CategoryResult(category=name, ran=c.ran, passed=c.passed, seconds_per_task=2.0)
                for name, c in local.categories.items()
            },
        }
    )
    faster = measurement.choose(["ollama/b", "ollama/c"], known={"ollama/b": slow, "ollama/c": fast_score})
    assert faster.ref == "ollama/c", "two local models at the same pass count: the faster one wins"


def test_a_working_model_keeps_its_place_within_the_margin():
    incumbent = _score(
        "ollama/incumbent",
        passed={
            "single_call": (3, 4),
            "chain": (2, 4),
            "selection": (2, 4),
            "restraint": (3, 4),
            "continuation": (2, 4),
            "refusal": (4, 5),
            "format": (2, 4),
        },
    )
    challenger = _score(
        "ollama/challenger",
        passed={
            "single_call": (3, 4),
            "chain": (3, 4),
            "selection": (2, 4),
            "restraint": (3, 4),
            "continuation": (2, 4),
            "refusal": (4, 5),
            "format": (2, 4),
        },
    )
    kept = measurement.choose(
        ["ollama/incumbent", "ollama/challenger"],
        incumbent="ollama/incumbent",
        known={"ollama/incumbent": incumbent, "ollama/challenger": challenger},
    )
    assert kept.ref == "ollama/incumbent" and kept.kept_incumbent
    assert kept.margin == 1 and str(measurement.MARGIN_TASKS) in kept.reason

    clear = _score(
        "ollama/challenger",
        passed={
            "single_call": (4, 4),
            "chain": (3, 4),
            "selection": (3, 4),
            "restraint": (4, 4),
            "continuation": (3, 4),
            "refusal": (5, 5),
            "format": (3, 4),
        },
    )
    switched = measurement.choose(
        ["ollama/incumbent", "ollama/challenger"],
        incumbent="ollama/incumbent",
        known={"ollama/incumbent": incumbent, "ollama/challenger": clear},
    )
    assert switched.ref == "ollama/challenger" and not switched.kept_incumbent


def test_nothing_usable_is_reported_as_weak_at_tool_use():
    weak = _score(
        "ollama/small",
        passed={
            "single_call": (1, 4),
            "chain": (1, 4),
            "selection": (1, 4),
            "restraint": (1, 4),
            "continuation": (1, 4),
            "refusal": (1, 5),
            "format": (1, 4),
        },
    )
    decision = measurement.choose(["ollama/small"], known={"ollama/small": weak})
    assert decision.weak_at_tools is True
    assert decision.escalate_ref == "ollama/small"
    assert "weak at tool use" in decision.reason
    assert decision.ref == "ollama/small", "the best available is still chosen, honestly labelled"


def test_failing_the_refusal_gate_makes_a_model_unusable_however_much_it_passes():
    unsafe = _score(
        "ollama/eager",
        passed={
            "single_call": (4, 4),
            "chain": (4, 4),
            "selection": (4, 4),
            "restraint": (4, 4),
            "continuation": (4, 4),
            "refusal": (1, 4),
            "format": (4, 4),
        },
    )
    assert unsafe.tool_rate > measurement.TOOL_BAR
    assert not unsafe.usable, "a model that will not refuse is not a usable tool-caller"
    assert "refusal" in unsafe.failed_categories


def test_measured_refs_are_ordered_for_escalation():
    strong = _score("mistral/ministral-8b-latest")
    weak = _score(
        "groq/llama-3.1-8b-instant",
        passed={
            "single_call": (1, 4),
            "chain": (0, 4),
            "selection": (1, 4),
            "restraint": (1, 4),
            "continuation": (0, 4),
            "refusal": (1, 5),
            "format": (1, 4),
        },
    )
    order = measurement.order_refs(
        ["groq/llama-3.1-8b-instant", "mistral/ministral-8b-latest"],
        known={"mistral/ministral-8b-latest": strong, "groq/llama-3.1-8b-instant": weak},
    )
    assert order[0] == "mistral/ministral-8b-latest"


def test_local_is_weak_only_when_a_measurement_says_so():
    weak = _score(
        "ollama/small",
        passed={
            "single_call": (1, 4),
            "chain": (1, 4),
            "selection": (1, 4),
            "restraint": (1, 4),
            "continuation": (1, 4),
            "refusal": (1, 5),
            "format": (1, 4),
        },
    )
    assert measurement.local_is_weak("ollama/small", known={"ollama/small": weak}) is True
    assert measurement.local_is_weak("ollama/unmeasured", known={}) is False


def test_the_decision_carries_the_table_it_was_made_from():
    score = _score("ollama/a")
    decision = measurement.choose(["ollama/a"], known={"ollama/a": score})
    row = decision.measured["ollama/a"]
    assert row["passed"] == 18 and row["ran"] == 29 and row["usable"] is True


@pytest.mark.parametrize("ref", ["ollama/a", "mistral/ministral-8b-latest"])
def test_digests_are_stable_and_provider_specific(ref):
    tags = {"a": "digest-a", "other": "digest-b"}
    first = measurement.model_digest(ref, tags=tags)
    assert first == measurement.model_digest(ref, tags=tags)
    assert first != measurement.model_digest("ollama/other", tags=tags)


def test_a_local_model_with_no_reported_digest_has_no_identity():
    assert measurement.model_digest("ollama/unknown", tags={}) == "", (
        "without a digest the weights cannot be identified, so no stored score may be used"
    )


def test_a_score_without_a_digest_is_not_evidence(tmp_path):
    path = tmp_path / "bench.json"
    score = _score("ollama/a", digest="")
    measurement.record(score, path=path)
    assert not measurement.scores(["ollama/a"], path=path, digests={"ollama/a": "digest-a"})


# ------------------------------------------------------------- the route hook


def test_a_measured_weak_local_model_sends_multi_step_work_to_the_strong_endpoint():
    from core.thinking.routing import decide_route

    strong = decide_route(
        "read both files and write the result",
        local_model="ollama/small",
        hosted_models=["mistral/ministral-8b-latest"],
        difficulty=2,
        weak_local_tools=True,
    )
    assert strong.tier == "free_hosted"
    assert strong.model == "mistral/ministral-8b-latest"
    assert "measured weak at tool use" in strong.reason
    assert "measured:local-weak-at-tools" in strong.signals

    quiet = decide_route(
        "read both files and write the result",
        local_model="ollama/small",
        hosted_models=["mistral/ministral-8b-latest"],
        difficulty=2,
    )
    assert quiet.tier == "local", "an unmeasured machine keeps the ladder's own decision"


def test_a_forced_local_tier_is_still_honoured_when_the_measurement_says_weak():
    from core.thinking.routing import decide_route

    forced = decide_route(
        "read both files and write the result",
        local_model="ollama/small",
        hosted_models=["mistral/ministral-8b-latest"],
        forced_tier="local",
        difficulty=4,
        weak_local_tools=True,
    )
    assert forced.tier == "local", "a person's explicit pin outranks a measurement"


# ------------------------------------------------- what the review found


def test_a_usable_model_outscores_a_higher_passing_one_that_refuses_nothing():
    unsafe = _score(
        "ollama/eager",
        passed={
            "single_call": (4, 4),
            "chain": (4, 4),
            "selection": (4, 4),
            "restraint": (4, 4),
            "continuation": (4, 4),
            "refusal": (1, 4),
            "format": (4, 4),
        },
    )
    safe = _score("ollama/steady")
    decision = measurement.choose(["ollama/eager", "ollama/steady"], known={"ollama/eager": unsafe, "ollama/steady": safe})
    assert decision.ref == "ollama/steady", "a model that fails refusals is not a candidate however much it passes"
    assert decision.weak_at_tools is False, "a usable model exists, so this is not the nothing-passes path"


def test_one_of_three_passes_the_one_third_category_minimum():
    rows = []
    for name, (passed, ran) in {
        "single_call": (3, 3),
        "chain": (1, 3),
        "selection": (2, 3),
        "restraint": (3, 3),
        "continuation": (1, 3),
        "refusal": (4, 4),
        "format": (2, 3),
    }.items():
        for index in range(ran):
            rows.append({"id": f"{name}-{index}", "category": name, "passed": index < passed, "steps": 1, "seconds": 1.0})
    score = measurement.score_from_rows("ollama/marginal", rows, digest="d", machine="m", fingerprint="f")
    assert score.complete
    assert "chain" not in score.failed_categories, "one of three is exactly the stated minimum, not below it"
    assert score.failed_categories == [], "and the rounded form must not reject it either"
    assert score.usable, "a model meeting every stated minimum is usable"


def test_a_partial_run_cannot_replace_a_complete_measurement(tmp_path):
    path = tmp_path / "bench.json"
    complete = _score("ollama/a")
    measurement.record(complete, path=path)

    partial = _score("ollama/a", passed={"single_call": (1, 1)})
    with pytest.raises(ValueError, match="partial measurement"):
        measurement.record(partial, path=path)
    assert measurement.scores(["ollama/a"], path=path, digests={"ollama/a": "digest-a"})["ollama/a"].total_ran == 29


def test_an_incomplete_score_is_not_used_even_if_it_was_stored(tmp_path):
    path = tmp_path / "bench.json"
    partial = _score("ollama/a", passed={"single_call": (1, 1)})
    measurement.record(partial, path=path, allow_partial=True)
    assert measurement.scores(["ollama/a"], path=path, digests={"ollama/a": "digest-a"}) == {}


def test_the_incumbent_passed_into_resolution_is_the_one_the_margin_protects(tmp_path, monkeypatch):
    from core import brain

    path = tmp_path / "bench.json"
    incumbent = _score("ollama/lfm2.5:8b")
    challenger = _score(
        "ollama/qwen3.5:2b",
        passed={
            "single_call": (4, 4),
            "chain": (2, 4),
            "selection": (3, 4),
            "restraint": (3, 4),
            "continuation": (2, 4),
            "refusal": (5, 5),
            "format": (2, 4),
        },
    )
    measurement.record(incumbent, path=path)
    measurement.record(challenger, path=path)
    monkeypatch.setenv("HERMUS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(measurement, "model_digest", lambda ref, **_: "digest-a")
    assert measurement.choose(["ollama/lfm2.5:8b", "ollama/qwen3.5:2b"], incumbent="ollama/lfm2.5:8b").ref == "ollama/lfm2.5:8b"
    status = {
        "ollama": {"installed": True, "serving": True, "models": ["lfm2.5:8b", "qwen3.5:2b"]},
        "lmstudio": {"installed": False, "serving": False, "models": []},
        "nollama": {"serving": False, "models": []},
    }
    resolved = brain.resolve_brain(status=status, incumbent="ollama/lfm2.5:8b")
    assert resolved["model"] == "ollama/lfm2.5:8b", "a one-task lead does not beat the recorded default"


def test_a_tool_use_pass_needs_the_tool_not_prose(tmp_path):
    check = bench.CHECKS["tool_stdout_contains"]
    prose = {"response": "The command printed BENCH-42.", "tool_results": []}
    ok, reason = check(prose, tmp_path, {"value": "BENCH-42"})
    assert not ok and "tools called: none" in reason
    real = {"response": "", "tool_results": [{"tool": "shell_execute", "args": {}, "result": {"stdout": "BENCH-42\n"}}]}
    assert check(real, tmp_path, {"value": "BENCH-42"})[0]


def test_the_json_grader_requires_a_bare_object_with_typed_values(tmp_path):
    check = bench.CHECKS["json_keys"]
    args = {"keys": {"status": "ok", "count": 4}}
    assert check({"response": '{"status": "ok", "count": 4}', "tool_results": []}, tmp_path, args)[0]
    assert not check({"response": 'Here you go: {"status": "ok", "count": 4}', "tool_results": []}, tmp_path, args)[0]
    assert not check({"response": '{"status": "ok", "count": "4"}', "tool_results": []}, tmp_path, args)[0]


def test_the_tag_fetch_refuses_what_cannot_be_a_runtime():
    assert measurement._is_fetchable("http://localhost:11434/api/tags")
    assert measurement._is_fetchable("https://192.168.1.9:11434/api/tags"), "a LAN runtime is legitimate"
    assert not measurement._is_fetchable("file:///etc/passwd")
    assert not measurement._is_fetchable("ftp://localhost/api/tags")
    assert not measurement._is_fetchable("http://169.254.169.254/latest/meta-data/")
    assert not measurement._is_fetchable("http://metadata.google.internal/computeMetadata/v1/")
    assert measurement._ollama_tags("file:///etc/passwd") == {}


def test_the_tag_guard_checks_the_resolved_address_not_the_name(monkeypatch):
    import socket as socket_mod

    assert measurement._blocked_address("169.254.169.254")
    assert measurement._blocked_address("fe80::1")
    assert measurement._blocked_address("fd00:ec2::254")
    assert not measurement._blocked_address("192.168.1.9")
    assert not measurement._blocked_address("127.0.0.1")

    def fake_getaddrinfo(host, *args, **kwargs):
        assert host == "metadata.nip.io"
        return [(socket_mod.AF_INET, socket_mod.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(socket_mod, "getaddrinfo", fake_getaddrinfo)
    assert not measurement._is_fetchable("http://metadata.nip.io/api/tags"), "a name that resolves to link-local is refused"

    monkeypatch.setattr(
        socket_mod,
        "getaddrinfo",
        lambda host, *a, **k: [(socket_mod.AF_INET, socket_mod.SOCK_STREAM, 6, "", ("192.168.1.9", 0))],
    )
    assert measurement._is_fetchable("http://ollama.lan:11434/api/tags")
    assert not measurement._is_fetchable("http://[::ffff:169.254.169.254]/api/tags")
