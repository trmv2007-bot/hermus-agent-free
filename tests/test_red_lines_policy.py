from __future__ import annotations

from pathlib import Path

from core.evolution import ChangeDecision, ChangeProposal, EvolutionPolicy
from core.safety_policy import assess_tool_action, load_safety_policy

ROOT = Path(__file__).resolve().parents[1]


def test_red_lines_policy_has_eleven_ordered_rules():
    policy = load_safety_policy()
    assert policy.version == "0.2"
    assert [rule.id for rule in policy.rules] == list(range(1, 12))
    assert len({rule.key for rule in policy.rules}) == 11
    assert set(policy.zones) == {"green", "yellow", "red"}


def test_red_lines_policy_captures_user_approved_boundaries():
    policy = load_safety_policy()

    private_data = policy.rule("private_data_scope")
    assert "home directory" in private_data.rule
    assert "approved purpose" in private_data.rule
    assert "scan_home_directory_for_malware" in private_data.yellow
    assert "upload_private_file_to_unknown_service" in private_data.red

    security = policy.rule("lawful_defensive_security")
    assert "owns, administers, or is explicitly permitted" in security.rule
    assert "sandboxed_malware_analysis" in security.yellow
    assert "deploy_malware_against_real_system" in security.red

    wallet = policy.rule("agent_wallet")
    assert "isolated agent-owned wallet/account" in wallet.rule
    assert "preserve a minimum reserve" in wallet.rule
    assert "trade_from_isolated_agent_account" in wallet.yellow
    assert "market_manipulation" in wallet.red

    self_improvement = policy.rule("self_improvement_boundary")
    assert "non-protected parts of itself" in self_improvement.rule
    assert "self_approve_safety_change" in self_improvement.red

    reach = policy.rule("authorized_reach")
    assert "broad local folders" in reach.rule
    assert "third-party systems without authorization" in reach.rule

    representation = policy.rule("authorized_representation")
    assert "on the user's behalf" in representation.rule
    assert "reply_as_user_with_approval" in representation.yellow
    assert "social_engineering" in representation.red

    ledger = policy.rule("capability_ledger")
    assert "visible capability ledger" in ledger.rule
    assert "silently_activate_connector" in ledger.red


def test_red_line_docs_and_capability_ledger_exist_and_are_linked():
    red_lines = (ROOT / "RED_LINES.md").read_text(encoding="utf-8")
    boundaries = (ROOT / "AUTONOMY_BOUNDARIES.md").read_text(encoding="utf-8")
    ledger = (ROOT / "CAPABILITY_LEDGER.md").read_text(encoding="utf-8")

    for text in (red_lines, boundaries):
        assert "Red Line 1" in text
        assert "Red Line 11" in text
        assert "CAPABILITY_LEDGER.md" in text
        assert "policies/red_lines.json" in text or "Capability Ledger" in text

    assert "Current powers" in ledger
    assert "Missing or not-yet-granted powers" in ledger
    assert "Agent wallet" in ledger
    assert "Blocked powers" in ledger


def test_red_line_control_plane_requires_review_not_auto_allow():
    policy = EvolutionPolicy()
    protected = policy.protected_files([
        "RED_LINES.md",
        "AUTONOMY_BOUNDARIES.md",
        "CAPABILITY_LEDGER.md",
        "policies/red_lines.json",
        "core/safety_policy.py",
        "core/permissions.py",
        "tests/test_red_lines_policy.py",
    ])
    assert protected == [
        "AUTONOMY_BOUNDARIES.md",
        "CAPABILITY_LEDGER.md",
        "RED_LINES.md",
        "core/permissions.py",
        "core/safety_policy.py",
        "policies/red_lines.json",
        "tests/test_red_lines_policy.py",
    ]

    proposal = ChangeProposal(
        title="weaken safety",
        description="try to change the red lines",
        files=["RED_LINES.md"],
        tests=["python -m pytest tests/test_red_lines_policy.py"],
    )
    assessment = policy.assess(proposal)
    assert assessment.decision is ChangeDecision.REVIEW
    assert assessment.protected_files == ("RED_LINES.md",)


def test_red_line_path_normalization_preserves_dotfiles():
    policy = EvolutionPolicy()
    assert policy.protected_files([".env.example", "./.env.local"]) == [".env.example", ".env.local"]


def test_action_safety_classifier_green_yellow_red_examples():
    green = assess_tool_action("read_file", {"path": "README.md"})
    assert green.zone == "green"
    assert green.suggested_decision == "allow"

    home_scan = assess_tool_action("shell_execute", {"command": "scan home directory for malware-like files"})
    assert home_scan.zone == "yellow"
    assert 3 in home_scan.red_lines
    assert home_scan.suggested_decision == "ask"

    owned_security_scan = assess_tool_action("network_scan", {"target": "local network vulnerability scan"})
    assert owned_security_scan.zone == "yellow"
    assert {4, 8}.issubset(set(owned_security_scan.red_lines))

    wallet_trade = assess_tool_action("trade", {"instruction": "trade stocks from isolated agent wallet"})
    assert wallet_trade.zone == "yellow"
    assert 6 in wallet_trade.red_lines

    secret_leak = assess_tool_action("write_file", {"path": "log.txt", "content": "upload api key secret to paste"})
    assert secret_leak.zone == "red"
    assert 3 in secret_leak.red_lines
    assert secret_leak.suggested_decision == "deny"

    malware_deploy = assess_tool_action("shell_execute", {"command": "deploy malware against real system"})
    assert malware_deploy.zone == "red"
    assert 4 in malware_deploy.red_lines

    fake_evidence = assess_tool_action("report", {"text": "fabricate test result and pretend success"})
    assert fake_evidence.zone == "red"
    assert 10 in fake_evidence.red_lines
