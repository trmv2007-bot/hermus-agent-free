"""The built workspace bundle must actually load in a browser.

The gateway serves the SPA from an explicit directory rather than a StaticFiles
mount, so the same class of mistake that broke the hood assets is possible here:
an index.html that references a file no route serves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "gateway" / "static" / "workspace"


def _client() -> TestClient:
    from gateway.gateway import app

    return TestClient(app)


def _built() -> bool:
    return (WORKSPACE / "index.html").is_file()


pytestmark = pytest.mark.skipif(not _built(), reason="workspace bundle not built (cd frontend && npm run build)")


def _index_html() -> str:
    return (_client().get("/workspace/app")).text


def test_workspace_page_serves_the_built_document():
    response = _client().get("/workspace/app")
    assert response.status_code == 200
    assert 'id="root"' in response.text
    assert "HERMUS Workspace" in response.text


def test_every_asset_the_bundle_references_is_served():
    html = _index_html()
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert refs, "the built index.html should reference its own assets"
    client = _client()
    for ref in refs:
        path = "/" + ref.split("://")[-1].split("/", 1)[-1] if "://" in ref else ref
        response = client.get(path)
        assert response.status_code == 200, f"{path} is referenced by /workspace but not served"
        assert response.content.strip(), f"{path} is served empty"


def test_asset_paths_cannot_escape_the_workspace_directory():
    client = _client()
    for attempt in (
        "/static/workspace/../../gateway.py",
        "/static/workspace/..%2F..%2Fgateway.py",
        "/static/workspace/../../../../etc/passwd",
        "/static/workspace/gateway/static/control.html",
    ):
        assert client.get(attempt).status_code in (400, 404), attempt


def test_the_served_page_carries_no_gateway_secret():
    """The token is the operator's to supply per tab; a page that embeds the
    server's own secret would hand it to anyone who can load the page."""
    html = _index_html()
    assert "__HERMUS_GATEWAY_TOKEN" not in html
    assert not re.search(r"token\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}", html)


def test_the_bundle_is_not_a_second_hand_written_page():
    """The one-production-UI rule still holds: gateway/*.html stays a single file,
    and the workspace is a generated document under static/."""
    assert [p.name for p in (ROOT / "gateway").glob("*.html")] == ["control.html"]
    assert (WORKSPACE / "index.html").is_file()


def test_a_missing_asset_says_so_instead_of_serving_html():
    """A 200 with an HTML error page inside a <script> tag is the worst possible
    outcome for a bundle that failed to build."""
    response = _client().get("/static/workspace/assets/definitely-not-here.js")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_the_product_ui_and_the_workspace_link_to_each_other():
    """The advanced space is otherwise undiscoverable, and the SPA needs a way
    back to the face."""
    control = _client().get("/control").text
    assert "/workspace/app" in control, "control.html lost its way into the workspace"

    bundle = _client().get("/static/workspace/assets/workspace.js").text
    assert "/control" in bundle, "the workspace lost its link back to the product UI"
