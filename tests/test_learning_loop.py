"""Closed-loop tests: lessons reach the agent prompt, approved proposals fire,
sentinel alerts hand off to FAIRY check-ins, healthz rolls up always-on state,
night-shift consolidate sweeps memory, harness token budgets are enforced, and
the eval benchmark file loads as a harness suite."""

from __future__ import annotations

from pathlib import Path

from core.events.bus import EventBus
from core.harness.runner import HarnessRunner, RunOptions
from core.harness.tasks import Task, resolve_suite
from core.sentinel import Sentinel, approve_proposal, envelope_from_dict

REPO = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------- 1. lessons -> prompt


def test_lessons_reach_agent_prompt(tmp_path, monkeypatch):
    import core.reasoning.lessons as lessons_mod

    store = lessons_mod.LessonsStore(db_path=str(tmp_path / "lessons.db"))
    r = store.add(lesson="User prefers terse answers and no emoji in replies", category="preference", source="test")
    assert r["success"]
    monkeypatch.setattr(lessons_mod, "lessons_store", store)

    from core.agent import HermusAgent

    agent = HermusAgent(model="mock/mock", mode="agent", max_steps=2)
    prompt = agent._build_system_prompt("please be terse, no emoji in my replies").lower()
    assert "terse answers" in prompt, "planted lesson must appear in the agent's system prompt"
    # and a non-matching message does NOT get the lesson (relevance-gated)
    assert "terse answers" not in agent._build_system_prompt("hi").lower()


# ------------------------------------------------------------- 5. approve -> rule fires


def test_approved_proposal_fires_as_rule(tmp_path, monkeypatch):
    import core.proactive as proactive_mod
    import core.sentinel as sentinel_mod

    proposals_file = tmp_path / "proposed_rules.json"
    rules_file = tmp_path / "rules.json"
    monkeypatch.setattr(sentinel_mod, "proposals_path", lambda: proposals_file)
    real_cls = proactive_mod.ProactiveAutomation
    monkeypatch.setattr(proactive_mod, "ProactiveAutomation", lambda *a, **k: real_cls(path=str(rules_file), **k))

    bus = EventBus(log_path=tmp_path / "events.jsonl")
    s = Sentinel(
        bus=bus,
        enqueue=lambda k, p: {"ok": True},
        notify=lambda t, sev: {"ok": True},
        proposals=proposals_file,
        rule_propose_after=2,
    )
    ev = {"type": "job.failed", "command": "nightly_backup", "status": "failed", "args": {"severity": "high"}}
    s.handle_event(envelope_from_dict(ev))
    d2 = s.handle_event(envelope_from_dict(ev))
    prop_id = d2["rule_proposal"]
    out = approve_proposal(prop_id)
    assert out["ok"] and out["rule_id"]

    # a fresh automation loading the same rules file must fire on the event
    fired_log: list[dict] = []
    automation = real_cls(
        path=str(rules_file), enqueue=lambda kind, payload: fired_log.append({"kind": kind, "payload": payload}) or {"ok": True}
    )
    fired = automation.handle_event(envelope_from_dict(ev))
    assert fired and fired[0]["queued"] is True
    assert fired_log and fired_log[0]["kind"] == "runtime.turn"
    assert "nightly_backup" in fired_log[0]["payload"]["task"]


# ------------------------------------------------------------- 3. sentinel alert -> check-in


def test_sentinel_alert_enqueues_readonly_checkin(tmp_path, monkeypatch):
    import gateway.gateway as gw
    from core.config import config

    monkeypatch.setattr(config, "presence_proactive_checkins", True)
    monkeypatch.setattr(config, "sentinel_checkin_cooldown_seconds", 900)
    gw._sentinel_checkin_state["last"] = 0.0

    submitted: list[dict] = []

    class _StubJob:
        id = "job_1"
        run_id = "run_1"

    class _StubQueue:
        enabled = True
        _started = True

        def submit(self, kind, payload, session_key=None, run_id=None):
            submitted.append({"kind": kind, "payload": payload, "session_key": session_key})
            return _StubJob()

    import gateway.queue as queue_mod

    monkeypatch.setattr(queue_mod, "job_queue", _StubQueue())

    env = envelope_from_dict(
        {
            "type": "sentinel.decision",
            "command": "sentinel.alert",
            "status": "succeeded",
            "args": {"action": "alert", "event_type": "incident", "salience": 1.0},
        }
    )
    r = gw._sentinel_alert_checkin(env)
    assert r and r["job_id"] == "job_1"
    assert submitted and submitted[0]["kind"] == "runtime.turn"
    assert submitted[0]["payload"]["read_only"] is True
    # cooldown: immediate second alert does not queue another
    assert gw._sentinel_alert_checkin(env) is None
    # non-alert decisions never trigger
    gw._sentinel_checkin_state["last"] = 0.0
    env2 = envelope_from_dict(
        {"type": "sentinel.decision", "command": "sentinel.note", "args": {"action": "note", "event_type": "heartbeat"}}
    )
    assert gw._sentinel_alert_checkin(env2) is None


# ------------------------------------------------------------- 7. /api/healthz


def test_healthz_route_rolls_up_always_on_state():
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    with TestClient(app) as c:
        r = c.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    for key in ("bus", "sentinel", "cron", "watchdog", "queue"):
        assert key in body, f"healthz missing {key}"
    assert "cursor" in body["bus"]


# ------------------------------------------------------------- 8. night-shift decay


def test_consolidate_deep_sweeps_memory(tmp_path, monkeypatch):
    import core.memory as memory_mod

    calls = {"sweep": 0}

    class _FakeMem:
        def remember(self, kind, content, **kw):
            return {"ok": True}

        def sweep(self, *, project=None, dry_run=True):
            calls["sweep"] += 1
            assert dry_run is False
            return {"checked": 5, "archived": ["a"], "purged": [], "promoted": [], "consolidated": [], "dry_run": False}

    monkeypatch.setattr(memory_mod, "get_memory", lambda: _FakeMem())
    bus = EventBus(log_path=tmp_path / "e.jsonl")
    s = Sentinel(bus=bus, enqueue=lambda k, p: {"ok": True}, notify=lambda t, sev: {"ok": True})
    d = s.consolidate(deep=True)
    assert calls["sweep"] == 1
    assert d["decay"]["checked"] == 5 and d["decay"]["archived"] == 1
    d2 = s.consolidate(deep=False)
    assert calls["sweep"] == 1 and "decay" not in d2


# ------------------------------------------------------------- 6. token budget


def _echo_suite():
    from core.harness.tasks import Suite

    return Suite(
        name="budget",
        tasks=[
            Task.from_dict({"id": "b1", "prompt": "Say the word: BUDGET", "checks": [{"type": "substring", "value": "BUDGET"}]})
        ],
    )


def test_token_budget_flagged(tmp_path):
    suite = _echo_suite()
    rep = HarnessRunner(runs_dir=tmp_path / "r").run_suite(suite, RunOptions(model="mock/mock", timeout_s=30, max_tokens_est=1))
    assert rep["summary"]["budget_exceeded"] is True
    rep2 = HarnessRunner(runs_dir=tmp_path / "r2").run_suite(
        suite, RunOptions(model="mock/mock", timeout_s=30, max_tokens_est=10**9)
    )
    assert rep2["summary"]["budget_exceeded"] is False
    assert rep2["summary"]["passed"] == rep2["summary"]["runs"]


# ------------------------------------------------------------- eval schema loads as suite


def test_eval_benchmark_loads_as_harness_suite():
    suite = resolve_suite(str(REPO / "tests/eval/benchmark_tasks.json"))
    assert len(suite.tasks) >= 10
    assert all(t.prompt for t in suite.tasks)


# ------------------------------------------------------------- 2. watchdog loop (unit side)


def test_watchdog_handle_runs_registered_fix():
    from core.watchdog import Watchdog

    wd = Watchdog()
    wd.register_fix(r"json decode", lambda err: {"ok": True, "fixed": True}, description="re-encode json")
    rec = wd.handle("json decode error in config")
    assert rec["action"] == "apply_fix" and rec["ok"] is True
    rec2 = wd.handle("totally unknown failure xyz")
    assert rec2["action"] == "diagnose"


def test_watchdog_events_drive_sentinel(tmp_path):
    bus = EventBus(log_path=tmp_path / "e.jsonl")
    calls = {"enq": 0, "alert": 0}
    s = Sentinel(
        bus=bus,
        enqueue=lambda k, p: calls.__setitem__("enq", calls["enq"] + 1) or {"ok": True},
        notify=lambda t, sev: calls.__setitem__("alert", calls["alert"] + 1) or {"ok": True},
    )
    s.subscribe()
    bus.publish(
        envelope_from_dict(
            {"type": "watchdog.fix_applied", "command": "fix_json", "status": "succeeded", "args": {"severity": "warn"}}
        )
    )
    bus.publish(
        envelope_from_dict(
            {"type": "watchdog.fix_failed", "command": "fix_timeout", "status": "failed", "args": {"severity": "high"}}
        )
    )
    acts = [d["action"] for d in s.decisions if d["type"].startswith("watchdog.")]
    assert acts == ["enqueue", "alert"]
    assert calls["enq"] == 2 and calls["alert"] == 1
