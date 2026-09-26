"""Nous Portal credentials.

The portal issues short-lived OAuth access tokens (this one expires in about an
hour) alongside a refresh token. The desktop app keeps them in a shared JSON
file and refreshes them in place, so the project reads that file at call time
instead of copying a key into .env - a copied key would be a secret that expires
with no way to renew it.

Resolution order:

1. ``NOUS_API_KEY`` already in the environment (CI, containers, manual override)
2. the shared auth file (``HERMES_NOUS_AUTH_FILE``, else the desktop default)

The resolved token is published back into ``os.environ`` as ``NOUS_API_KEY`` so
the existing provider machinery in :mod:`core.providers` works unchanged - it
already looks for that variable.

Nothing in this module logs, returns, or prints a token.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://inference-api.nousresearch.com/v1"

# Where the desktop app parks the refreshed token.
_FALLBACK_PATHS = (
    Path.home() / "AppData" / "Local" / "hermes" / "shared" / "nous_auth.json",
    Path.home() / ".hermes" / "shared" / "nous_auth.json",
)

_lock = threading.Lock()
_cache: dict[str, Any] = {"token": "", "expires_at": 0.0, "loaded_at": 0.0}
# Refresh this long before actual expiry so an in-flight request never races it.
_EXPIRY_SKEW_SECONDS = 120


def auth_file_path() -> Path | None:
    """Path to the shared auth file, if one exists."""
    override = os.getenv("HERMES_NOUS_AUTH_FILE", "").strip()
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    for candidate in _FALLBACK_PATHS:
        if candidate.exists():
            return candidate
    return None


def _read_auth_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _parse_expiry(value: Any) -> float:
    """Accept the ISO-8601 form the portal writes. Unparseable = already expired."""
    if not isinstance(value, str) or not value.strip():
        return 0.0
    text = value.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return 0.0
        return parsed.timestamp()
    except ValueError:
        return 0.0


def load_credentials(force: bool = False) -> dict[str, Any]:
    """Return ``{token, base_url, expires_at, source}``; never raises.

    An expired token is still returned (it is better to attempt the call and let
    the server answer 401 than to silently downgrade to a local model), but
    ``expired`` is set so the caller can log the truth.
    """
    now = time.time()
    with _lock:
        if (
            not force
            and _cache["token"]
            and now - _cache["loaded_at"] < 30
        ):
            return dict(_cache)

        env_key = os.getenv("NOUS_API_KEY", "").strip()
        if env_key:
            _cache.update(
                {
                    "token": env_key,
                    "expires_at": float("inf"),
                    "loaded_at": now,
                    "base_url": os.getenv("NOUS_BASE_URL", "").strip() or DEFAULT_BASE_URL,
                    "source": "env",
                    "expired": False,
                }
            )
            return dict(_cache)

        path = auth_file_path()
        if path is None:
            _cache.update({"token": "", "loaded_at": now, "source": "missing", "expired": True})
            return dict(_cache)

        data = _read_auth_file(path)
        token = str(data.get("access_token") or "").strip()
        expires_at = _parse_expiry(data.get("expires_at"))
        _cache.update(
            {
                "token": token,
                "expires_at": expires_at,
                "loaded_at": now,
                "base_url": str(data.get("inference_base_url") or "").strip() or DEFAULT_BASE_URL,
                "source": str(path),
                "expired": bool(token) and expires_at and expires_at - _EXPIRY_SKEW_SECONDS <= now,
            }
        )
        return dict(_cache)


def get_access_token() -> str:
    """Current bearer token, or "" when nothing is configured."""
    return str(load_credentials().get("token") or "")


def get_base_url() -> str:
    creds = load_credentials()
    return str(creds.get("base_url") or os.getenv("NOUS_BASE_URL", "").strip() or DEFAULT_BASE_URL)


def is_configured() -> bool:
    return bool(get_access_token())


def token_status() -> dict[str, Any]:
    """Doctor-shaped status. Contains no secret material."""
    creds = load_credentials()
    expires_at = creds.get("expires_at")
    if isinstance(expires_at, float) and expires_at == float("inf"):
        human = "never (env token)"
    elif expires_at:
        remaining = int(expires_at - time.time())
        human = f"in {remaining}s" if remaining > 0 else "expired"
    else:
        human = "unknown"
    return {
        "configured": bool(creds.get("token")),
        "source": creds.get("source", "missing"),
        "expired": bool(creds.get("expired")),
        "expires_in": human,
        "base_url": creds.get("base_url") or DEFAULT_BASE_URL,
    }


def ensure_env() -> str:
    """Publish the token as NOUS_API_KEY so the provider registry finds it."""
    token = get_access_token()
    if token and not os.getenv("NOUS_API_KEY", "").strip():
        os.environ["NOUS_API_KEY"] = token
    base = get_base_url()
    if base and not os.getenv("NOUS_BASE_URL", "").strip():
        os.environ["NOUS_BASE_URL"] = base
    return token
