from datetime import datetime
from zoneinfo import ZoneInfo

from scheduler.cron import CronManager, _one_shot, _recurring_cron


def test_scheduler_parses_common_recurring_patterns():
    manager = CronManager("/tmp/hermus-phase8-parse.json", timezone="Asia/Kolkata", start=False)
    assert manager.parse("every weekday at 9am")["cron"] == "0 9 * * 0-4"
    assert manager.parse("every 2 hours")["cron"] == "0 */2 * * *"
    assert manager.parse("every monday at 18:30")["cron"] == "30 18 * * 0"


def test_scheduler_parses_one_shot_relative_and_tomorrow():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert _one_shot("in 15 minutes", now).hour == 12
    target = _one_shot("tomorrow at 9am", now)
    assert target.date().isoformat() == "2026-10-03"
    assert target.hour == 9 and target.minute == 0


def test_scheduler_persists_and_restores_state(tmp_path):
    path = tmp_path / "schedules.json"
    first = CronManager(str(path), timezone="UTC", start=False)
    job = first.add_job("every day at 8am", task="review tasks", user_id="u1")
    second = CronManager(str(path), timezone="UTC", start=False)
    restored = second.list_jobs()[0]
    assert restored["id"] == job["id"]
    assert restored["task"] == "review tasks"
    assert restored["timezone"] == "UTC"
    assert restored["enabled"] is True


def test_scheduler_enqueue_uses_canonical_queue(monkeypatch, tmp_path):
    submitted = {}

    class FakeJob:
        id = "job_8"
        run_id = "run_8"

    class FakeQueue:
        def submit(self, kind, payload, **kwargs):
            submitted["kind"] = kind
            submitted["payload"] = payload
            submitted["kwargs"] = kwargs
            return FakeJob()

    import gateway.queue as queue_module
    monkeypatch.setattr(queue_module, "job_queue", FakeQueue())

    manager = CronManager(str(tmp_path / "schedules.json"), timezone="UTC", start=False)
    job = manager.add_job("every hour", task="check status", user_id="u1")
    manager._execute_job(job)

    assert submitted["kind"] == "runtime.turn"
    assert submitted["payload"]["scheduled"] is True
    assert submitted["payload"]["schedule_id"] == job["id"]
    assert submitted["kwargs"]["session_key"] == "schedule:u1"
    assert manager.list_jobs()[0]["last_job_id"] == "job_8"


def test_scheduler_can_limit_runs_and_disable_after_last_run(monkeypatch, tmp_path):
    class FakeJob:
        id = "job_limit"
        run_id = "run_limit"

    class FakeQueue:
        def submit(self, kind, payload, **kwargs):
            return FakeJob()

    import gateway.queue as queue_module
    monkeypatch.setattr(queue_module, "job_queue", FakeQueue())

    manager = CronManager(str(tmp_path / "schedules.json"), timezone="UTC", start=False)
    job = manager.add_job("every hour", task="one time", max_runs=1)
    manager._execute_job(job)

    current = manager.list_jobs()[0]
    assert current["run_count"] == 1
    assert current["enabled"] is False


def test_one_shot_disables_after_successful_enqueue(monkeypatch, tmp_path):
    class FakeJob:
        id = "job_once"
        run_id = "run_once"

    class FakeQueue:
        def submit(self, kind, payload, **kwargs):
            return FakeJob()

    import gateway.queue as queue_module
    monkeypatch.setattr(queue_module, "job_queue", FakeQueue())

    manager = CronManager(str(tmp_path / "schedules.json"), timezone="UTC", start=False)
    job = manager.add_job("in 10 minutes", task="send reminder")
    manager._execute_job(job)

    current = manager.list_jobs()[0]
    assert current["run_count"] == 1
    assert current["enabled"] is False
    assert current["last_job_id"] == "job_once"
