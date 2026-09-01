from __future__ import annotations

from core.capability_ledger import CapabilityEntry, CapabilityLedger, capability_entry_from_blocked_action, capability_setup_proposal


def test_capability_ledger_adds_discovered_power_to_markdown(tmp_path):
    path = tmp_path / "CAPABILITY_LEDGER.md"
    ledger = CapabilityLedger(path)
    result = ledger.add_discovered(CapabilityEntry.create(
        power="Gmail delegated send",
        use="reply to approved emails",
        risk="privacy and reputation",
        needed_approval_setup="Gmail connector plus send policy",
        status="not_granted",
    ))
    assert result["success"] is True
    text = path.read_text(encoding="utf-8")
    assert "Gmail delegated send" in text
    assert "reply to approved emails" in text
    rows = ledger.list_discovered()
    assert rows[0]["power"] == "Gmail delegated send"


def test_capability_ledger_deduplicates_by_power_name(tmp_path):
    ledger = CapabilityLedger(tmp_path / "CAPABILITY_LEDGER.md")
    entry = CapabilityEntry.create("Agent wallet")
    assert ledger.add_discovered(entry)["deduped"] is False
    assert ledger.add_discovered(entry)["deduped"] is True
    assert len(ledger.list_discovered()) == 1


def test_control_room_can_record_discovered_power():
    src = __import__("pathlib").Path("gateway/control.html").read_text(encoding="utf-8")
    assert "/capabilities/ledger/discover" in src
    assert "Record power" in src
    assert "function addPower" in src


def test_gateway_exposes_capability_ledger_discover_route():
    src = __import__("pathlib").Path("gateway/routes_subsystems.py").read_text(encoding="utf-8")
    assert '@router.post("/capabilities/ledger/discover")' in src
    assert "CapabilityEntry.create" in src


def test_capability_entry_from_blocked_private_scope_action():
    entry = capability_entry_from_blocked_action(
        "shell_execute",
        {"command": "scan ~/Downloads for malware"},
        {"zone": "yellow", "red_lines": [3]},
        reason="yellow action needs scoped approval",
    )
    assert entry.power == "Scoped private-data access for shell_execute"
    assert "~/Downloads" in entry.use
    assert entry.status == "not_granted"


def test_capability_ledger_records_blocked_action(tmp_path):
    ledger = CapabilityLedger(tmp_path / "CAPABILITY_LEDGER.md")
    result = ledger.record_blocked_action(
        "network_scan",
        {"target": "192.168.1.0/24"},
        {"zone": "yellow", "red_lines": [4, 8]},
        reason="needs network scope",
    )
    assert result["success"] is True
    rows = ledger.list_discovered()
    assert rows[0]["power"] == "Authorized security/reach scope for network_scan"
    assert "192.168.1.0/24" in rows[0]["use"]


def test_permission_manager_records_capability_need_when_yellow_has_no_grant():
    src = __import__("pathlib").Path("core/permissions.py").read_text(encoding="utf-8")
    assert "_record_capability_need" in src
    assert "yellow action needs scoped approval" in src


def test_tool_gateway_records_missing_tool_capability():
    src = __import__("pathlib").Path("core/tools/gateway.py").read_text(encoding="utf-8")
    assert "Tool capability:" in src
    assert "source=\"tool_gateway\"" in src


def test_capability_setup_proposal_is_category_aware():
    proposal = capability_setup_proposal("Gmail delegated send")
    assert proposal.category == "delegated_communication"
    assert 9 in proposal.red_lines
    assert any("send" in item.lower() for item in proposal.tests)
    md = proposal.to_markdown()
    assert "Required approvals/setup" in md
    assert "Activation gates" in md


def test_capability_ledger_can_write_setup_proposal(tmp_path):
    ledger = CapabilityLedger(tmp_path / "CAPABILITY_LEDGER.md")
    out_dir = tmp_path / "proposals"
    result = ledger.propose("Agent wallet trading", write=True, output_dir=out_dir)
    assert result["success"] is True
    assert result["path"]
    assert "agent_wallet_finance" in result["markdown"]
    assert (out_dir / "agent-wallet-trading.md").exists()


def test_control_room_can_generate_setup_proposal():
    src = __import__("pathlib").Path("gateway/control.html").read_text(encoding="utf-8")
    assert "/capabilities/ledger/propose" in src
    assert "Propose setup" in src
    assert "function proposePower" in src
