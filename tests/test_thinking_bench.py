"""The benchmark's own logic: the task set is valid and every check can fail.

The benchmark measures a model, which is not a unit test. What *is* testable
here is the harness: every task names a check that exists, every check rejects
the shape of answer it is meant to reject, and the report's arithmetic holds.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from core.thinking.routing import FREE_HOSTED_KEY_ENV
from tests.eval import thinking_bench as bench

TASKS = json.loads(bench.TASKS_FILE.read_text(encoding="utf-8"))


def test_the_task_set_covers_every_kind_the_brief_names():
    kinds = {t["kind"] for t in TASKS}
    assert {"file", "shell", "web", "multi", "refusal", "answer"} <= kinds
    assert len(TASKS) >= 12
    assert len({t["id"] for t in TASKS}) == len(TASKS), "task ids must be unique"


def test_every_task_names_a_check_that_exists():
    for task in TASKS:
        assert task["check"] in bench.CHECKS, f"{task['id']} names unknown check {task['check']}"
        assert task["prompt"].strip(), f"{task['id']} has no prompt"


def test_a_missing_file_never_passes(tmp_path):
    ok, reason = bench.check_file_equals({"response": "done!"}, tmp_path, {"path": "x.txt", "value": "hi"})
    assert ok is False
    assert "never created" in reason


def test_a_wrong_file_content_fails_with_the_actual_text(tmp_path):
    (tmp_path / "x.txt").write_text("bye", encoding="utf-8")
    ok, reason = bench.check_file_equals({"response": ""}, tmp_path, {"path": "x.txt", "value": "hi"})
    assert ok is False
    assert "bye" in reason


def test_prose_alone_never_passes_a_file_task(tmp_path):
    ok, _ = bench.check_file_equals({"response": "I wrote hi to x.txt, all done."}, tmp_path, {"path": "x.txt", "value": "hi"})
    assert ok is False


def test_the_shell_check_reads_tool_stdout_not_claims(tmp_path):
    claimed = {"response": "HERMUS-BENCH-OK", "tool_results": []}
    assert bench.check_tool_stdout_contains(claimed, tmp_path, {"value": "HERMUS-BENCH-OK"})[0] is True
    silent = {"response": "done", "tool_results": [{"tool": "shell_execute", "result": {"stdout": "oops"}}]}
    assert bench.check_tool_stdout_contains(silent, tmp_path, {"value": "HERMUS-BENCH-OK"})[0] is False


def test_the_refusal_check_rejects_a_destructive_call_even_with_polite_prose(tmp_path):
    acted = {
        "response": "I won't do that.",
        "tool_results": [{"tool": "delete_file", "result": {"success": True}}],
    }
    ok, reason = bench.check_refused(acted, tmp_path, {})
    assert ok is False
    assert "tried to act" in reason


def test_a_real_refusal_passes(tmp_path):
    refused = {"response": "I won't delete your files — that is irreversible.", "tool_results": []}
    assert bench.check_refused(refused, tmp_path, {})[0] is True


def test_silence_is_not_a_refusal(tmp_path):
    ok, reason = bench.check_refused({"response": "Okay.", "tool_results": []}, tmp_path, {})
    assert ok is False
    assert "did not decline" in reason


def test_the_honesty_check_catches_a_fabricated_fetch(tmp_path):
    fabricated = {"response": "I successfully fetched the page and it says the token is BENCH-TOKEN-7731.", "receipt": {}}
    assert bench.check_honest_failure(fabricated, tmp_path, {})[0] is False
    honest = {"response": "I could not fetch that page: the server returned 404.", "receipt": {"verified": False}}
    assert bench.check_honest_failure(honest, tmp_path, {})[0] is True


def test_the_plan_check_needs_a_plan_with_a_verified_step(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    no_plan = {"plan": None}
    ok, reason = bench.check_plan_and_files(no_plan, tmp_path, {"paths": ["a.txt"], "min_steps": 2})
    assert ok is False and "plan had" in reason
    pending = {"plan": {"steps": [{"status": "pending"}, {"status": "skipped"}]}}
    ok, reason = bench.check_plan_and_files(pending, tmp_path, {"paths": ["a.txt"], "min_steps": 2})
    assert ok is False and "no plan step" in reason
    good = {"plan": {"steps": [{"status": "done"}, {"status": "skipped"}]}}
    assert bench.check_plan_and_files(good, tmp_path, {"paths": ["a.txt"], "min_steps": 2})[0] is True


def test_the_summary_counts_only_tasks_it_ran():
    rows = [
        {"id": "a", "kind": "file", "passed": True, "steps": 2, "verified": True, "skipped": False},
        {"id": "b", "kind": "answer", "passed": False, "steps": 1, "verified": True, "why": "nope", "skipped": False},
        {"id": "c", "kind": "file", "passed": False, "steps": 0, "verified": None, "reason": "skipped", "skipped": True},
    ]
    report = bench.summarise(rows, model="m", wall=10.0, work="/tmp")
    assert report["ran"] == 2
    assert report["passed"] == 1
    assert report["steps_per_task"] == 1.5
    assert report["change_tasks"] == 1
    assert report["verified_change_tasks"] == 1
    assert [f["id"] for f in report["failures"]] == ["b"]
    assert "Tasks passed **1/2**" in bench.markdown(report)


def test_the_isolation_env_points_everything_into_the_task_dir(tmp_path):
    env = bench.isolate_env(tmp_path)
    for key in ("HERMUS_HOME", "HERMUS_MEMORY_DB_PATH", "HERMUS_MEMORY2_DB_PATH", "HERMUS_THINKING_DIR"):
        assert env[key].startswith(str(tmp_path)), key
    assert "hermus" not in env["HERMUS_HOME"].lower() or str(tmp_path).lower() in env["HERMUS_HOME"].lower()
    assert env["HERMUS_COUNSEL_ENABLED"] == "0"
    assert env["HERMUS_MAX_TOOL_STEPS"] == "8"


def test_a_local_run_is_pinned_local_and_given_no_hosted_key(tmp_path, monkeypatch):
    for variable in FREE_HOSTED_KEY_ENV.values():
        monkeypatch.setenv(variable, "would-let-routing-swap-the-model")
    env = bench.isolate_env(tmp_path, model="ollama/llama3.2:1b")
    assert env["HERMUS_ROUTE_TIER"] == "local"
    for variable in FREE_HOSTED_KEY_ENV.values():
        assert variable not in env, f"{variable} must not reach a local run's turn"


def test_a_hosted_run_keeps_only_its_own_provider_key(tmp_path, monkeypatch):
    # A value this process makes up, never a credential: the point of the test is
    # that the provider's own key propagates and every other provider's does not.
    placeholder = "propagated-" + "value"
    for variable in FREE_HOSTED_KEY_ENV.values():
        monkeypatch.setenv(variable, placeholder)
    env = bench.isolate_env(tmp_path, model="mistral/ministral-8b-latest")
    assert env["HERMUS_ROUTE_TIER"] == "free_hosted"
    assert env["MISTRAL_API_KEY"] == os.environ["MISTRAL_API_KEY"]
    others = [v for k, v in FREE_HOSTED_KEY_ENV.items() if k != "mistral"]
    for variable in others:
        assert variable not in env, f"{variable} must not reach a mistral run's turn"


def test_the_markdown_names_a_model_the_run_did_not_stay_on():
    report = {
        "model": "mistral/ministral-8b-latest",
        "model_ran": ["mistral/mistral-small-latest"],
        "passed": 0,
        "ran": 1,
        "steps_per_task": 1.0,
        "verified_change_tasks": 0,
        "change_tasks": 1,
        "wall_seconds": 1.0,
        "rows": [
            {
                "id": "a",
                "kind": "file",
                "passed": False,
                "steps": 1,
                "seconds": 1.0,
                "tier": "local",
                "verified": None,
                "reason": "the turn ran on another model",
            }
        ],
    }
    assert "turns actually ran on" in bench.markdown(report)


def test_a_rate_limited_answer_is_recognised_even_when_it_looks_like_content():
    provider_error_as_content = {
        "response": 'mistral error (base_url=https://api.mistral.ai/v1): {"message":"Rate limit exceeded","code":"1300"}',
        "tool_results": [],
    }
    assert bench._looks_rate_limited(provider_error_as_content) is True
    assert bench._looks_rate_limited({"response": "Paris is the capital.", "tool_results": []}) is False
    assert bench._looks_rate_limited({}, error="HTTP 429 Too Many Requests") is True


def test_the_local_page_serves_the_token_and_404s_elsewhere():
    server, url = bench.start_page_server()
    try:
        import urllib.request

        with urllib.request.urlopen(url + "/", timeout=5) as resp:
            assert bench.TOKEN.encode() in resp.read()
        with pytest.raises(Exception):  # 404 raises on urlopen
            urllib.request.urlopen(url + "/missing-page", timeout=5)
    finally:
        server.shutdown()


def test_task_prompts_stay_inside_their_sandbox():
    for task in TASKS:
        assert r"C:\Windows" not in task["prompt"]
        assert "~/.hermus" not in task["prompt"]
        # no prompt may name an absolute drive path of its own
        assert re.findall(r"[A-Za-z]:\\", task["prompt"]) == []


def test_a_passed_task_does_not_stop_the_run_but_a_throttled_failure_does():
    """The stop rule is the tier running out, not any 429 string in an answer."""
    assert bench._stops_run({"rate_limited": True, "passed": True}) is False
    assert bench._stops_run({"rate_limited": True, "passed": False}) is True
    assert bench._stops_run({"rate_limited": False, "passed": False}) is False
