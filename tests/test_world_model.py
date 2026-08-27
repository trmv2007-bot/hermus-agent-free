from core.world_model import WorldModel


def test_world_model_tracks_provenance_and_redacts_credentials(tmp_path):
    model = WorldModel(tmp_path / "world.jsonl")
    fact = model.observe(
        "user", "context", {"project": "Hermus", "api_token": "do-not-store"},
        source="profile", permission_scope="profile.read", confidence=0.9,
    )
    assert fact.value["project"] == "Hermus"
    assert fact.value["api_token"] == "[REDACTED]"
    assert fact.permission_scope == "profile.read"

    loaded = WorldModel(tmp_path / "world.jsonl")
    assert loaded.get("user", "context").value["api_token"] == "[REDACTED]"


def test_world_model_merges_facts_and_publishes_events():
    model = WorldModel()
    events = []
    model.subscribe(events.append)
    model.observe("runtime", "cpu_cores", 8, source="runtime")
    model.emit("file_changed", {"path": "core/agent.py"}, source="filesystem")
    assert model.get("runtime", "cpu_cores").value == 8
    assert events[0].event_type == "file_changed"
    assert events[0].data["path"] == "core/agent.py"


def test_runtime_profile_is_available():
    profile = WorldModel().refresh_runtime()
    assert profile["cpu_cores"] >= 1
    assert WorldModel().refresh_runtime()["platform"]
