from __future__ import annotations

from core.router2 import ModelRouter


def test_catalog_capabilities_beat_model_name(monkeypatch):
    router = ModelRouter()
    monkeypatch.setattr(
        router,
        "_available_workers",
        lambda: [
            {
                "provider": "mock",
                "model": "super-coder",
                "capabilities": {"tools": "no"},
                "reachable": True,
            },
            {
                "provider": "mock",
                "model": "plain-model",
                "capabilities": {"tools": "yes", "reasoning": "yes"},
                "reachable": True,
            },
        ],
    )
    result = router.select("fix the deployment code")
    assert result["success"] is True
    assert result["model"] == "mock/plain-model"


def test_unreachable_catalog_candidate_is_not_selected(monkeypatch):
    router = ModelRouter()
    monkeypatch.setattr(
        router,
        "_available_workers",
        lambda: [
            {
                "provider": "mock",
                "model": "good-name",
                "capabilities": {"tools": "yes"},
                "reachable": False,
            }
        ],
    )
    result = router.select("write code")
    assert result["success"] is False
