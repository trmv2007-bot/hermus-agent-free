from core.evolution import ChangeDecision
from core.self_improvement_controller import SelfImprovementController


def test_self_improvement_protects_control_plane(tmp_path):
    controller = SelfImprovementController(tmp_path / "evolution.jsonl")
    result = controller.propose(
        title="Change safety",
        description="Improve approval handling",
        files=["core/permissions.py"],
        tests=["test approval"],
    )
    assert result["assessment"]["decision"] == ChangeDecision.REVIEW.value
    assert "core/permissions.py" in result["assessment"]["protected_files"]


def test_self_improvement_denies_bypass_content(tmp_path):
    controller = SelfImprovementController(tmp_path / "evolution.jsonl")
    result = controller.propose(
        title="Bad optimization",
        description="disable approval and bypass sandbox for speed",
        files=["core/agent.py"],
        tests=["test"],
    )
    assert result["assessment"]["decision"] == ChangeDecision.DENY.value


def test_self_improvement_allows_tested_normal_change(tmp_path):
    controller = SelfImprovementController(tmp_path / "evolution.jsonl")
    result = controller.propose(
        title="Improve parser",
        description="Add robust parsing and regression handling",
        files=["core/parser.py", "tests/test_parser.py"],
        tests=["pytest tests/test_parser.py"],
    )
    assert result["assessment"]["decision"] == ChangeDecision.ALLOW.value
    assert controller.status()["proposals"] == 1


def test_reflection_creates_governed_proposals(tmp_path):
    controller = SelfImprovementController(tmp_path / "evolution.jsonl")
    result = controller.record_reflection(
        {"mistakes": ["Tool browser failed: timeout"]},
        [{"mistake": "Tool browser failed: timeout", "suggested_fix": "retry with fallback"}],
    )
    assert result["proposals"] == 1
    assert result["review_required"] == 0
    assert controller.history(1)[0]["assessment"]["decision"] == "allow"
