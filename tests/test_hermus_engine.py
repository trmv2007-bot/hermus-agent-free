from core.hermus_engine import HermusEngine, Intent


def test_engine_rejects_empty_intent():
    result = HermusEngine().submit(Intent(text="   "))
    assert result["accepted"] is False
    assert result["status"] == "rejected"


def test_engine_snapshot_has_backend_sections():
    snapshot = HermusEngine().snapshot()
    assert {"presence", "execution", "agents", "health"} <= set(snapshot)
    assert {"runs", "queue", "active", "recent"} <= set(snapshot["execution"])
