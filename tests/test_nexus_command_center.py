from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_nexus_command_center_surfaces_are_reachable():
    html = (ROOT / "gateway/control.html").read_text(encoding="utf-8")
    js = (ROOT / "gateway/static/nexus.js").read_text(encoding="utf-8")
    assert 'data-open="Focus"' in html
    assert 'data-open="Learning"' in html
    assert "Ctrl" in js and "toLowerCase()==='p'" in js
    assert "PALETTE_ITEMS" in js
    assert "api('/focus')" in js
    assert "api('/learning?limit=6')" in js
