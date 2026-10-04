from __future__ import annotations

from core.learning_fabric import LearningFabric


class _Lessons:
    def stats(self): return {"total": 3}
    def recent(self, limit=12): return [{"id": 1}]


class _Forge:
    def stats(self): return {"registered_skills": 2}
    def index(self): return {"skills": {"a": {"name": "a"}, "b": {"name": "b"}}}


class _Episodes:
    def stats(self): return {"total": 4}
    def list(self, limit=12): return [{"task_id": "x"}]


def test_learning_fabric_returns_one_projection(monkeypatch):
    import core.learning_fabric as mod
    monkeypatch.setattr(mod, "_now", lambda: "now")
    monkeypatch.setattr("core.skill_forge.skill_forge", _Forge())
    monkeypatch.setattr("core.reasoning.lessons.lessons_store", _Lessons())
    monkeypatch.setattr("core.computer.get_episode_store", lambda: _Episodes())

    out = LearningFabric().snapshot()
    assert out["version"] == 1
    assert out["totals"] == {"skills": 2, "lessons": 3, "episodes": 4}
