from __future__ import annotations

from core.contracts import EventEnvelope
from core.events.bus import EventBus
from core.presence import PresenceManager
from core.presence_kernel import PresenceKernel
from core.run_events import RunBus
from core.world_model import WorldModel


class _Executive:
    def active_goals(self, *, limit=10):
        return []

    def recent_events(self, *, limit=10):
        return []


class _Awareness:
    def status(self, *, max_age_seconds=60.0):
        return {"fresh": True, "age_seconds": 1, "fact_count": 0, "event_count": 0}


def test_presence_kernel_projects_event_state_and_cursor(tmp_path):
    bus = EventBus()
    presence = PresenceManager(tmp_path / "presence.json")
    kernel = PresenceKernel(
        presence=presence,
        executive=_Executive(),
        world=WorldModel(),
        awareness=_Awareness(),
        bus=bus,
        runs=RunBus(),
    )
    kernel.start()

    bus.publish(EventEnvelope(type="command.requested", command="build"))
    assert presence.current()["state"] == "thinking"

    bus.publish(EventEnvelope(type="command.started", command="build"))
    assert presence.current()["state"] == "working"

    bus.publish(EventEnvelope(type="verification", command="verify"))
    assert presence.current()["state"] == "verifying"

    bus.publish(EventEnvelope(type="command.completed", command="build"))
    assert presence.current()["state"] == "idle"

    events = kernel.events_since(0, limit=20)
    assert events["cursor"] == 4
    assert len(events["events"]) == 4
    assert events["events"][-1]["type"] == "command.completed"


def test_presence_kernel_snapshot_contains_one_coherent_projection(tmp_path):
    bus = EventBus()
    presence = PresenceManager(tmp_path / "presence.json")
    kernel = PresenceKernel(
        presence=presence,
        executive=_Executive(),
        world=WorldModel(),
        awareness=_Awareness(),
        bus=bus,
        runs=RunBus(),
    )
    snapshot = kernel.snapshot(user_id="default")

    assert snapshot["version"] == 1
    assert snapshot["identity"]["name"] == "Hermus"
    assert snapshot["presence"]["state"] == "idle"
    assert isinstance(snapshot["goals"], list)
    assert isinstance(snapshot["attention"], list)
    assert "world" in snapshot and "facts" in snapshot["world"]
    assert "runtime" in snapshot and "active_runs" in snapshot["runtime"]
    assert snapshot["summary"]["state"] == "idle"
