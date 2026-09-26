"""Bind-address policy: exposure decides the auth requirement.

Before this, `start()` hardcoded `uvicorn.run(app, host="0.0.0.0")` while
`_check_gateway_auth` returned None (open) whenever no token was configured.
On a fresh install that served computer control, remote approval and shell
tools to every device on the network with no credential, while the startup
banner advertised http://localhost.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gateway.bind_policy import (
    auth_required_for_bind,
    ensure_gateway_token,
    is_loopback_host,
)


@pytest.mark.parametrize(
    "host,loopback",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("", True),
        (None, True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.5", False),
        ("10.0.0.3", False),
    ],
)
def test_loopback_detection(host, loopback):
    assert is_loopback_host(host) is loopback


def test_lan_bind_requires_auth_but_loopback_does_not(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_ALLOW_UNAUTHENTICATED_LAN", raising=False)
    assert auth_required_for_bind("0.0.0.0") is True
    assert auth_required_for_bind("192.168.1.5") is True
    assert auth_required_for_bind("127.0.0.1") is False
    assert auth_required_for_bind("localhost") is False


def test_explicit_escape_hatch_is_respected(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_ALLOW_UNAUTHENTICATED_LAN", "1")
    assert auth_required_for_bind("0.0.0.0") is False


def test_missing_token_is_generated_and_persisted(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMUS_GATEWAY_TOKEN", raising=False)
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", None, raising=False)
    env = tmp_path / ".env"
    env.write_text("HERMES_MODEL=ollama/x" + chr(10), encoding="utf-8")

    token, generated, persisted = ensure_gateway_token(env)

    assert generated is True
    assert persisted is True
    assert len(token) >= 32, "generated token is too short to be worth anything"
    written = env.read_text(encoding="utf-8")
    assert "HERMES_GATEWAY_TOKEN=" + token in written
    assert "HERMES_MODEL=ollama/x" in written, "existing .env entries must survive"
    assert os.environ["HERMES_GATEWAY_TOKEN"] == token, "running process must use the new token"
    os.environ.pop("HERMUS_GATEWAY_TOKEN", None)


def test_existing_token_is_never_overwritten(tmp_path, monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "gateway_api_token", "already-configured", raising=False)
    env = tmp_path / ".env"
    env.write_text("HERMES_MODEL=ollama/x" + chr(10), encoding="utf-8")

    token, generated, persisted = ensure_gateway_token(env)

    assert token == "already-configured"
    assert generated is False
    assert persisted is False
    assert "HERMES_GATEWAY_TOKEN" not in env.read_text(encoding="utf-8")


def test_start_never_binds_all_interfaces_without_considering_auth():
    """The hardcoded 0.0.0.0 is what made this reachable; guard the bind."""
    src = (Path(__file__).resolve().parents[1] / "gateway" / "gateway.py").read_text(encoding="utf-8")
    assert 'uvicorn.run(app, host="0.0.0.0"' not in src, "gateway binds all interfaces unconditionally"
    assert "uvicorn.run(app, host=host" in src, "gateway must bind the host it decided on"
    assert "auth_required_for_bind(host)" in src, "start() must consult the bind policy"


def test_generated_token_is_not_trivially_guessable():
    from gateway.bind_policy import generate_token

    tokens = {generate_token() for _ in range(50)}
    assert len(tokens) == 50, "token generator is repeating"
    assert all(len(t) >= 32 for t in tokens)
