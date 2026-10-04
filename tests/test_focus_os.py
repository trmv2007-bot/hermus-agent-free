from __future__ import annotations

from core.focus_os import FocusOS


def test_focus_os_prioritizes_attention(monkeypatch):
    import core.focus_os as mod

    monkeypatch.setattr(mod, "_now", lambda: "now")

    class P:
        def briefing(self, **kwargs):
            return {"active_goals": [], "priority_tasks": [], "due_tasks": []}
    class K:
        def snapshot(self, **kwargs):
            return {
                "attention": [{"title": "Approval needed", "detail": "Approve", "severity": "high"}],
                "runtime": {"active_runs": []},
                "world": {},
            }
    class D:
        def snapshot(self):
            return {"safety": {"emergency_stop": {"active": False}}}
    class L:
        def snapshot(self, **kwargs):
            return {"totals": {"skills": 1}}

    monkeypatch.setattr(mod, "personal_os", P(), raising=False)
    monkeypatch.setattr(mod, "presence_kernel", K(), raising=False)
    monkeypatch.setattr(mod, "device_fabric", D(), raising=False)
    monkeypatch.setattr(mod, "learning_fabric", L(), raising=False)

    out = FocusOS().snapshot()
    assert out["headline"] == "Approval needed"
    assert out["priorities"][0]["kind"] == "attention"
