"""Product-level harness tests: run registry (latest/stats/flaky), regression
detection + bus events, cost accounting, A/B compare, gateway API, sentinel
reaction to harness events, and the nightly-eval scheduler."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.events.bus import EventBus
from core.harness.runner import HarnessRunner, RunOptions
from core.harness.tasks import Suite, Task
from core.sentinel import Sentinel, envelope_from_dict

REPO = Path(__file__).resolve().parents[1]


def _pass_suite(name="prod", fail_ids: tuple[str, ...] = ()) -> Suite:
    tasks = []
    for i in range(2):
        tid = f"t{i}" if f"t{i}" not in fail_ids else f"t{i}"
        checks = [{"type": "substring", "value": "OK"}] if tid not in fail_ids else [{"type": "substring", "value": "NEVER"}]
        tasks.append(Task.from_dict({"id": tid, "prompt": "Reply with the exact word: OK", "checks": checks}))
    return Suite(name=name, tasks=tasks)


def _run(runner, suite, **kw):
    return runner.run_suite(suite, RunOptions(model="mock/mock", timeout_s=30, **kw))


# ------------------------------------------------------------- registry / stats


def test_run_registry_latest_and_stats(tmp_path):
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    _run(runner, _pass_suite("alpha"))
    _run(runner, _pass_suite("alpha"))
    _run(runner, _pass_suite("beta"))
    assert len(runner.list_runs(limit=100)) == 3
    last = runner.latest_run("alpha")
    assert last and last["suite"] == "alpha" and last["success_rate"] == 1.0
    stats = {s["suite"]: s for s in runner.suite_stats()}
    assert stats["alpha"]["runs"] == 2 and stats["alpha"]["avg_success_rate"] == 1.0
    assert stats["beta"]["runs"] == 1


def test_flaky_task_detected(tmp_path):
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    _run(runner, _pass_suite("flaky"))  # both pass
    _run(runner, _pass_suite("flaky", fail_ids=("t0",)))  # t0 fails
    stats = {s["suite"]: s for s in runner.suite_stats()}
    assert stats["flaky"]["flaky_tasks"] == ["t0"]


def test_compare_runs_finds_regressions(tmp_path):
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    r1 = _run(runner, _pass_suite("cmp"))
    r2 = _run(runner, _pass_suite("cmp", fail_ids=("t1",)))
    comp = runner.compare(r1["run_id"], r2["run_id"])
    assert comp["regressions"] == ["t1"] and comp["delta"] < 0 and comp["winner"] == "a"


# ------------------------------------------------------------- regression + events + cost


def test_regression_detected_and_published(tmp_path):
    bus = EventBus(log_path=tmp_path / "bus.jsonl")
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    _run(runner, _pass_suite("reg"), bus=bus)  # 100%
    r2 = _run(runner, _pass_suite("reg", fail_ids=("t0",)), bus=bus)  # 50% -> regression
    assert r2["summary"]["regression"] is not None
    assert r2["summary"]["regression"]["failed_tasks"] == ["t0"]
    assert r2["summary"]["regression"]["drop"] == 0.5
    types = [e.type for e in bus.recent(limit=20)]
    assert "harness.run.completed" in types
    assert "harness.regression" in types
    # index row carries the regression flag
    rows = [r for r in runner.list_runs(100) if r["suite"] == "reg"]
    assert rows[-1]["regression"] is True and rows[-2]["regression"] is False


def test_no_regression_when_not_worse(tmp_path):
    bus = EventBus(log_path=tmp_path / "bus.jsonl")
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    _run(runner, _pass_suite("ok"), bus=bus)
    r2 = _run(runner, _pass_suite("ok"), bus=bus)
    assert r2["summary"]["regression"] is None
    types = [e.type for e in bus.recent(limit=20)]
    assert "harness.regression" not in types and "harness.run.completed" in types


def test_cost_estimate_in_summary(tmp_path):
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    r = _run(runner, _pass_suite("cost"))
    assert "cost_est_usd" in r["summary"]  # mock => 0.0, but the field exists
    assert r["summary"]["total_tokens_est"] > 0


# ------------------------------------------------------------- sentinel reacts


def test_sentinel_alerts_on_harness_regression(tmp_path):
    bus = EventBus(log_path=tmp_path / "e.jsonl")
    calls = {"alert": 0, "enq": 0}
    s = Sentinel(
        bus=bus,
        enqueue=lambda k, p: calls.__setitem__("enq", calls["enq"] + 1) or {"ok": True},
        notify=lambda t, sev: calls.__setitem__("alert", calls["alert"] + 1) or {"ok": True},
    )
    d = s.handle_event(
        envelope_from_dict(
            {
                "type": "harness.regression",
                "command": "fairy regressed",
                "status": "failed",
                "args": {"severity": "high", "suite": "fairy", "drop": 0.4},
            }
        )
    )
    assert d["action"] == "alert" and calls["alert"] == 1 and calls["enq"] == 1
    d2 = s.handle_event(
        envelope_from_dict({"type": "harness.run.completed", "command": "harness run sentinel", "args": {"success_rate": 1.0}})
    )
    assert d2["action"] == "ignore"


# ------------------------------------------------------------- gateway API


def test_harness_api_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from core.harness.runner import HarnessRunner

    # point the API's runner at a tmp runs dir with one recorded run
    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    rep = _run(runner, _pass_suite("api"))

    monkeypatch.setattr("gateway.routes_harness._runner", lambda: HarnessRunner(runs_dir=tmp_path / "runs"))
    from gateway.gateway import app

    with TestClient(app) as c:
        r = c.get("/api/harness/suites")
        assert r.status_code == 200 and any(s["name"] for s in r.json()["suites"])
        r = c.get("/api/harness/suites?stats=true")
        assert r.status_code == 200
        names = {s["name"] for s in r.json()["suites"]}
        assert "sentinel" in names
        r = c.get("/api/harness/runs?suite=api")
        assert r.status_code == 200 and r.json()["count"] >= 1
        r = c.get("/api/harness/latest", params={"suite": "api"})
        assert r.status_code == 200 and r.json()["latest"]["suite"] == "api"
        r = c.get(f"/api/harness/runs/{rep['run_id']}")
        assert r.status_code == 200 and r.json()["run_id"] == rep["run_id"]
        r = c.get("/api/harness/runs/does-not-exist")
        assert r.status_code == 404
        r = c.get("/api/harness/stats")
        assert r.status_code == 200 and any(s["suite"] == "api" for s in r.json()["stats"])
        r = c.get("/api/harness/compare", params={"a": rep["run_id"], "b": rep["run_id"]})
        assert r.status_code == 200 and r.json()["winner"] == "tie"


# ------------------------------------------------------------- nightly eval schedule (CLI, hermetic-ish)


def test_harness_schedule_cli_registers_and_removes():
    env = {"HERMUS_NO_DOTENV": "1", "PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)}
    import os

    env.update({k: v for k, v in os.environ.items() if k.startswith("HERMUS_")})
    env["HERMUS_NO_DOTENV"] = "1"
    py = sys.executable  # CI has no .venv; use the running interpreter
    suite = "zz_sched_test"
    r = subprocess.run(
        [py, str(REPO / "hermus.py"), "harness", "schedule", "--suite", suite, "--at", "4:00 am"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(REPO),
    )
    assert r.returncode == 0 and "registered nightly eval" in r.stdout, r.stdout + r.stderr
    check = subprocess.run(
        [
            py,
            "-c",
            "from scheduler.cron import cron_manager; import sys; "
            f"sys.exit(0 if any('{suite}' in str(j.get('task','')) for j in cron_manager.list_jobs()) else 1)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(REPO),
    )
    assert check.returncode == 0
    r2 = subprocess.run(
        [py, str(REPO / "hermus.py"), "harness", "schedule", "--suite", suite, "--remove"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(REPO),
    )
    assert r2.returncode == 0 and "removed 1" in r2.stdout, r2.stdout + r2.stderr
