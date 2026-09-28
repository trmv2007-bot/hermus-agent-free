"""Bind-address policy for the gateway.

The control plane can run computer-control, remote-approval and shell tools.
Serving that on every interface with no credential means anything on the same
WiFi can drive the machine, so the bind address decides the auth policy:

* loopback bind  -> token optional (a desktop-only gateway is its own boundary)
* non-loopback   -> token REQUIRED. If none is configured one is generated and
                    persisted, because "exposed to the LAN" must never silently
                    mean "open to the LAN".

`HERMUS_GATEWAY_ALLOW_UNAUTHENTICATED_LAN=1` is the deliberate escape hatch for
someone who has already put a real firewall in front of the port.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", ""}


def is_loopback_host(host: str | None) -> bool:
    """True when binding this address cannot be reached from another machine."""
    name = (host or "").strip()
    if not name or name.lower() in LOOPBACK_HOSTS:
        return True
    if name.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def auth_required_for_bind(host: str | None) -> bool:
    """A non-loopback bind always requires a token. No opt-out.

    An earlier version honoured HERMES_GATEWAY_ALLOW_UNAUTHENTICATED_LAN. It is
    deliberately gone: "exposed to the LAN" should never have a silent
    off-switch, and the two honest ways to reach the port without a token are
    binding 127.0.0.1 or putting a real firewall in front of it.
    """
    return not is_loopback_host(host)


def generate_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def _persist_token(env_path: Path, token: str) -> bool:
    """Append HERMUS_GATEWAY_TOKEN to .env without disturbing what is there."""
    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        kept = [ln for ln in existing.splitlines() if not ln.strip().startswith("HERMES_GATEWAY_TOKEN=")]
        kept.append(f"HERMES_GATEWAY_TOKEN={token}")
        env_path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _read_token_from_env_file(env_path: Path) -> str:
    """The token as written in .env, or "".

    Parsed straight off the file rather than through the loader: the loader's
    view and the file's contents disagreed here, and the file is the thing that
    survives a restart.
    """
    try:
        if not env_path.is_file():
            return ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("HERMES_GATEWAY_TOKEN="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def ensure_gateway_token(env_path: Path | None = None) -> tuple[str, bool, bool]:
    """Return ``(token, generated, persisted)`` for a LAN-exposed gateway.

    The file on disk is the authority, not the process environment.

    This used to read only ``config.gateway_api_token or os.getenv(...)``, and
    that chain evaluated falsy inside this function on a machine where every
    part of it was truthy one line earlier -- so a token already sitting in
    .env was ignored and a fresh one was generated and written on EVERY boot.
    Measured: the token rotated on each restart, so any value handed to a
    client was stale within minutes. A credential that regenerates itself is
    not a credential.

    Existing configuration is still never overwritten: when a token is found
    anywhere it is used as-is, and only its absence causes a generation.
    """
    from core.config import config

    path = Path(env_path) if env_path else Path(__file__).resolve().parents[1] / ".env"

    for candidate in (
        config.gateway_api_token,
        os.environ.get("HERMES_GATEWAY_TOKEN"),
        _read_token_from_env_file(path),
    ):
        value = str(candidate or "").strip()
        if value:
            os.environ["HERMES_GATEWAY_TOKEN"] = value
            return value, False, False

    token = generate_token()
    persisted = _persist_token(path, token)
    os.environ["HERMES_GATEWAY_TOKEN"] = token
    return token, True, persisted
