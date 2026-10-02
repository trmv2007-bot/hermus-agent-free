from core.executive_memory import ExecutiveMemory


class FakeMemory:
    def __init__(self):
        self.calls = []

    def hybrid_recall(self, *args, **kwargs):
        self.calls.append(("recall", args, kwargs))
        return [{"content": "known fix"}]

    def remember(self, *args, **kwargs):
        self.calls.append(("remember", args, kwargs))
        return {"success": True, "id": 1}


def test_recall_uses_learning_memory():
    fake = FakeMemory()
    memory = ExecutiveMemory(fake)
    assert memory.recall_for_goal("fix deployment", project="demo") == [{"content": "known fix"}]
    assert fake.calls[0][2]["kinds"] == ["episodic", "semantic", "procedural", "project"]


def test_outcome_redacts_sensitive_values():
    fake = FakeMemory()
    memory = ExecutiveMemory(fake)
    memory.remember_outcome(
        goal="deploy service",
        state="completed",
        verified=True,
        result={"api_token": "secret", "message": "ok"},
    )
    content = fake.calls[0][1][1]
    assert "secret" not in content
    assert "[REDACTED]" in content


def test_lesson_becomes_procedural_memory():
    fake = FakeMemory()
    memory = ExecutiveMemory(fake)
    result = memory.remember_lesson(goal="test", lesson="run integration tests first")
    assert result["success"] is True
    assert fake.calls[0][1][0] == "procedural"
