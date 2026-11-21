"""The harness surface: the page, its assets, and the diff it reads.

`/harness` is the home now — a task in, a transcript and a diff out — while
`/control` keeps the cockpit. These pin the parts that are easy to break
silently: the page and its assets being served at all, `/` pointing at the
harness, and the diff projection telling the truth (a run with no recorded
changes must say so, never invent hunks).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway.realtime import _collect_paths, _parse_unified


@pytest.fixture(scope="module")
def client():
    from gateway.gateway import app

    with TestClient(app) as test_client:
        yield test_client


def test_the_harness_is_the_home_surface(client):
    page = client.get("/harness")
    assert page.status_code == 200
    body = page.text
    for required in ('id="composer"', 'id="task"', 'id="transcript"', 'id="runList"'):
        assert required in body, f"the harness shell is missing {required}"


def test_root_redirects_to_the_harness(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers["location"].endswith("/harness")


def test_the_cockpit_is_still_there(client):
    assert client.get("/control").status_code == 200


def test_harness_assets_are_served(client):
    css = client.get("/static/harness.css")
    js = client.get("/static/harness.js")
    assert css.status_code == 200 and "--accent" in css.text
    assert js.status_code == 200 and "loadRuns" in js.text


def test_diff_of_an_unknown_run_is_a_404(client):
    assert client.get("/runs/run_does_not_exist/diff").status_code == 404


def test_diff_reports_no_changes_rather_than_inventing_them(client):
    started = client.post("/command", json={"text": "say hi", "async": True}).json()
    run_id = started.get("run_id")
    if not run_id:
        pytest.skip("gateway queue unavailable in this environment")
    body = client.get(f"/runs/{run_id}/diff").json()
    assert body["available"] is False
    assert body["reason"], "an unavailable diff must explain itself"


def test_collect_paths_finds_files_anywhere_in_a_payload():
    seen: set[str] = set()
    _collect_paths(
        {
            "tool_calls": [{"name": "file_write", "arguments": {"path": "notes/a.txt"}}],
            "files": ["memory/b.txt", {"path": "src/c.py"}],
            "nested": {"deep": {"file_path": "docs/d.md"}},
            "noise": {"value": 3, "path": None},
        },
        seen,
    )
    assert {"notes/a.txt", "memory/b.txt", "src/c.py", "docs/d.md"} <= seen


def test_parse_unified_splits_hunks_and_counts_lines():
    diff = (
        "diff --git a/app.py b/app.py\n"
        "index 111..222 100644\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,3 @@\n"
        " keep\n"
        "-old\n"
        "+new\n"
        "+extra\n"
        "diff --git a/b.py b/b.py\n"
        "@@ -5 +5 @@\n"
        "-gone\n"
        "+here\n"
    )
    files = _parse_unified(diff)
    assert [f["path"] for f in files] == ["app.py", "b.py"]
    assert files[0]["additions"] == 2 and files[0]["deletions"] == 1
    assert files[1]["hunks"][0]["header"] == "@@ -5 +5 @@"
    assert "-gone" in files[1]["hunks"][0]["lines"]
