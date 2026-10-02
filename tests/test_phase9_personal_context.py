from core.personal_context import PersonalContext


class FakeMemory:
    def __init__(self):
        self.model = {"preferences": {}, "projects": [], "workflows": []}
        self.remembered = []
        self.recalled = []
        self.sessions = []

    def load_user_model(self):
        return self.model

    def update_user_model(self, info):
        for key, value in info.items():
            if isinstance(self.model.get(key), dict) and isinstance(value, dict):
                self.model[key].update(value)
            else:
                self.model[key] = value

    def remember(self, kind, content, **kwargs):
        self.remembered.append((kind, content, kwargs))
        return {"success": True}

    def recall(self, query, **kwargs):
        self.recalled.append((query, kwargs))
        return [{"id": 1, "kind": "semantic", "content": "User prefers pytest", "score": 0.9}]

    def search_sessions(self, query, **kwargs):
        self.sessions.append((query, kwargs))
        return [{"session_id": "s1", "role": "user", "content": "We were working on tests", "timestamp": "now"}]


def test_explicit_turn_facts_are_persisted_without_inference():
    memory = FakeMemory()
    context = PersonalContext(memory)
    changes = context.observe_turn("I prefer pytest.", session_id="s1", project="hermus")
    assert changes
    assert "pytest" in memory.model["preferences"]["explicit_preferences"]
    assert any("pytest" in content for _, content, _ in memory.remembered)


def test_goals_and_projects_are_structured_and_upserted():
    memory = FakeMemory()
    context = PersonalContext(memory)
    first = context.add_goal("Ship Phase 9", priority="high", project="hermus")
    second = context.add_goal("Ship Phase 9", status="done", project="hermus")
    project = context.upsert_project("Hermus", language="Python")

    assert first["replaced"] is False
    assert second["replaced"] is True
    assert memory.model["goals"][0]["status"] == "done"
    assert project["success"] is True
    assert memory.model["projects"][0]["language"] == "Python"


def test_snapshot_combines_profile_and_relevant_runtime_context():
    memory = FakeMemory()
    memory.model["preferences"] = {"style": "concise"}
    memory.model["goals"] = [{"title": "Ship Phase 9", "status": "active"}]
    memory.model["projects"] = [{"name": "Hermus"}]
    memory.model["current_focus"] = "personal context"
    context = PersonalContext(memory)

    snap = context.snapshot(query="phase 9", project="hermus")
    assert snap.preferences["style"] == "concise"
    assert snap.goals[0]["title"] == "Ship Phase 9"
    assert snap.current_focus == "personal context"
    assert snap.relevant_memories
    assert snap.recent_sessions


def test_prompt_block_is_bounded_and_contains_context():
    memory = FakeMemory()
    context = PersonalContext(memory)
    block = context.prompt_block(query="testing", project="hermus", max_chars=700)
    assert len(block) <= 700
    assert "relevant_memories" in block
