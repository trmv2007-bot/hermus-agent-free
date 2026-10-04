from pathlib import Path


def test_personal_space_surface_is_exposed():
    root = Path(__file__).resolve().parents[1]
    html = (root / "gateway/control.html").read_text(encoding="utf-8")
    js = (root / "gateway/static/personal-space.js").read_text(encoding="utf-8")
    routes = (root / "gateway/routes_personal_space.py").read_text(encoding="utf-8")
    assert "PERSONAL SPACE" in html
    assert "/static/personal-space.js" in html
    assert "/personal-space" in js
    assert "/proposals/" in js
    assert "/personal-space" in routes
