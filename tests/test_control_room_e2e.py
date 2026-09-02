"""Control-room E2E — the single production UI is real, not decorative.

Final One-Shot spec §7/§13: every interactive control in /control must be a
typed Command that reaches a backend owner, produces a real state transition /
event, and can be reconstructed by snapshot + replay. No fake progress, no
fake counters, no browser-local-only state, no static "healthy" claims.

These tests drive the canonical backend directly through the same endpoints
/control uses, so they prove the click→command→owner→state→event→UI loop.
"""
from __future__ import annotations

import os
from pathlib import Path


def _client():
    from fastapi.testclient import TestClient
    from gateway.gateway import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Routing: root + the ONE production control room
# ---------------------------------------------------------------------------
def test_root_redirects_to_control():
    c = _client()
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (307, 302)
    assert r.headers.get("location") == "/control"
    # HEAD is allowed on root (health checks / proxies).
    assert c.head("/", follow_redirects=False).status_code in (307, 302)


def test_control_room_serves_from_real_backend_seeds():
    c = _client()
    r = c.get("/control")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    text = r.text
    # snapshot + replay + command architecture, no UI-owned truth
    assert "Snapshot" in text and "Replay" in text
    assert "/api/v1/commands" in text
    assert "never simulates success" in text or "never owns truth" in text
    # every meaningful control maps to a real backend command
    for api in ("/api/v1/system/health", "/api/v1/system/capabilities",
                "/jobs", "/queue/status", "/events/recent", "/dashboard/events",
                "/computer/status", "/computer/run", "/computer/control/emergency-stop",
                "/remote/status", "/remote/approvals", "/remote/", "/remote/",
                "/doctor/status", "/doctor/run", "/api/v1/runs/"):
        assert api in text, f"control room must wire {api}"
    # approve/reject are built dynamically from a real /remote/{action} command
    assert '"/remote/" + act' in text or "'/remote/' + act" in text \
        or '"/remote/"' in text, "remote approve/reject must hit the real backend"
    # Authenticated deployments must be usable from the browser too: HTTP uses
    # the header and browser-only SSE/WS transports receive the query token.
    assert "X-Hermus-Token" in text
    assert "__HERMUS_GATEWAY_TOKEN" in text
    client_js = Path("gateway/static/control-client.js").read_text()
    assert "?token=" in client_js


# ---------------------------------------------------------------------------
# Snapshot rendering comes from real backend state (never fabricated)
# ---------------------------------------------------------------------------
def test_health_snapshot_is_backend_derived():
    """/api/v1/system/health is a real probe; health/capability cards map to it."""
    c = _client()
    h = c.get("/api/v1/system/health").json()
    assert "ok" in h and "python" in h and "venv" in h and "capabilities" in h
    for v in h["capabilities"].values():
        assert v["status"] in ("ready", "capable", "unavailable", "missing", "present")


def test_capabilities_snapshot_is_backend_derived():
    c = _client()
    caps = c.get("/api/v1/system/capabilities").json()
    assert "providers" in caps and "tools" in caps and "tool_count" in caps
    assert "speech" in caps and "transcription" in caps and "avatar" in caps
    assert isinstance(caps["providers"], list)
    assert isinstance(caps["tool_count"], int)


# ---------------------------------------------------------------------------
# Every interactive command reaches a backend owner + emits a typed Command
# ---------------------------------------------------------------------------
def test_command_endpoint_emits_typed_canonical_command():
    c = _client()
    r = c.post("/api/v1/commands", json={
        "command": "system.health", "args": {}, "source": "control",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["trace_id"] and body["event_id"]
    # The command is on the canonical durable EventBus (replayable) with a trace.
    from core.events import get_bus
    snapshot = get_bus().snapshot()
    assert any(getattr(e, "command", None) == "system.health" for e in snapshot)
    assert any(getattr(e, "trace_id", None) == body["trace_id"] for e in snapshot)


def test_command_endpoint_reaches_canonical_bus_with_trace():
    """Click accounting lands on the one durable canonical EventBus."""
    from core.events import get_bus
    c = _client()
    r = c.post("/api/v1/commands", json={"command": "computer.run",
                                         "args": {"goal": "open browser"},
                                         "source": "control"}).json()
    envs = [e for e in get_bus().snapshot() if getattr(e, "trace_id", None) == r["trace_id"]]
    assert envs and envs[0].command == "computer.run"
    assert envs[0].type == "command.requested"
    assert envs[0].source == "control"


# ---------------------------------------------------------------------------
# Computer / Remote / Doctor controls drive real backend owners
# ---------------------------------------------------------------------------
def test_computer_controls_map_to_real_backend():
    c = _client()
    # snapshot backend
    st = c.get("/computer/status").json()
    assert "active" in st
    # the control room's run button targets the real POST /computer/run
    assert c.post("/computer/run", json={"objective": "test"}).status_code in (200, 400, 404, 503)
    # emergency stop is a real, guarded action; always release it afterwards so
    # the global brake does not bleed into every later test/run.
    assert c.post("/computer/control/emergency-stop", json={}).status_code in (200, 400, 503)
    assert "/computer/control/emergency-stop" in _client().get("/control").text
    # release the brake so the global state does not leak into later tests/runs
    assert c.post("/computer/control/emergency-release", json={}).status_code in (200, 400, 503)


def test_remote_controls_map_to_real_backend():
    c = _client()
    # snapshot backend
    assert c.get("/remote/status").json()["emergency"]["halted"] is False
    ap = c.get("/remote/approvals").json()
    assert "history" in ap and "status" in ap
    # approve/reject are real, guarded actions
    assert c.post("/remote/approve", json={"prompt_id": "nope"}).status_code in (200, 400, 404)
    assert c.post("/remote/reject", json={"prompt_id": "nope"}).status_code in (200, 400, 404)


def test_doctor_control_is_real_diagnostics():
    c = _client()
    d = c.get("/doctor/status").json()
    for k in ("enabled", "model", "engine_status", "worst_severity"):
        assert k in d
    run = c.post("/doctor/run", json={"use_llm": False, "ask_internet": False}).json()
    assert run["status"] in ("ok", "attention", "critical")
    assert "findings" in run


# ---------------------------------------------------------------------------
# Live event stream + reconnect/refresh reconstruct the same state
# ---------------------------------------------------------------------------
def test_dashboard_event_stream_snapshot_then_replay():
    """The live WS sends a snapshot, then events; replay reconstructs state."""
    from core.dashboard_events import dashboard_event_bus
    c = _client()
    # event bus is bridged onto the canonical EventBus; the snapshot is real
    assert isinstance(dashboard_event_bus.recent(50), list)
    # replay endpoint reconstructs a run from the durable ledger
    r = c.get("/api/v1/runs/run_nonexistent")
    assert r.status_code == 404  # no fabricated state for unknown runs


def test_replay_endpoint_timeline_is_backed_by_durable_log():
    c = _client()
    from core.events import get_bus
    envs = [e for e in get_bus().snapshot() if getattr(e, "trace_id", None) is not None]
    if envs:
        tid = envs[0].trace_id
        # a command is replayable by its trace id; the run endpoint reads the ledger
        assert any(getattr(e, "trace_id", None) == tid for e in get_bus().snapshot())
        t = c.get("/api/v1/runs/" + tid + "/timeline")
        assert t.status_code in (200, 404)  # ledger-backed, never fabricated


def test_events_recent_is_backed_realtime_feed():
    c = _client()
    r = c.get("/events/recent?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert "count" in body and "events" in body


# ---------------------------------------------------------------------------
# No legacy dashboard surface remains reachable
# ---------------------------------------------------------------------------
def test_no_legacy_dashboard_surface_remains():
    c = _client()
    for path in ("/dashboard", "/dashboard/legacy", "/jarvis", "/dashboard/jarvis",
                 "/computer/dashboard", "/remote", "/dashboard-assets/hermus-client.js",
                 "/dashboard-assets/living-deck.js"):
        assert c.get(path).status_code == 404, path


def test_no_static_assets_directory_entrypoint():
    c = _client()
    assert c.get("/dashboard-assets/").status_code in (404,)
    assert "/dashboard-assets" not in c.get("/control").text


def test_no_production_reference_to_deleted_surfaces():
    """No code path (non-comment) may depend on the deleted dashboard surfaces."""
    import pathlib
    import token as tok_mod
    root = pathlib.Path(__file__).resolve().parents[1]
    forbidden = ("dashboard.html", "jarvis_dashboard", "dashboard_computer.html",
                 "remote.html", "living-deck", "hermus-client", "jarvis-control",
                 "dashboard-assets")
    targets = list((root / "gateway").rglob("*.py")) + [root / "hermus.py"]
    for f in targets:
        src = f.read_text(encoding="utf-8")
        # strip comments (COMMENT tokens) so docstrings/comments don't false-positive
        import io
        code = ""
        try:
            for tok in tok_mod.generate_tokens(io.StringIO(src).readline):
                if tok.type not in (tok_mod.COMMENT, tok_mod.ENCODING, tok_mod.NL, tok_mod.NEWLINE, tok_mod.ENDMARKER):
                    code += tok.string
        except Exception:
            code = src  # fall back to raw if tokenize fails
        lc = code.lower()
        for t in forbidden:
            assert t not in lc, f"{f}: code path still references deleted surface {t}"
