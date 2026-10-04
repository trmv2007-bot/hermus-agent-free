from __future__ import annotations

from core.context_fabric import ContextFabric


class _Memory:
    def recall(self, query, **kwargs):
        return [{"content": "Known project convention", "kind": "semantic"}]


class _Presence:
    def snapshot(self, **kwargs):
        return {
            "presence": {"state": "working"},
            "attention": [{"id": "x"}],
            "runtime": {"active_runs": []},
            "summary": {"state": "working"},
        }


class _Brain:
    def active_goals(self, *, limit=8):
        return [{"title": "Ship HERMUS"}]


class _ModelGateway:
    def selected_models(self):
        return {"selections": {"default": "auto"}}


class _World:
    def status(self):
        return {"fresh": True}


def test_context_fabric_builds_bounded_packet(monkeypatch):
    import core.context_fabric as mod

    monkeypatch.setattr(mod, "memory", _Memory())
    monkeypatch.setattr(mod, "presence_kernel", _Presence())
    monkeypatch.setattr(mod, "executive_brain", _Brain())
    monkeypatch.setattr(mod, "world_awareness", _World())
    monkeypatch.setattr(mod, "get_model_gateway", lambda: _ModelGateway())

    packet = ContextFabric().build("fix workshop", project="demo")
    assert packet["version"] == 1
    assert packet["project"] == "demo"
    assert packet["memory"][0]["content"] == "Known project convention"
    assert packet["goals"][0]["title"] == "Ship HERMUS"
    assert packet["attention"] == [{"id": "x"}]
