from __future__ import annotations

from core.device_fabric import DeviceFabric


class _World:
    def snapshot(self):
        return {"facts": [{"subject": "browser", "predicate": "state", "value": {"active": True}}]}


def test_device_fabric_degrades_honestly(monkeypatch):
    import core.device_fabric as mod
    monkeypatch.setattr(mod, "world_awareness", type("A", (), {"world": _World()})())
    out = DeviceFabric().snapshot()
    assert out["version"] == 1
    assert out["browser"]["state"]["active"] is True
    assert "safety" in out
