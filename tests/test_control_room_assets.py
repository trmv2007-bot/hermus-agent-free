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


def test_assets_declared_in_the_stylesheet_are_served():
    """Fonts are referenced relatively from ``control.css``, so a check written
    against absolute ``/static/`` paths matches nothing and passes while every
    font request 500s — which is exactly what happened here.
    """
    from gateway.gateway import app

    css = (ROOT / "gateway" / "static" / "control.css").read_text(encoding="utf-8")
    refs = sorted(set(re.findall(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", css)))
    local = [ref for ref in refs if not ref.startswith("data:")]
    assert local, f"no local assets referenced by control.css — the regex is wrong ({refs})"

    client = TestClient(app)
    for ref in local:
        # Relative to the stylesheet's own directory, which is how a browser
        # resolves it.
        url = "/static/" + ref if not ref.startswith("/") else ref
        response = client.get(url)
        assert response.status_code == 200, f"{url} is declared in control.css but returns {response.status_code}"
        assert len(response.content) > 100, f"{url} is served but empty ({len(response.content)} bytes)"
        if url.endswith(".woff2"):
            assert response.content[:4] == b"wOF2", f"{url} is not a real woff2 payload"
            assert "font" in response.headers.get("content-type", "")
