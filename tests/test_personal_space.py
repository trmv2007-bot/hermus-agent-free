from __future__ import annotations

import json
import time

from core.personal_space import PersonalSpace


class FakeBus:
    def __init__(self):
        self.callbacks = []

    def subscribe(self):
        def register(cb):
            self.callbacks.append(cb)
            return cb
        return register

    def unsubscribe(self, cb):
        self.callbacks = [x for x in self.callbacks if x is not cb]

    def publish(self, envelope):
        self.events = getattr(self, "events", [])
        self.events.append(envelope)


class FakeJob:
    id = "job_ps_1"
    run_id = "run_ps_1"


class FakeQueue:
    enabled = True
    _started = True

    def __init__(self):
        self.jobs = []
        self.results = {}

    def list_jobs(self, limit=50, session_key=None):
        return list(self.jobs)

    def submit(self, kind, payload, **kwargs):
        assert kind == "runtime.turn"
        row = {"id": "job_ps_1", "run_id": "run_ps_1", "kind": kind, "status": "queued", "payload": payload}
        self.jobs.append(row)
        return FakeJob()

    def result(self, job_id):
        return self.results.get(job_id)


def _patch_common(monkeypatch, tmp_path):
    import core.personal_space as mod

    monkeypatch.setattr(mod, "get_bus", lambda: FakeBus())
    monkeypatch.setattr(mod, "_now", lambda: 1_000_000.0)
    monkeypatch.setattr(mod.PersonalSpace, "_context", lambda self: {"focus": {"headline": "test"}})
    monkeypatch.setattr(mod.PersonalSpace, "_attention_blocks", lambda self: (False, ""))
    monkeypatch.setattr(mod.PersonalSpace, "_settings", staticmethod(lambda: (True, 60, 10, 4)))
    return mod.PersonalSpace(tmp_path / "personal_space.json")


def test_personal_space_waits_for_idle(monkeypatch, tmp_path):
    space = _patch_common(monkeypatch, tmp_path)
    assert space.tick()["status"] == "waiting_for_idle"


def test_personal_space_enqueues_read_only_curiosity(monkeypatch, tmp_path):
    import core.personal_space as mod

    queue = FakeQueue()
    monkeypatch.setattr("gateway.queue.job_queue", queue, raising=False)
    space = _patch_common(monkeypatch, tmp_path)
    space.note_user_activity(timestamp=900_000.0)
    out = space.tick()
    assert out["status"] == "started"
    assert queue.jobs[0]["payload"]["read_only"] is True
    assert queue.jobs[0]["payload"]["prefer"] == "chat"
    assert queue.jobs[0]["payload"]["platform"] == "personal-space"


def test_harvest_creates_pending_proposal(monkeypatch, tmp_path):
    queue = FakeQueue()
    monkeypatch.setattr("gateway.queue.job_queue", queue, raising=False)
    space = _patch_common(monkeypatch, tmp_path)
    space.note_user_activity(timestamp=900_000.0)
    assert space.tick()["status"] == "started"
    queue.jobs[0]["status"] = "succeeded"
    queue.results["job_ps_1"] = {
        "response": (
            "TITLE: Faster tests\nCATEGORY: improvement\nCONFIDENCE: 0.88\n"
            "SUMMARY: A possible test optimization.\nWHY: It may save time.\n"
            "NEXT: Review the test setup and compare runtimes."
        ),
        "run_id": "run_ps_1",
    }
    out = space.harvest()
    assert out["proposal"]["status"] == "pending"
    assert out["proposal"]["requires_approval"] is True


def test_no_proposal_from_no_proposal(monkeypatch, tmp_path):
    queue = FakeQueue()
    monkeypatch.setattr("gateway.queue.job_queue", queue, raising=False)
    space = _patch_common(monkeypatch, tmp_path)
    space.note_user_activity(timestamp=900_000.0)
    assert space.tick()["status"] == "started"
    queue.jobs[0]["status"] = "succeeded"
    queue.results["job_ps_1"] = {"response": "NO_PROPOSAL"}
    out = space.harvest()
    assert out["proposal"] is None
    assert space.list_proposals() == []


def test_approve_uses_normal_runtime(monkeypatch, tmp_path):
    queue = FakeQueue()
    monkeypatch.setattr("gateway.queue.job_queue", queue, raising=False)
    space = _patch_common(monkeypatch, tmp_path)
    space.note_user_activity(timestamp=900_000.0)
    assert space.tick()["status"] == "started"
    queue.jobs[0]["status"] = "succeeded"
    queue.results["job_ps_1"] = {
        "response": (
            "TITLE: Useful change\nCATEGORY: project\nCONFIDENCE: 0.9\n"
            "SUMMARY: A useful thing.\nWHY: It matters.\nNEXT: Inspect the project and prepare a patch."
        ),
        "run_id": "run_ps_1",
    }
    proposal = space.harvest()["proposal"]
    out = space.approve(proposal["id"])
    assert out["success"] is True
    approved = queue.jobs[-1]["payload"]
    assert approved["read_only"] is False
    assert approved["platform"] == "personal-space-approved"
