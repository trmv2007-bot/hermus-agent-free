"""Tests for the sentinel (always-alert core) + its harness driver + CLI wiring."""

from __future__ import annotations

import json

from core.events.bus import EventBus
from core.harness.tasks import Task
from core.sentinel import Sentinel, envelope_from_dict, run_sentinel_task, thresholds


def _sentinel(tmp_path, **kw):
    bus = EventBus(log_path=tmp_path / "events.jsonl")
    calls = {"enqueued": [], "notified": [], "recorded": []}

    def enqueue(kind, payload):
        calls["enqueued"].append({"kind": kind, "payload": payload})
        return {"ok": True}

    def notify(text, severity):
        calls["notified"].append({"text": text, "severity": severity})
        return {"ok": True}

    s = Sentinel(
        bus=bus,
        enqueue=enqueue,
        notify=notify,
        recorder=calls["recorded"].append,
        focus_provider=lambda: [],
        **kw,
    )
    return s, bus, calls


def _ev(**kw):
    return envelope_from_dict(kw)


# --------------------------------------------------------------------------- salience / decisions


def test_thresholds_from_config():
    th = thresholds()
    assert th["alert"] == 0.75 and th["enqueue"] == 0.5 and th["note"] == 0.25
    from core.config import config

    assert config.sentinel_alert_threshold == 0.75


def test_critical_incident_alerts_and_enqueues(tmp_path):
    s, bus, calls = _sentinel(tmp_path)
    s.subscribe()  # wire inline reactions, as the daemon does
    d = s.handle_event(_ev(type="incident", command="circuit_open", status="failed", args={"severity": "critical"}))
    assert d["action"] == "alert" and d["salience"] >= 0.75
    assert calls["enqueued"] and calls["notified"] and calls["recorded"]
    # decision event published on the bus, and handling it does not recurse
    types = [getattr(e, "type", "") for e in bus.recent(limit=10)]
    assert "sentinel.decision" in types
    assert len(s.decisions) == 2  # incident + its own decision event, then stops
    assert s.decisions[1]["type"] == "sentinel.decision" and s.decisions[1]["action"] == "ignore"


def test_heartbeat_ignored(tmp_path):
    s, _, calls = _sentinel(tmp_path)
    d = s.handle_event(_ev(type="presence.heartbeat", command="beat", args={"n": 1}))
    assert d["action"] == "ignore"
    assert not calls["enqueued"] and not calls["notified"]


def test_mid_severity_enqueues_without_alert(tmp_path):
    s, _, calls = _sentinel(tmp_path)
    d = s.handle_event(_ev(type="watchdog.fix_failed", command="repair", args={"severity": "warn", "pattern": "X"}))
    assert d["action"] == "enqueue"
    assert calls["enqueued"] and not calls["notified"]


def test_duplicate_event_deescalates(tmp_path):
    s, _, _ = _sentinel(tmp_path)
    ev = _ev(type="incident", command="disk_full", status="failed", args={"severity": "critical"})
    d1 = s.handle_event(ev)
    d2 = s.handle_event(ev)
    assert d1["action"] == "alert" and d2["action"] == "note" and d2["duplicate"] is True


def test_focus_boost(tmp_path):
    bus = EventBus(log_path=tmp_path / "e.jsonl")
    s = Sentinel(bus=bus, enqueue=lambda k, p: {"ok": True}, notify=lambda t, x: {"ok": True}, focus_provider=lambda: ["deploy"])
    # base default 0.2 + failed 0.15 = 0.35 (note) -> +focus 0.2 = 0.55 (enqueue)
    d = s.handle_event(_ev(type="custom.event", command="deploy failed", status="failed", args={"text": "deploy failed"}))
    assert d["action"] == "enqueue"
    assert any("focus_match" in r for r in d["reasons"])


def test_llm_triage_with_mock_falls_back_to_note(tmp_path):
    from core.sentinel import build_llm

    llm, res = build_llm("mock/mock")
    assert res["ok"] and llm is not None
    s, _, _ = _sentinel(tmp_path, llm=llm, model="mock/mock", use_llm_triage=True)
    # signal.* base weight 0.45 lands in the LLM-triage band [0.45, 0.5)
    d = s.handle_event(_ev(type="signal.new", command="triage_me", args={"title": "x"}))
    assert any("llm_triage" in r for r in d["reasons"])
    assert d["action"] == "note"  # mock echo is not JSON -> conservative fallback, never auto-act


# --------------------------------------------------------------------------- harness driver


def test_run_sentinel_task_writes_artifacts(tmp_path):
    task = Task(
        id="t",
        prompt="critical incident",
        driver="sentinel",
        event={"type": "incident", "command": "db_down", "status": "failed", "args": {"severity": "critical"}},
        checks=[],
    )
    out = run_sentinel_task(task, tmp_path / "ws", model="mock/mock")
    ws = tmp_path / "ws"
    assert (ws / "sentinel_decisions.jsonl").is_file()
    assert (ws / "enqueued.jsonl").is_file() and (ws / "alerts.jsonl").is_file()
    assert out["decisions"][0]["action"] == "alert"
    parsed = json.loads(out["response"])
    assert parsed["decisions"][0]["action"] == "alert"


def test_harness_sentinel_suite_passes_offline(tmp_path):
    from core.harness.runner import HarnessRunner, RunOptions

    runner = HarnessRunner(runs_dir=tmp_path / "runs")
    report = runner.run_suite("sentinel", RunOptions(model="mock/mock", timeout_s=30, tag="sentinel-test"))
    s = report["summary"]
    assert s["runs"] == 6, [t["id"] for t in report["tasks"] if not t["success"]]
    assert s["passed"] == 6 and s["success_rate"] == 1.0
    # artifacts per task
    for t in report["tasks"]:
        assert (tmp_path / "runs" / report["run_id"] / "tasks" / t["id"] / "result.json").is_file()


# --------------------------------------------------------------------------- CLI


def test_cli_sentinel_subcommands_parse():
    from hermus_cli import COMMANDS, build_parser

    assert "sentinel" in COMMANDS
    parser = build_parser()
    assert parser.parse_args(["sentinel", "run", "--once", "--model", "mock/mock"]).sentinel_action == "run"
    t = parser.parse_args(["sentinel", "test", '{"type": "incident"}'])
    assert t.sentinel_action == "test" and t.event.startswith("{")
    assert parser.parse_args(["sentinel", "status"]).sentinel_action == "status"
    assert parser.parse_args(["sentinel", "install"]).sentinel_action == "install"


def test_cli_sentinel_test_prints_decision(capsys):
    from hermus_cli import build_parser, g_runtime
    from hermus_cli._common import CLIContext

    parser = build_parser()
    ctx = CLIContext(parser=parser)
    ev = json.dumps({"type": "incident", "command": "circuit_open", "status": "failed", "args": {"severity": "critical"}})
    g_runtime._run_sentinel(parser.parse_args(["sentinel", "test", ev]), ctx)
    out = capsys.readouterr().out
    assert '"action": "alert"' in out and "salience" in out
