"""Tests for the eval-harness layer of core/harness (tasks, verify, runner, CLI).

The runner tests inject a scripted agent via ``RunOptions.agent_factory`` so
they are deterministic and offline; one CLI test runs the real agent with
``mock/mock`` on a response-only task.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core.harness.runner import HarnessRunner, RunOptions
from core.harness.tasks import SuiteError, list_builtin_suites, resolve_suite, suite_info
from core.harness.verify import verify_task

# --------------------------------------------------------------------------- helpers


class ScriptedAgent:
    """Deterministic stand-in for HermusAgent.

    Writes the files its prompt asks for, so file checks can pass for real.
    """

    def __init__(self, task, workspace, *, model=None, max_steps=None):
        self.task = task
        self.workspace = Path(workspace)
        self.model = model
        self.max_steps = max_steps
        self.trajectory = [{"role": "user", "content": task.prompt}]

    def chat(self, prompt: str) -> dict:
        self.trajectory.append({"role": "assistant", "content": "working on it"})
        if "hello.txt" in prompt:
            (self.workspace / "hello.txt").write_text("hello harness\n", encoding="utf-8")
        if "intro.md" in prompt:
            p = self.workspace / "docs" / "guide" / "intro.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# Intro\n", encoding="utf-8")
        if "sleep" in prompt:
            time.sleep(5)
        return {
            "response": f"done — the project name is asked about. {prompt[:30]}",
            "steps": 2,
            "tool_results": [{"tool": "file_write", "args": {"path": "hello.txt"}, "result": {"success": True}}],
        }


def _factory(task, workspace, *, model=None, max_steps=None):
    return ScriptedAgent(task, workspace, model=model, max_steps=max_steps)


def _write_suite(tmp_path: Path, tasks: list[dict], name: str = "test") -> Path:
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps({"name": name, "description": "test suite", "tasks": tasks}), encoding="utf-8")
    return p


def _demo_tasks() -> list[dict]:
    return [
        {
            "id": "write_hello",
            "category": "file",
            "prompt": "Create hello.txt containing: hello harness",
            "checks": [
                {"type": "file_exists", "path": "hello.txt"},
                {"type": "file_contains", "path": "hello.txt", "value": "hello harness"},
            ],
        },
        {
            "id": "nested",
            "category": "file",
            "prompt": "Create docs/guide/intro.md with first line # Intro",
            "checks": [{"type": "file_regex", "path": "docs/guide/intro.md", "pattern": "#\\s*Intro"}],
        },
        {
            "id": "answer",
            "category": "response",
            "prompt": "What is the project name?",
            "checks": [{"type": "substring", "value": "project name"}],
        },
        {
            "id": "broken_setup",
            "category": "setup",
            "prompt": "anything",
            "setup": ["exit 3"],
            "checks": [{"type": "substring", "value": "never"}],
        },
    ]


# --------------------------------------------------------------------------- suites


def test_builtin_smoke_suite_loads():
    names = list_builtin_suites()
    assert "smoke" in names
    suite = resolve_suite("smoke")
    assert suite.name == "smoke"
    assert len(suite.tasks) >= 5
    info = suite_info("smoke")
    assert info["task_count"] == len(suite.tasks)
    assert all("id" in t and "prompt" in t for t in info["tasks"])


def test_bare_list_suite_format_compatible(tmp_path):
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps([{"id": "t1", "prompt": "hi", "checks": [{"type": "substring", "value": "x"}]}]))
    suite = resolve_suite(str(p))
    assert suite.name == "legacy"
    assert suite.tasks[0].id == "t1"


def test_suite_validation_errors(tmp_path):
    with pytest.raises(SuiteError):
        resolve_suite("does-not-exist-suite")
    dup = _write_suite(tmp_path, [{"id": "a", "prompt": "x"}, {"id": "a", "prompt": "y"}])
    with pytest.raises(SuiteError, match="duplicate"):
        resolve_suite(str(dup))
    noprompt = _write_suite(tmp_path, [{"id": "a"}])
    with pytest.raises(SuiteError, match="prompt"):
        resolve_suite(str(noprompt))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(SuiteError):
        resolve_suite(str(bad))


# --------------------------------------------------------------------------- verifier


def test_verify_response_and_tool_checks(tmp_path):
    v = verify_task(
        {
            "checks": [
                {"type": "substring", "value": "PARIS"},
                {"type": "not_substring", "value": "london"},
                {"type": "regex", "value": r"\b\d+\b"},
                {"type": "length_gt", "value": 5},
                {"type": "tool_called", "name": "web_search"},
            ]
        },
        workspace=tmp_path,
        response="The capital is Paris, answer 42.",
        tool_calls=["web_search", "file_read"],
    )
    assert v["success"] and v["checks_passed"] == 5 and v["score"] == 1.0
    v2 = verify_task({"checks": [{"type": "substring", "value": "london"}]}, workspace=tmp_path, response="Paris")
    assert not v2["success"] and v2["checks"][0]["passed"] is False


def test_verify_file_checks(tmp_path):
    (tmp_path / "hello.txt").write_text("hello harness\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    v = verify_task(
        {
            "checks": [
                {"type": "file_exists", "path": "hello.txt"},
                {"type": "file_not_exists", "path": "nope.txt"},
                {"type": "file_contains", "path": "hello.txt", "value": "HELLO HARNESS"},
                {"type": "file_regex", "path": "hello.txt", "pattern": "hello\\s+harness"},
            ]
        },
        workspace=tmp_path,
        response="",
    )
    assert v["success"], json.dumps(v["checks"], indent=2)


def test_verify_command_check(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    v = verify_task(
        {
            "checks": [
                {"type": "command", "cmd": 'test -f f.txt && python3 -c "print(6*7)"', "expect_exit": 0, "stdout_contains": "42"},
                {"type": "command", "cmd": "test -f missing.txt", "expect_exit": 1},
            ]
        },
        workspace=tmp_path,
    )
    assert v["success"], v["checks"]
    v2 = verify_task({"checks": [{"type": "command", "cmd": "exit 1", "expect_exit": 0}]}, workspace=tmp_path)
    assert not v2["success"]


def test_verify_combinators_and_required(tmp_path):
    (tmp_path / "a.txt").write_text("alpha")
    # 'all' fails because nested file_exists(b.txt) fails; 'any' passes via a.txt
    v = verify_task(
        {
            "checks": [
                {
                    "type": "all",
                    "checks": [
                        {"type": "file_contains", "path": "a.txt", "value": "alpha"},
                        {"type": "file_exists", "path": "b.txt"},
                    ],
                },
                {
                    "type": "any",
                    "checks": [
                        {"type": "file_exists", "path": "b.txt"},
                        {"type": "file_exists", "path": "a.txt"},
                    ],
                },
            ]
        },
        workspace=tmp_path,
    )
    assert not v["success"]  # required 'all' failed
    assert v["checks_total"] == 6  # 2 combinators + 4 flattened nested
    # a failing OPTIONAL check never fails the task; a passing required one carries it
    v2 = verify_task(
        {
            "checks": [
                {"type": "file_exists", "path": "a.txt"},
                {"type": "file_exists", "path": "missing.txt", "required": False},
            ]
        },
        workspace=tmp_path,
    )
    assert v2["success"] is True and v2["score"] == 0.5
    # no checks at all -> nothing proven -> fail
    assert verify_task({"checks": []}, workspace=tmp_path)["success"] is False
    v3 = verify_task({"checks": [{"type": "unknown_type"}]}, workspace=tmp_path)
    assert not v3["success"]


# --------------------------------------------------------------------------- runner


def test_runner_end_to_end(tmp_path):
    suite_path = _write_suite(tmp_path, _demo_tasks())
    runs_dir = tmp_path / "runs"
    runner = HarnessRunner(runs_dir=runs_dir)
    seen = []
    report = runner.run_suite(
        str(suite_path),
        RunOptions(agent_factory=_factory, timeout_s=15, tag="t1", on_task_done=lambda i, n, r: seen.append(r["id"])),
    )
    assert [r["id"] for r in report["tasks"]] == ["write_hello", "nested", "answer", "broken_setup"]
    assert seen == ["write_hello", "nested", "answer", "broken_setup"]
    s = report["summary"]
    assert s["runs"] == 4 and s["passed"] == 3 and s["success_rate"] == 0.75
    assert s["statuses"].get("setup_failed") == 1
    assert s["by_category"]["file"]["passed"] == 2

    # artifacts
    run_dir = Path(report["report_json"]).parent
    assert (run_dir / "report.md").is_file()
    assert (runs_dir / "index.json").is_file()
    tr = run_dir / "tasks" / "write_hello"
    assert (tr / "result.json").is_file() and (tr / "trajectory.json").is_file()
    assert (tr / "workspace" / "hello.txt").read_text().strip() == "hello harness"  # isolation + real effect
    assert not (run_dir / "tasks" / "answer" / "workspace" / "hello.txt").exists()

    # history / load / compare
    runs = runner.list_runs()
    assert len(runs) == 1 and runs[0]["tag"] == "t1"
    loaded = runner.load_run(report["run_id"])
    assert loaded["summary"]["passed"] == 3
    report2 = runner.run_suite(str(suite_path), RunOptions(agent_factory=_factory, timeout_s=15, limit=3))
    assert report2["summary"]["runs"] == 3
    comp = runner.compare(report["run_id"], report2["run_id"])
    assert comp["common_tasks"] == 3 and comp["winner"] in ("a", "b", "tie")
    assert runner.compare_markdown(report["run_id"], report2["run_id"]).startswith("# Harness comparison")


def test_runner_limit_and_only(tmp_path):
    suite_path = _write_suite(tmp_path, _demo_tasks())
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    r1 = runner.run_suite(str(suite_path), RunOptions(agent_factory=_factory, only=["write_hello"], timeout_s=10))
    assert [t["id"] for t in r1["tasks"]] == ["write_hello"]
    r2 = runner.run_suite(str(suite_path), RunOptions(agent_factory=_factory, limit=2, timeout_s=10))
    assert len(r2["tasks"]) == 2
    with pytest.raises(SuiteError):
        runner.run_suite(str(suite_path), RunOptions(agent_factory=_factory, only=["nope"]))


def test_runner_timeout_is_recorded_not_fatal(tmp_path):
    suite_path = _write_suite(
        tmp_path,
        [
            {"id": "slow", "prompt": "please sleep a while", "checks": [{"type": "substring", "value": "x"}]},
            {"id": "fast", "prompt": "quick task", "checks": [{"type": "substring", "value": "project name"}]},
        ],
    )
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    report = runner.run_suite(str(suite_path), RunOptions(agent_factory=_factory, timeout_s=0.3))
    by_id = {t["id"]: t for t in report["tasks"]}
    assert by_id["slow"]["status"] == "timeout" and by_id["slow"]["success"] is False
    assert by_id["fast"]["status"] == "ok"  # run continues after a timeout
    assert report["summary"]["statuses"]["timeout"] == 1


def test_runner_no_keep_removes_workspace(tmp_path):
    suite_path = _write_suite(tmp_path, _demo_tasks()[:1])
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    report = runner.run_suite(str(suite_path), RunOptions(agent_factory=_factory, keep_workspace=False, timeout_s=10))
    assert report["tasks"][0]["workspace"] == "(removed)"
    assert not (Path(report["report_json"]).parent / "tasks" / "write_hello" / "workspace").exists()
    assert report["tasks"][0]["success"] is True


# --------------------------------------------------------------------------- CLI wiring


def test_cli_harness_subcommands_parse():
    from hermus_cli import COMMANDS, build_parser

    assert "harness" in COMMANDS
    parser = build_parser()
    a = parser.parse_args(["harness", "run", "smoke", "--limit", "2", "--only", "a,b", "--model", "mock/mock", "--tag", "x"])
    assert a.harness_action == "run" and a.suite == "smoke" and a.only == "a,b" and a.limit == 2
    for argv in (
        ["harness", "suites"],
        ["harness", "tasks", "smoke"],
        ["harness", "runs"],
        ["harness", "show", "abc"],
        ["harness", "report", "abc"],
        ["harness", "compare", "a", "b"],
        ["harness", "sessions"],  # pre-existing subcommand still parses
    ):
        args = parser.parse_args(argv)
        assert args.harness_action == argv[1]


def test_cli_harness_suites_and_tasks_run_readonly(capsys):
    from hermus_cli import build_parser, g_runtime
    from hermus_cli._common import CLIContext

    parser = build_parser()
    ctx = CLIContext(parser=parser)
    g_runtime._run_harness(parser.parse_args(["harness", "suites"]), ctx)
    out = capsys.readouterr().out
    assert "smoke" in out
    g_runtime._run_harness(parser.parse_args(["harness", "tasks", "smoke"]), ctx)
    out = capsys.readouterr().out
    assert "write_hello" in out


def test_cli_harness_run_with_mock_model_end_to_end(tmp_path, capsys):
    """Real agent (mock/mock, offline) through the CLI on a response-only task."""
    from hermus_cli import build_parser, g_runtime
    from hermus_cli._common import CLIContext

    parser = build_parser()
    ctx = CLIContext(parser=parser)
    out_dir = tmp_path / "cli-runs"
    argv = [
        "harness",
        "run",
        "smoke",
        "--only",
        "echo_marker",
        "--model",
        "mock/mock",
        "--timeout",
        "60",
        "--tag",
        "pytest",
        "--out",
        str(out_dir),
    ]
    g_runtime._run_harness(parser.parse_args(argv), ctx)
    out = capsys.readouterr().out
    assert "echo_marker" in out and "PASS" in out
    reports = list(out_dir.glob("*/report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["summary"]["runs"] == 1
    assert report["tasks"][0]["success"] is True  # mock echoes the marker back
    assert report["model"] == "mock/mock"
