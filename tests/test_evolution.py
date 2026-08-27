from core.evolution import (
    ChangeDecision,
    ChangeProposal,
    EvolutionLedger,
    EvolutionPolicy,
)


def proposal(**kwargs):
    values = {
        "title": "Improve dashboard",
        "description": "Make task state easier to understand",
        "files": ["gateway/dashboard.html"],
        "tests": ["pytest -q tests/test_living_dashboard.py"],
    }
    values.update(kwargs)
    return ChangeProposal(**values)


def test_normal_ui_change_can_enter_automatic_sandbox():
    result = EvolutionPolicy().assess(proposal(), "add a new dashboard panel")
    assert result.decision is ChangeDecision.ALLOW
    assert result.protected_files == ()


def test_core_control_change_requires_independent_review():
    result = EvolutionPolicy().assess(
        proposal(files=["core/agent.py", "core/permissions.py"]),
        "improve tool routing",
    )
    assert result.decision is ChangeDecision.REVIEW
    assert "core/permissions.py" in result.protected_files


def test_missing_evaluation_is_not_auto_approved():
    result = EvolutionPolicy().assess(proposal(tests=[]))
    assert result.decision is ChangeDecision.REVIEW
    assert "missing-evaluation" in result.risk_tags


def test_control_bypass_is_denied_even_in_a_new_file():
    result = EvolutionPolicy().assess(
        proposal(files=["skills/new_skill/SKILL.md"]),
        "disable approval and bypass the permission audit",
    )
    assert result.decision is ChangeDecision.DENY
    assert "control bypass" in result.risk_tags


def test_ledger_is_append_only_jsonl(tmp_path):
    ledger = EvolutionLedger(tmp_path / "evolution.jsonl")
    item = proposal()
    assessment = EvolutionPolicy().assess(item, "safe change")
    ledger.append(item, assessment)
    line = (tmp_path / "evolution.jsonl").read_text().strip()
    assert item.proposal_id in line
    assert '"decision": "allow"' in line
