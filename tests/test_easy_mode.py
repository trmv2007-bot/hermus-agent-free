"""Easy-mode UX tests: `hermus check` (one-command offline selfcheck with
scoreboard + CI exit code), `harness run` with no suite (runs ALL builtin
suites + scoreboard), `doctor --harness` always-on section, and the
scoreboard renderer."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = str(REPO / ".venv/bin/python")


def _cli(*argv, timeout=300):
    env = dict(os.environ)
    env["HERMUS_NO_DOTENV"] = "1"
    return subprocess.run(
        [PY, str(REPO / "hermus.py"), *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(REPO),
    )


def test_check_command_passes_offline_with_scoreboard(tmp_path):
    r = _cli("check", "--no-ping", "--out", str(tmp_path / "runs"))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "== environment ==" in r.stdout
    assert "✓" in r.stdout
    for suite in ("sentinel", "watchdog", "ambient"):
        assert f"{suite}: " in r.stdout
    assert "=== scoreboard ===" in r.stdout
    assert "100%" in r.stdout
    # runs were persisted
    assert (tmp_path / "runs" / "index.json").is_file()


def test_check_fails_loudly_on_broken_suite(tmp_path):
    # a suite that cannot resolve must make check exit non-zero
    r = _cli("check", "--no-ping", "--no-env", "--suite", "definitely_not_a_suite", "--out", str(tmp_path / "runs"))
    assert r.returncode != 0


def test_harness_run_without_suite_runs_all_builtin(tmp_path):
    r = _cli("harness", "run", "--model", "mock/mock", "--no-ping", "--out", str(tmp_path / "runs"), "--tag", "all")
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    for suite in ("ambient", "fairy", "sentinel", "smoke", "watchdog"):
        assert f"=== suite '{suite}' ===" in r.stdout
    assert "=== scoreboard ===" in r.stdout


def test_doctor_harness_section():
    r = _cli("doctor", "--harness", timeout=180)
    assert r.returncode == 0, r.stderr[-1500:]
    assert "== always-on / harness ==" in r.stdout
    assert "sentinel thresholds" in r.stdout
    assert "last sentinel run" in r.stdout


def test_check_command_registered():
    import hermus_cli

    assert "check" in hermus_cli.COMMANDS
    names = [s.name for s in hermus_cli.SPECS]
    assert len(names) == len(set(names))


def test_scoreboard_renderer():
    from core.harness.report import render_scoreboard

    out = render_scoreboard(
        [
            {
                "suite": "sentinel",
                "model": "mock/mock",
                "run_id": "r1",
                "summary": {"passed": 6, "runs": 6, "success_rate": 1.0, "total_tokens_est": 0, "wall_ms": 5},
            },
            {
                "suite": "fairy",
                "model": "mock/mock",
                "run_id": "r2",
                "summary": {
                    "passed": 1,
                    "runs": 5,
                    "success_rate": 0.2,
                    "total_tokens_est": 300,
                    "wall_ms": 900,
                    "regression": {"drop": 0.3},
                },
            },
        ]
    )
    assert "scoreboard" in out and "sentinel" in out and "6/6" in out and "REGRESSED" in out and "r2" in out
