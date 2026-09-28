"""The gateway gate must reject a wrong token. This is the test that was missing.

A control plane that can run shell commands and drive the computer is exposed
on every interface by default (`start()` binds 0.0.0.0). The token gate is the
only thing between that and the LAN. A gate that looks correct in the source
and accepts every token is the single worst failure mode in this repository,
and nothing in the suite would have caught it.
"""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture()
def gated_env(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_TOKEN", "unit-test-token-123")
    # The module reads config.gateway_api_token first; force the env path so the
    # test exercises the same resolution the gateway uses on a fresh boot.
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", None, raising=False)
    import gateway.context as ctx

    importlib.reload(ctx)
    return ctx


def _app_with_gate(ctx):
    sub = APIRouter()

    @sub.get("/thing")
    def thing():
        return {"ok": True}

    app = FastAPI()
    app.include_router(sub, dependencies=[Depends(ctx._check_gateway_auth)])
    return TestClient(app)


class _FakeRequest:
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


def test_the_gate_rejects_a_wrong_token(gated_env) -> None:
    ctx = gated_env
    with pytest.raises(HTTPException) as exc:
        ctx._check_gateway_auth(_FakeRequest({"X-Hermus-Token": "wrong"}))
    assert exc.value.status_code == 401


def test_the_gate_rejects_a_missing_token(gated_env) -> None:
    ctx = gated_env
    with pytest.raises(HTTPException) as exc:
        ctx._check_gateway_auth(_FakeRequest({}))
    assert exc.value.status_code == 401


def test_the_gate_accepts_the_right_token(gated_env) -> None:
    ctx = gated_env
    assert ctx._check_gateway_auth(_FakeRequest({"X-Hermus-Token": "unit-test-token-123"})) is None


def test_the_gate_is_open_when_no_token_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_GATEWAY_TOKEN", raising=False)
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", None, raising=False)
    import gateway.context as ctx

    importlib.reload(ctx)
    assert ctx.gateway_expected_token() == ""
    assert ctx._check_gateway_auth(_FakeRequest({})) is None


def test_a_gated_route_really_returns_401_over_http(gated_env) -> None:
    """The unit call is not enough: the dependency has to run in the chain.

    A gate that is correct when called directly but never invoked by the
    framework protects nothing, and that is a failure mode with no symptom
    other than "it works fine".
    """
    client = _app_with_gate(gated_env)
    assert client.get("/thing").status_code == 401
    assert client.get("/thing", headers={"X-Hermus-Token": "nope"}).status_code == 401
    ok = client.get("/thing", headers={"X-Hermus-Token": "unit-test-token-123"})
    assert ok.status_code == 200, ok.text


def test_every_control_plane_router_is_actually_gated() -> None:
    """Not a source-text check: ask the assembled app.

    Asserting the string "dependencies=_gate_control" appears in gateway.py
    passes identically whether the routers are wired, dead, or renamed. This
    resolves the real route table and checks a representative route from each
    control-plane module answers 401.
    """
    os.environ.setdefault("HERMES_GATEWAY_TOKEN", "assembly-test-token")
    from core.config import config

    config.gateway_api_token = "assembly-test-token"
    import gateway.context as ctx

    importlib.reload(ctx)

    from gateway.gateway import app

    spec = app.openapi()
    paths = set(spec.get("paths", {}))
    control_plane = [
        "/settings",
        "/engine/status",
        "/voice/status",
        "/engine/models",
    ]
    present = [p for p in control_plane if p in paths]
    assert present, f"expected control-plane routes in the app, found only {sorted(paths)[:20]}"

    client = TestClient(app)
    unauthenticated = {}
    for path in present:
        unauthenticated[path] = client.get(path).status_code

    open_routes = [p for p, code in unauthenticated.items() if code != 401]
    assert not open_routes, (
        "control-plane routes reachable without a token: "
        f"{open_routes} (statuses: {unauthenticated})"
    )
