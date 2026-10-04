from __future__ import annotations

from core.router2 import ModelRouter


def test_quality_telemetry_can_change_equal_capability_ranking():
    router = ModelRouter()
    good = {
        "provider": "mock",
        "model": "plain-a",
        "capabilities": {"tools": "yes"},
        "reachable": True,
        "success_rate": 0.98,
        "avg_latency_ms": 100,
        "quality_calls": 10,
    }
    weak = {
        "provider": "mock",
        "model": "plain-b",
        "capabilities": {"tools": "yes"},
        "reachable": True,
        "success_rate": 0.45,
        "avg_latency_ms": 2500,
        "quality_calls": 10,
    }
    router._available_workers = lambda: [weak, good]
    result = router.select("fix some code")
    assert result["model"] == "mock/plain-a"
    assert "quality=" in result["reason"]
