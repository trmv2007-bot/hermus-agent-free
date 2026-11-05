import json
from pathlib import Path


def test_red_lines_protect_core_controls():
    policy = json.loads(Path("policies/red_lines.json").read_text())
    text = json.dumps(policy).lower()
    for term in ("permissions", "audit", "emergency stop", "approval gates"):
        assert term in text


def test_autonomy_boundaries_document_preflight_and_audit():
    text = Path("AUTONOMY_BOUNDARIES.md").read_text().lower()
    assert "pre-flight" in text or "preflight" in text
    assert "audit" in text
    assert "red-line" in text
