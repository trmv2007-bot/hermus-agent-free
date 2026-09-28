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
import pathlib

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


def _fresh_app_with_gate():
    """A minimal app with the real gate attached, built fresh.

    Returns the app, not a client: the side-effect guard needs to boot it
    itself so it can compare .env before and after.
    """
    os.environ.setdefault("HERMES_GATEWAY_TOKEN", "no-side-effect-token")
    import gateway.context as ctx

    return _build_app(ctx._check_gateway_auth)


def _build_app(dependency):
    sub = APIRouter()

    @sub.get("/thing")
    def thing():
        return {"ok": True}

    app = FastAPI()
    app.include_router(sub, dependencies=[Depends(dependency)])
    return app


def _app_with_gate(ctx):
    return TestClient(_build_app(ctx._check_gateway_auth))


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
    """A loopback gateway with no token is its own boundary.

    Deliberately does NOT boot a TestClient here. The app lifespan calls
    ensure_gateway_token(), which GENERATES AND PERSISTS a token to the real
    .env. A test that boots the app with the token removed therefore rewrites
    the developer's configuration as a side effect -- it did, three times,
    before this test was fixed. Calling the gate directly keeps the test
    honest and the .env untouched.
    """
    monkeypatch.delenv("HERMES_GATEWAY_TOKEN", raising=False)
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", None, raising=False)
    import gateway.context as ctx

    importlib.reload(ctx)
    assert ctx.gateway_expected_token() == ""
    assert ctx._check_gateway_auth(_FakeRequest({})) is None


def test_no_test_rewrites_the_developers_env() -> None:
    """The token in .env must be stable across the suite.

    Regression guard for the side effect above: several tests in this file
    boot a TestClient, and the lifespan generates and persists a token when it
    cannot find one. Any test that leaves HERMES_GATEWAY_TOKEN unset during a
    TestClient run silently rotates the real credential.
    """
    env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if not env.is_file():
        pytest.skip("no .env in this checkout")

    before = env.read_text(encoding="utf-8")

    client = TestClient(_fresh_app_with_gate())
    client.get("/thing")

    after = env.read_text(encoding="utf-8")
    assert before == after, "running the app rotated HERMES_GATEWAY_TOKEN in .env"


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


def test_a_token_on_disk_is_never_rotated(tmp_path, monkeypatch) -> None:
    """A credential that regenerates itself is not a credential.

    ensure_gateway_token() used to read only the config field and the process
    environment. On this machine that chain came back empty even with a token
    sitting in .env, so a NEW token was generated and written on every single
    boot. Measured: the value rotated on each restart, so anything handed to a
    client was stale within minutes and the LAN control plane silently changed
    its own password.

    The file on disk is the authority now.
    """
    from gateway.bind_policy import ensure_gateway_token

    env = tmp_path / ".env"
    env.write_text("HERMES_GATEWAY_TOKEN=token-written-to-disk\n", encoding="utf-8")

    monkeypatch.delenv("HERMES_GATEWAY_TOKEN", raising=False)
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", None, raising=False)

    token, generated, _ = ensure_gateway_token(env_path=env)

    assert token == "token-written-to-disk", token
    assert generated is False, "an existing token was rotated"
    assert "token-written-to-disk" in env.read_text(encoding="utf-8")

    # And again: a second call must be a no-op, not another rotation.
    again, generated_again, _ = ensure_gateway_token(env_path=env)
    assert (again, generated_again) == (token, False)


def test_the_env_is_the_source_of_truth_when_the_environment_is_empty(tmp_path, monkeypatch) -> None:
    """Explicitly the scenario that used to fail: env unset, token in .env."""
    from gateway.bind_policy import ensure_gateway_token

    env = tmp_path / ".env"
    env.write_text("HERMUS_MODEL=x\nHERMES_GATEWAY_TOKEN=persisted-secret\n", encoding="utf-8")
    monkeypatch.delenv("HERMES_GATEWAY_TOKEN", raising=False)
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", None, raising=False)

    token, generated, _ = ensure_gateway_token(env_path=env)
    assert token == "persisted-secret"
    assert generated is False


def test_a_token_is_generated_only_when_there_is_none(tmp_path, monkeypatch) -> None:
    from gateway.bind_policy import ensure_gateway_token

    env = tmp_path / ".env"
    env.write_text("HERMUS_MODEL=x\n", encoding="utf-8")
    monkeypatch.delenv("HERMES_GATEWAY_TOKEN", raising=False)
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", None, raising=False)

    token, generated, persisted = ensure_gateway_token(env_path=env)
    assert generated is True
    assert persisted is True
    assert token and len(token) >= 32
    # and it is now on disk, so the next boot reuses it
    again, generated_again, _ = ensure_gateway_token(env_path=env)
    assert (again, generated_again) == (token, False)
