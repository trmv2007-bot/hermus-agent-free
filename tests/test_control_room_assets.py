"""Every asset control.html asks for must actually be served.

The hood (the 3D instrument space) shipped referencing ``/static/hood.css``,
``/static/hood.js`` and ``/static/gods-eye.js``, none of which had routes — so
the feature 404'd in the browser while the files sat in the repo. The gateway
serves control assets from an explicit allow-list instead of a StaticFiles
mount, which makes exactly this mistake possible, so it is now pinned.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "gateway" / "control.html").read_text(encoding="utf-8")

_REF = re.compile(r'(?:src|href)="(/static/[^"]+)"')


def _referenced_assets() -> list[str]:
    return sorted(set(_REF.findall(HTML)))


def test_control_html_references_static_assets():
    """Guards the regex itself: no matches would make the next test vacuously pass."""
    assert len(_referenced_assets()) >= 8


@pytest.mark.parametrize("asset", _referenced_assets())
def test_referenced_asset_is_served(asset):
    from gateway.gateway import app

    response = TestClient(app).get(asset)
    assert response.status_code == 200, f"{asset} is referenced by control.html but not served"
    body = response.text
    assert body.strip(), f"{asset} is served empty"
    if asset.endswith(".js"):
        assert "not found" not in body[:40].lower()
    if asset.endswith(".css"):
        assert "{" in body


def test_fonts_used_by_the_stylesheet_are_routed():
    """Fonts are declared in CSS, so the markup scan above cannot see them."""
    from gateway.gateway import app

    css = (ROOT / "gateway" / "static" / "control.css").read_text(encoding="utf-8")
    urls = sorted(set(re.findall(r"url\(['\"]?(/static/[^'\")]+)", css)))
    client = TestClient(app)
    for url in urls:
        assert client.get(url).status_code == 200, f"{url} is declared in control.css but not served"
