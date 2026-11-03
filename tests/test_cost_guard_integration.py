from core.cost_guard import CostGuard
from core.mission import MissionEngine, make_agent_backed_executor


class _Node:
    role = "tester"
    goal = "Run tests and report the result"
    inputs = {}
    outputs = None
    dependencies = []


class _Agent:
    model_name = "test-model"

    class _LLM:
        provider = "test-provider"

    llm = _LLM()

    def chat(self, prompt, **kwargs):
        return {
            "response": "Tests passed.",
            "tool_calls": [],
            "tool_results": [],
            "steps": 1,
            "last_usage": {
                "total_cost": 0.75,
                "prompt_tokens": 40,
                "completion_tokens": 10,
                "total_tokens": 50,
            },
        }


def test_agent_usage_trips_cost_guard():
    guard = CostGuard(max_cost_usd=0.5, max_tokens=100)
    execute = make_agent_backed_executor(agent=_Agent(), cost_guard=guard)

    result = execute(_Node(), {})

    assert guard.tripped is True
    assert guard.total_tokens == 50
    assert result["success"] is False
    assert "COST_BREAKER_TRIPPED" in result["error"]


def test_mission_engine_builds_cost_guarded_default_executor(tmp_path):
    engine = MissionEngine(storage_dir=tmp_path)

    assert isinstance(engine.cost_guard, CostGuard)
    assert engine.cost_guard.is_safe()


def test_real_executor_receives_active_mission_guard(monkeypatch, tmp_path):
    import core.mission as mission

    engine = MissionEngine(storage_dir=tmp_path)
    guard = engine.cost_guard
    received = {}

    def build_executor(**kwargs):
        received.update(kwargs)
        return lambda node, context: {"success": True, "output": "done"}

    monkeypatch.setattr(mission, "make_agent_backed_executor", build_executor)
    engine._build_real_executor()

    assert received["cost_guard"] is guard
