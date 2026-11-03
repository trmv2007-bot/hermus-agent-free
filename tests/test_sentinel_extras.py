"""Tests for the always-on extras: rule proposals + approval, night-shift
consolidation, local push alerts, the ambient harness driver, isolated
parallel workers with token accounting, and the sentinel read API."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.events.bus import EventBus
from core.harness.runner import HarnessRunner, RunOptions
from core.harness.tasks import Suite, Task
from core.sentinel import Sentinel, approve_proposal, envelope_from_dict


def _sentinel(tmp_path, **kw):
    bus = EventBus(log_path=tmp_path / "events.jsonl")
    calls = {"enqueued": [], "notified": [], "recorded": []}
    s = Sentinel(
        bus=bus,
        enqueue=lambda k, p: calls["enqueued"].append({"kind": k, "payload": p}) or {"ok": True},
        notify=lambda t, sev: calls["notified"].append({"text": t, "severity": sev}) or {"ok": True},
        recorder=calls["recorded"].append,
        proposals=tmp_path / "proposed_rules.json",
        **kw,
    )
    return s, bus, calls


# ------------------------------------------------------------- #2 rule learning


def test_rule_proposal_after_n_repeats_and_approval(tmp_path, monkeypatch):
    s, bus, calls = _sentinel(tmp_path, rule_propose_after=3)
    ev = {"type": "job.failed", "command": "build", "status": "failed", "args": {"severity": "high"}}
    actions = [s.handle_event(envelope_from_dict(ev))["action"] for _ in range(3)]
    assert actions[0] == "alert" and actions[1] == "note"  # dup de-escalates
    assert calls["enqueued"]  # first alert acted
    rows = json.loads((tmp_path / "proposed_rules.json").read_text())
    assert len(rows) == 1 and rows[0]["pattern"] == "job.failed|build" and rows[0]["approved"] is False
    # a 4th repeat must NOT create a second proposal
    s.handle_event(envelope_from_dict(ev))
    assert len(json.loads((tmp_path / "proposed_rules.json").read_text())) == 1
    # proposal event went out on the bus
    assert any(getattr(e, "type", "") == "sentinel.rule_proposed" for e in bus.recent(limit=20))

    # approve -> durable proactive rule (patch storage paths into tmp)
    import core.proactive as proactive_mod
    import core.sentinel as sentinel_mod

    monkeypatch.setattr(sentinel_mod, "proposals_path", lambda: tmp_path / "proposed_rules.json")
    real_cls = proactive_mod.ProactiveAutomation
    monkeypatch.setattr(proactive_mod, "ProactiveAutomation", lambda *a, **k: real_cls(path=str(tmp_path / "rules.json")))
    out = approve_proposal(rows[0]["id"])
    assert out["ok"] and out["rule_id"]
    rules = real_cls(path=str(tmp_path / "rules.json")).list_rules()
    assert any(r["enabled"] and r["action_type"] == "runtime.turn" for r in rules)
    assert json.loads((tmp_path / "proposed_rules.json").read_text())[0]["approved"] is True
    # double approve is rejected
    assert approve_proposal(rows[0]["id"])["ok"] is False


def test_no_proposal_for_ignored_events(tmp_path):
    s, _, _ = _sentinel(tmp_path, rule_propose_after=2)
    for _ in range(5):
        s.handle_event(envelope_from_dict({"type": "heartbeat", "command": "ping"}))
    assert not (tmp_path / "proposed_rules.json").exists()


# ------------------------------------------------------------- #3 night shift


def test_consolidate_deep_writes_lessons(tmp_path, monkeypatch):
    s, bus, _ = _sentinel(tmp_path)
    for i in range(3):
        bus.publish(envelope_from_dict({"type": "command.failed", "command": f"deploy-{i}", "status": "failed"}))
    bus.publish(envelope_from_dict({"type": "heartbeat", "command": "ping", "status": "ok"}))

    import core.memory as memory_mod
    import core.reasoning.lessons as lessons_mod

    class _FakeMem:
        def remember(self, kind, content, **kw):
            return {"ok": True, "kind": kind}

    monkeypatch.setattr(memory_mod, "get_memory", lambda: _FakeMem())
    real_store = lessons_mod.LessonsStore
    monkeypatch.setattr(lessons_mod, "LessonsStore", lambda *a, **k: real_store(db_path=str(tmp_path / "lessons.db")))
    d = s.consolidate(deep=True)
    assert d["events"] >= 4 and d["failures"] == 3
    assert d.get("lessons_added", 0) >= 1
    # shallow consolidate adds no lessons
    d2 = s.consolidate(deep=False)
    assert "lessons_added" not in d2


# ------------------------------------------------------------- #7 local alerts


def test_default_notify_appends_alerts_jsonl(tmp_path, monkeypatch):
    from core.config import config

    monkeypatch.setattr(type(config), "resolve_path", lambda self, p: tmp_path / p)
    s = Sentinel(bus=None)  # default notify chain
    res = s._default_notify("test alert", "high")
    alerts = tmp_path / "data/sentinel/alerts.jsonl"
    assert alerts.is_file()
    rows = [json.loads(r) for r in alerts.read_text().splitlines() if r.strip()]
    assert rows and rows[-1]["text"] == "test alert" and rows[-1]["severity"] == "high"
    assert "jsonl" in str(res)


# ------------------------------------------------------------- #6 ambient driver


def test_ambient_driver_raised_cleared_silent(tmp_path):
    from core.ambient_loop import run_ambient_task

    def mk(ticks, name):
        ws = tmp_path / name
        t = Task.from_dict({"id": name, "prompt": "x", "driver": "ambient", "ticks": ticks})
        return run_ambient_task(t, ws), ws

    out, ws = mk(
        [
            [{"id": "a", "severity": "low"}],
            [{"id": "a", "severity": "low"}, {"id": "b", "severity": "high"}],
        ],
        "raised",
    )
    ev_lines = (ws / "ambient_events.jsonl").read_text()
    assert "attention.raised" in ev_lines and "sig" not in ev_lines  # ids are not 'sig_*'
    assert '"id": "b"' in ev_lines or "b" in ev_lines

    out2, ws2 = mk(
        [
            [{"id": "a"}, {"id": "b"}],
            [{"id": "a"}],
        ],
        "cleared",
    )
    assert "attention.cleared" in (ws2 / "ambient_events.jsonl").read_text()

    out3, ws3 = mk(
        [
            [{"id": "a"}],
            [{"id": "a"}],
        ],
        "silent",
    )
    ev3 = (ws3 / "ambient_events.jsonl").read_text()
    assert "attention.raised" not in ev3 and "attention.cleared" not in ev3
    ticks = json.loads(out3["response"])["ticks"]
    assert ticks[1]["attention_changed"] is False


def test_ambient_suite_runs_green():
    from core.harness.tasks import resolve_suite

    suite = resolve_suite(str(Path(__file__).resolve().parents[1] / "harness/suites/ambient.json"))
    assert {t.driver for t in suite.tasks} == {"ambient"}
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        rep = HarnessRunner(runs_dir=Path(td)).run_suite(suite, RunOptions(model="mock/mock", timeout_s=30))
    assert rep["summary"]["passed"] == rep["summary"]["runs"] == 3, rep["summary"]


# ------------------------------------------------------------- #5 isolated workers + tokens


def _echo_suite(n=2):
    return Suite(
        name="iso",
        tasks=[
            Task.from_dict(
                {
                    "id": f"echo{i}",
                    "prompt": f"Reply with the exact word: MARKER{i}",
                    "checks": [{"type": "substring", "value": f"MARKER{i}"}],
                }
            )
            for i in range(n)
        ],
    )


def test_worker_subprocess_protocol(tmp_path):
    task = Task.from_dict({"id": "w1", "prompt": "Reply with the exact word: WORKEROK"})
    tf = tmp_path / "task.json"
    tf.write_text(json.dumps(task.to_dict()))
    ws = tmp_path / "ws"
    proc = subprocess.run(
        [sys.executable, "-m", "core.harness.worker", "--task", str(tf), "--workspace", str(ws), "--model", "mock/mock"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("HARNESS_RESULT ")]
    assert proc.returncode == 0 and line, proc.stdout[-500:] + proc.stderr[-500:]
    payload = json.loads(line[-1][len("HARNESS_RESULT ") :])
    assert "WORKEROK" in payload["response"]
    assert payload["tokens_est"] > 0


def test_runner_isolated_parallel_with_tokens(tmp_path):
    suite = _echo_suite(3)
    rep = HarnessRunner(runs_dir=tmp_path / "runs").run_suite(
        suite, RunOptions(model="mock/mock", timeout_s=60, isolated=True, workers=3)
    )
    assert rep["summary"]["passed"] == 3, json.dumps(rep["summary"])
    assert rep["summary"]["total_tokens_est"] > 0
    for t in rep["tasks"]:
        assert t["tokens_est"] > 0 and t["driver"] == "agent"


def test_runner_isolated_keeps_sentinel_inline(tmp_path):
    suite = Suite(
        name="mix",
        tasks=[
            Task.from_dict(
                {
                    "id": "s1",
                    "driver": "sentinel",
                    "prompt": "x",
                    "event": {"type": "incident", "command": "boom", "status": "failed", "args": {"severity": "critical"}},
                    "checks": [{"type": "json", "key": "decisions.0.action", "value": "alert"}],
                }
            )
        ],
    )
    rep = HarnessRunner(runs_dir=tmp_path / "runs").run_suite(
        suite, RunOptions(model="mock/mock", timeout_s=60, isolated=True, workers=2)
    )
    assert rep["summary"]["passed"] == 1


# ------------------------------------------------------------- #9 sentinel API routes


def test_sentinel_routes_registered():
    from gateway.routes_sentinel import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/api/sentinel/status" in paths
    assert "/api/sentinel/decisions" in paths
    assert "/api/sentinel/alerts" in paths
    assert "/api/sentinel/proposals" in paths


def test_gateway_includes_sentinel_router():
    src = (Path(__file__).resolve().parents[1] / "gateway/gateway.py").read_text()
    assert "routes_sentinel import router as _sentinel_router" in src
    assert "app.include_router(_sentinel_router" in src
    # and the always-on spawn
    assert "sentinel_with_gateway" in src and "_sentinel_thread.start()" in src
