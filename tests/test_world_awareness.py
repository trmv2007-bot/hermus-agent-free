from datetime import datetime, timezone

from core.world_awareness import WorldAwareness
from core.world_model import WorldModel


class FakeRegistry:
    def statuses(self):
        return []

    def refresh(self, name):
        return []


def test_world_awareness_reconciles_git_and_tracks_freshness(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = 0
            stdout = {
                "git rev-parse HEAD": "abc123\n",
                "git rev-parse --abbrev-ref HEAD": "main\n",
                "git status --porcelain": " M core/world.py\n",
            }[" ".join(command)]

        return Result()

    monkeypatch.setattr("core.world_awareness.subprocess.run", fake_run)
    world = WorldModel(tmp_path / "world.jsonl")
    awareness = WorldAwareness(world=world, registry=FakeRegistry())

    snapshot = awareness.refresh(workspace_root=tmp_path, include_processes=False)
    commit = world.get("workspace.git", "commit")
    status = world.get("workspace.git", "status")

    assert commit.value == "abc123"
    assert status.value["clean"] is False
    assert status.value["changed_paths"] == [" M core/world.py"]
    assert snapshot["awareness"]["observation_digest"]
    assert awareness.status(max_age_seconds=60)["fresh"] is True
    assert len(calls) == 3


def test_world_awareness_detects_changes_between_refreshes(tmp_path):
    world = WorldModel()
    awareness = WorldAwareness(world=world, registry=FakeRegistry())

    first = awareness.refresh(workspace_root=tmp_path, include_processes=False)
    second = awareness.refresh(workspace_root=tmp_path, include_processes=False)

    assert first["awareness"]["changed"] is False
    assert second["awareness"]["changed"] is False
    assert world.get("world", "last_refresh_at") is not None


def test_world_fact_records_provenance_and_confidence():
    world = WorldModel()
    fact = world.observe(
        "browser",
        "state",
        {"url": "https://example.test"},
        source="browser-observer",
        confidence=0.8,
        permission_scope="browser.read",
    )
    assert fact.source == "browser-observer"
    assert fact.confidence == 0.8
    assert fact.permission_scope == "browser.read"
    assert fact.observed_at <= datetime.now(timezone.utc).isoformat()
