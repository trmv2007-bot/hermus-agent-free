from pathlib import Path

from core.executive import ExecutiveBrain
from core.executive_loop import ExecutiveLoop
from core.world_model import WorldModel


def test_perception_populates_world_model(tmp_path: Path):
    loop = ExecutiveLoop(
        brain=ExecutiveBrain(tmp_path / "executive.sqlite3"),
        world=WorldModel(tmp_path / "world.jsonl"),
    )
    result = loop.perceive(platform="test", user_id="tester")
    assert result["runtime"]["cpu_cores"] >= 1
    assert loop.world.get("runtime", "platform") is not None
    assert loop.world.get("session", "identity").value["user_id"] == "tester"


def test_loop_is_importable_without_starting_external_actions():
    loop = ExecutiveLoop(
        brain=ExecutiveBrain(":memory:"),
        world=WorldModel(),
    )
    assert loop.brain is not None
    assert loop.world is not None
