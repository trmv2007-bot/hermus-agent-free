"""The handful of environment settings a person may change from the dashboard.

Everything Hermus reads is an environment variable (see ``.env.example``), and
until now every one of them needed a text editor and a restart. This module owns
the small, safe subset the Control Room may read and write — ports, sandbox
limits, the default model, log level — so the dashboard is a real configuration
surface instead of a viewer.

Two rules keep it safe:

* **A whitelist, not a blacklist.** Only the keys in :data:`CHANGEABLE` can be
  written. Anything that looks like a credential is refused even if someone
  later adds it to that dict by mistake (:func:`_is_secret`).
* **Secrets are never read back.** The dashboard shows whether a provider key is
  configured, never its value; that stays ``core.multi_key``'s job.

Writes land in ``.env`` at the repository root, which the installers and the
gateway already load, and are reported as needing a restart because
``core.config.config`` is a settings singleton fixed at import.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .config import config

#: Writable settings: name -> one line the dashboard shows beside it.
CHANGEABLE: dict[str, str] = {
    "HERMUS_MODEL": "Default model as provider/model, or 'auto' to let the router choose",
    "HERMUS_GATEWAY_PORT": "Port the gateway listens on",
    "HERMUS_SANDBOX": "Command sandbox backend: auto, local, docker, podman or off",
    "HERMUS_SANDBOX_NETWORK": "1 lets sandboxed commands reach the network, 0 blocks them",
    "HERMUS_SANDBOX_MEMORY_MB": "Memory cap for one sandboxed command",
    "HERMUS_ASK_POLICY": "What happens to a yellow-zone action: allow, ask or deny",
    "HERMUS_LOG_LEVEL": "Log level: DEBUG, INFO, WARNING or ERROR",
    "HERMUS_RATE_LIMIT_PER_MINUTE": "Per-client request limit; 0 turns limiting off",
}

#: Values a setting may take, where the set is closed. ``None`` means free text.
CHOICES: dict[str, tuple[str, ...] | None] = {
    "HERMUS_SANDBOX": ("auto", "local", "docker", "podman", "off"),
    "HERMUS_ASK_POLICY": ("allow", "ask", "deny"),
    "HERMUS_LOG_LEVEL": ("DEBUG", "INFO", "WARNING", "ERROR"),
    "HERMUS_SANDBOX_NETWORK": ("0", "1"),
    "HERMUS_GATEWAY_PORT": None,
    "HERMUS_SANDBOX_MEMORY_MB": None,
    "HERMUS_RATE_LIMIT_PER_MINUTE": None,
    "HERMUS_MODEL": None,
}

_NUMERIC = {"HERMUS_GATEWAY_PORT", "HERMUS_SANDBOX_MEMORY_MB", "HERMUS_RATE_LIMIT_PER_MINUTE"}
_SECRET = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL|PASSWD)", re.I)
_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


class SettingError(ValueError):
    """The key or value is not something the dashboard may write."""


def _env_path() -> Path:
    return config.base_dir / ".env"


def _is_secret(name: str) -> bool:
    return bool(_SECRET.search(name))


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LINE.match(line)
        if match:
            out[match.group(1)] = line.split("=", 1)[1].strip()
    return out


def validate(name: str, value: str) -> str:
    """Return the normalised value, or raise :class:`SettingRefused`."""
    name = str(name).strip()
    if _is_secret(name):
        raise SettingError("credentials are never written from the dashboard")
    if name not in CHANGEABLE:
        raise SettingError(f"{name} is not a dashboard setting (allowed: {', '.join(sorted(CHANGEABLE))})")
    value = str(value).strip()
    if not value:
        raise SettingError("a value is required")
    if name in _NUMERIC and not value.isdigit():
        raise SettingError(f"{name} takes a whole number")
    choices = CHOICES.get(name)
    if choices and value not in choices and value.lower() not in [c.lower() for c in choices]:
        raise SettingError(f"{name} must be one of: {', '.join(choices)}")
    return value


def read_settings() -> dict:
    """Current values, where each came from, and what the dashboard can change."""
    stored = _read_env_file(_env_path())
    rows = []
    for name, about in CHANGEABLE.items():
        env_value = os.environ.get(name)
        file_value = stored.get(name)
        source = "env" if env_value else ("file" if file_value else "default")
        rows.append(
            {
                "key": name,
                "value": env_value or file_value or "",
                "source": source,
                "about": about,
                "choices": list(CHOICES.get(name) or []),
            }
        )
    return {
        "count": len(rows),
        "file": str(_env_path()),
        "file_exists": _env_path().is_file(),
        "settings": rows,
        "restart_required": True,
    }


def write_setting(name: str, value: str) -> dict:
    """Persist one setting to ``.env``, preserving every other line.

    The value also goes into this process's environment so a setting read per
    call (log level, rate limit) takes effect at once; anything read from the
    settings singleton still needs a restart, which the response says.
    """
    name = str(name).strip()
    value = validate(name, value)
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    written = False
    for i, line in enumerate(lines):
        match = _LINE.match(line)
        if match and match.group(1) == name:
            lines[i] = f"{name}={value}"
            written = True
            break
    if not written:
        lines.append(f"{name}={value}")
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    os.environ[name] = value
    return {
        "success": True,
        "key": name,
        "value": value,
        "file": str(path),
        "restart_required": True,
        "note": "restart the gateway for this to take effect",
    }


__all__ = ["CHANGEABLE", "SettingError", "read_settings", "validate", "write_setting"]
