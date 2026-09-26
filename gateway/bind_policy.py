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


def ensure_gateway_token(env_path: Path | None = None) -> tuple[str, bool, bool]:
    """Return ``(token, generated, persisted)`` for a LAN-exposed gateway.

    Existing configuration is never overwritten. When there is no token and one
    is required, a strong random one is generated, written to ``.env`` so it
    survives restarts, and returned so the caller can show it to the user.
    """
    from core.config import config

    existing = (config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN") or "").strip()
    if existing:
        return existing, False, False
    token = generate_token()
    path = Path(env_path) if env_path else Path(__file__).resolve().parents[1] / ".env"
    persisted = _persist_token(path, token)
    os.environ["HERMES_GATEWAY_TOKEN"] = token
    return token, True, persisted
