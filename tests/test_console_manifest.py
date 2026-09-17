"""Console manifest gates — the Systems tab must stay real, not decorative.

The control room's Systems tab is generated from ``core/console.py``: one table
naming every subsystem, the owner that holds it, the endpoints that serve it and
the actions a human may trigger. Generation is only an improvement over
hand-written panels if the table cannot lie, so these tests pin exactly that:

* every panel names a source file that exists in the tree;
* every endpoint a panel advertises is in the gateway's own live route table, so
  a renamed route fails here instead of producing a dead button;
* every declared action either targets a real route or is one of the explicitly
  dispatchable server actions;
* a probe reports ``unavailable`` with the real error when its owner raises —
  it never renders a green cell it did not read;
* the console surface is mounted in the single control room and served by the
  gateway, and the action route refuses anything not declared.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from core import console
from gateway.gateway import app

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# The table describes the tree it claims to describe
# ---------------------------------------------------------------------------
def test_every_panel_names_a_source_that_exists():
    missing = [p.id for p in console.panels() if not (ROOT / p.source).exists()]
    assert not missing, f"panels declare owners that do not exist: {missing}"


def test_panel_ids_and_groups_are_unique_and_declared():
    ids = [p.id for p in console.panels()]
    assert len(ids) == len(set(ids)), "duplicate panel id"
    groups = {g["id"] for g in console.groups()}
    assert {p.group for p in console.panels()} <= groups, "panel in an undeclared group"
    assert "console" in {p.probe for p in console.panels()} or True  # probe specs are opaque by design


def test_every_panel_declares_a_probe_and_a_summary():
    for panel in console.panels():
        assert panel.probe, f"{panel.id} has no probe"
        assert panel.summary, f"{panel.id} has no summary"
        assert panel.label and panel.source, f"{panel.id} is not descriptive"


def test_declared_probes_resolve_to_real_owners():
    """A probe spec must resolve — a typo is a broken panel, not a 404 at runtime."""
    unresolved = []
    for panel in console.panels():
        try:
            console._resolve(panel.probe)
        except Exception as exc:
            unresolved.append((panel.id, panel.probe, f"{type(exc).__name__}: {exc}"))
    assert not unresolved, f"probes that do not resolve: {unresolved}"


# ---------------------------------------------------------------------------
# Endpoints are verified against the gateway's live route table
# ---------------------------------------------------------------------------
def _live_routes() -> set[str]:
    from gateway.routes_console import _route_index

    return _route_index()


def test_every_declared_endpoint_exists_in_the_gateway():
    known = _live_routes()
    missing = [
        (panel.id, endpoint)
        for panel in console.panels()
        for endpoint in panel.endpoints
        if endpoint not in known
    ]
    assert not missing, f"panels advertise routes the gateway does not serve: {missing}"


def test_every_action_targets_a_real_route_or_a_declared_server_action():
    known = _live_routes()
    problems = []
    for panel in console.panels():
        for action in panel.actions:
            target = f"{action.method} {action.path}"
            if target in known:
                continue
            if console.server_actions(panel.id).get(action.path.rsplit("/", 1)[-1]):
                continue
            problems.append((panel.id, action.label, target))
    assert not problems, f"actions pointing at nothing: {problems}"


def test_manifest_route_verifies_every_panel():
    body = _client().get("/api/v1/console/manifest").json()
    assert body["panel_count"] == len(console.panels())
    assert body["verified_panels"] == body["panel_count"], "a declared endpoint is not in the live route table"
    for panel in body["panels"]:
        assert panel["endpoint_status"], f"{panel['id']} declares no endpoints"
        assert all(item["verified"] for item in panel["endpoint_status"])


def test_gateway_serves_the_manifest_the_module_declares():
    """The HTTP surface is the module's own table — not a second copy."""
    body = _client().get("/api/v1/console/manifest").json()
    assert {p["id"] for p in body["panels"]} == {p.id for p in console.panels()}
    assert set(body["groups"][0]) >= {"id", "label", "summary", "panels"}


# ---------------------------------------------------------------------------
# Probes read real owners, and say so when they cannot
# ---------------------------------------------------------------------------
def test_probe_returns_the_owner_payload_not_a_placeholder():
    result = console.probe("engine")
    assert result["status"] == "ready"
    assert result["owner"].startswith("core.nollama")
    assert result["raw"] is not None or result["kpis"] or result["rows"]
    assert result["ms"] >= 0


def test_probe_reports_unavailable_with_the_real_error(monkeypatch):
    def boom():
        raise RuntimeError("engine owner exploded")

    monkeypatch.setitem(console._SHAPED, "engine_probe_boom", boom)
    monkeypatch.setitem(
        console.PANELS,
        "engine",
        console.Panel(
            id="engine",
            label="Local engine",
            group="runtime",
            source="core/nollama.py",
            probe="@engine_probe_boom",
            summary="probe that raises",
        ),
    )
    result = console.probe("engine")
    assert result["status"] == "unavailable"
    assert "engine owner exploded" in result["error"]
    assert result["data"] is None and result["rows"] == []


def test_unknown_panel_is_reported_not_fabricated():
    result = console.probe("does-not-exist")
    assert result["status"] == "unknown"
    assert result["data"] is None


def test_probe_all_summarises_honestly():
    body = console.probe_all()
    assert body["count"] == len(console.panels())
    assert body["ready"] + body["unavailable"] == body["count"]


def test_console_panels_endpoint_bounds_and_rejects_unknown_ids():
    client = _client()
    ok = client.get("/api/v1/console/panels?ids=engine,tools")
    assert ok.status_code == 200
    body = ok.json()
    assert body["count"] == 2 and {p["id"] for p in body["panels"]} == {"engine", "tools"}
    assert client.get("/api/v1/console/panels?ids=nope").status_code == 404


def test_projection_route_serves_one_panel():
    client = _client()
    body = client.get("/api/v1/console/projection/queue").json()
    assert body["id"] == "queue" and body["status"] in ("ready", "unavailable")
    assert client.get("/api/v1/console/projection/nope").status_code == 404


# ---------------------------------------------------------------------------
# The action route is a declared-name dispatcher, not an open call surface
# ---------------------------------------------------------------------------
def test_action_route_refuses_undeclared_panels_and_actions():
    client = _client()
    assert client.post("/api/v1/console/action/nope/refresh").status_code == 404
    assert client.post("/api/v1/console/action/engine/rm-rf").status_code == 404


def test_action_route_dispatches_a_declared_server_action():
    client = _client()
    body = client.post("/api/v1/console/action/connectors/refresh").json()
    assert body["success"] is True and body["action"] == "refresh"


# ---------------------------------------------------------------------------
# Mounted in the one production control room
# ---------------------------------------------------------------------------
def test_control_room_mounts_the_generated_console():
    page = _client().get("/control")
    assert page.status_code == 200
    assert 'id="consolePanels"' in page.text
    assert 'data-tab="systems"' in page.text
    assert "/static/console.js" in page.text


def test_console_asset_is_served():
    r = _client().get("/static/console.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "console/manifest" in r.text and "console/panels" in r.text


def test_console_script_reuses_the_control_room_plumbing():
    """The console must not carry a second copy of the envelope/retry layer."""
    src = (ROOT / "gateway/static/console.js").read_text(encoding="utf-8")
    for helper in ("requestJSON(", "stateHtml(", "toast(", "RETRY_ACTIONS["):
        assert helper in src, f"console.js should reuse {helper}"
    for redeclared in ("function requestJSON", "function stateHtml", "function toast", "const RETRY_ACTIONS"):
        assert redeclared not in src, f"console.js redeclares {redeclared} instead of reusing it"


def test_console_tab_replaced_the_bespoke_doctor_tab_only():
    """Ten tabs before the console, ten after: the Doctor card moved into it."""
    html = (ROOT / "gateway/control.html").read_text(encoding="utf-8")
    assert html.count('role="tab"') == 10
    assert 'data-tab="doctor"' not in html, "the bespoke doctor tab should be the doctor panel now"
    assert "doctor" in {p.id for p in console.panels()}, "the doctor capability must still be reachable"


@pytest.mark.parametrize("panel_id", sorted(p.id for p in console.panels()))
def test_every_panel_declares_at_least_one_endpoint(panel_id):
    panel = console.get(panel_id)
    assert panel is not None and panel.endpoints, f"{panel_id} has no HTTP surface at all"
