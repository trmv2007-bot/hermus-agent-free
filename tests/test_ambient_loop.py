from __future__ import annotations

from core.ambient_loop import AmbientLoop
from core.contracts import EventType
from core.events.bus import EventBus


class FakeKernel:
    def __init__(self):
        self.items = []

    def start(self):
        return self

    def snapshot(self, *, user_id="default", include_events=False):
        return {
            "attention": list(self.items),
            "summary": {"state": "idle", "detail": "ready", "headline": "ready"},
        }


class FakeAwareness:
    def __init__(self):
        self.calls = 0

    def refresh(self, **kwargs):
        self.calls += 1
        return {"facts": []}


def test_ambient_loop_is_quiet_until_attention_changes():
    bus = EventBus()
    kernel = FakeKernel()
    awareness = FakeAwareness()
    loop = AmbientLoop(kernel=kernel, awareness=awareness, bus=bus)

    first = loop.tick()
    assert first["attention_changed"] is False
    assert bus.recent(limit=20) == []

    kernel.items = [
        {
            "id": "approvals",
            "severity": "high",
            "title": "Approval needed",
            "detail": "One action is waiting.",
        }
    ]
    second = loop.tick()
    assert second["attention_changed"] is True
    events = bus.recent(limit=20, event_type=EventType.STATE_CHANGED.value)
    assert len(events) == 1
    assert events[0].command == "attention.raised"


def test_ambient_loop_reports_cleared_attention():
    bus = EventBus()
    kernel = FakeKernel()
    awareness = FakeAwareness()
    loop = AmbientLoop(kernel=kernel, awareness=awareness, bus=bus)

    kernel.items = [{"id": "goal", "severity": "medium", "title": "Goal due", "detail": "Review it."}]
    loop.tick()
    kernel.items = []
    result = loop.tick()

    assert result["cleared"] == ["goal"]
    commands = [event.command for event in bus.recent(limit=20, event_type=EventType.STATE_CHANGED.value)]
    assert "attention.cleared" in commands
