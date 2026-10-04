from pathlib import Path


def test_routines_surface_is_exposed_and_validated():
    root = Path(__file__).resolve().parents[1]
    html = (root / "gateway/control.html").read_text(encoding="utf-8")
    js = (root / "gateway/static/nexus.js").read_text(encoding="utf-8")
    assert 'data-open="Routines"' in html
    assert "api('/routines')" in js
    assert "/routines/validate" in (root / "gateway/routes_routines.py").read_text(encoding="utf-8")
