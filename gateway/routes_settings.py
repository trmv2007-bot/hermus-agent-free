"""Settings that are actually settings.

The old "diagnostics" surface was an iframe of the control room — a read-only
dump of numbers. This is the opposite: a small, explicit, writable
configuration surface backed by the same ``.env`` the process already reads.

Three rules, each of which exists because the alternative has already gone
wrong somewhere in this repository.

1. A secret is never returned, only its presence and last four characters.
   A settings page that echoes your API key back to the browser has turned a
   local config file into a value in browser history, in a React devtools
   panel, and in anything that can read the DOM. The write accepts a real
   value; the read never produces one.

2. Writing a key is separate from saving a key.
   An empty field means "leave this alone", not "clear this". Clearing a
   working API key because a form was submitted with an empty input is the
   kind of bug that only shows up when it is too late to matter.

3. Only keys on an allowlist can be written.
   This route edits a file the process reads at import time. Accepting an
   arbitrary key name would let a request set something like PYTHONPATH or
   PATH and change what the next process start does — a config editor that is
   also a code execution primitive. The allowlist is the security boundary,
   not a validation nicety.

The gateway reads ``.env`` at import, so writes take effect on restart. That
is stated in every response rather than left for the user to discover.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter()

ENV_PATH = Path(os.environ.get("HERMUS_ENV_FILE", Path(__file__).resolve().parents[1] / ".env"))


class Setting:
    """One configurable value, and whether it is a secret."""

    def __init__(self, key: str, label: str, group: str, secret: bool = False, help: str = ""):
        self.key = key
        self.label = label
        self.group = group
        self.secret = secret
        self.help = help


# The complete writable surface. Adding a key here is a deliberate act.
SETTINGS: tuple[Setting, ...] = (
    # --- models ------------------------------------------------------------
    Setting("HERMES_MODEL", "Main model", "models", help="The model that answers. provider/model, e.g. nvidia/nemotron-3-super-120b-a12b"),
    Setting("HERMES_FALLBACK_MODEL", "Fallback model", "models", help="Used when the main model fails."),
    Setting("HERMES_BASE_URL", "Custom base URL", "models", help="Leave empty to use the provider default."),
    # --- keys --------------------------------------------------------------
    Setting("NVIDIA_API_KEY", "NVIDIA API key", "keys", secret=True, help="Free NIM models. nvidia.build"),
    Setting("OPENROUTER_API_KEY", "OpenRouter key", "keys", secret=True, help="openrouter.ai"),
    Setting("OPENAI_API_KEY", "OpenAI key", "keys", secret=True),
    Setting("ANTHROPIC_API_KEY", "Anthropic key", "keys", secret=True),
    Setting("GOOGLE_API_KEY", "Google AI Studio key", "keys", secret=True, help="Free Gemini tier."),
    Setting("NOUS_API_KEY", "Nous Portal key", "keys", secret=True),
    Setting("GROQ_API_KEY", "Groq key", "keys", secret=True),
    # --- voice -------------------------------------------------------------
    Setting("HERMUS_PIPER_MODEL", "Piper voice", "voice", help="Path to a .onnx voice file. Without this, HERMUS cannot speak."),
    Setting("HERMUS_VOICE_ENABLED", "Voice mode", "voice", help="1 or 0"),
    Setting("HERMUS_VOICE_ACK_MODE", "Acknowledge mode", "voice", help="canned = instant, llm = a model writes the filler"),
    Setting("HERMUS_VOICE_SPEAK_ANSWER", "Speak answers", "voice", help="1 or 0"),
    # --- room --------------------------------------------------------------
    Setting("HERMUS_GATEWAY_HOST", "Gateway host", "room"),
    Setting("HERMUS_GATEWAY_PORT", "Gateway port", "room"),
    Setting("HERMUS_GATEWAY_TOKEN", "Gateway token", "room", secret=True, help="Leave empty for local-only access."),
    Setting("HERMUS_MEMORY_DIR", "Memory directory", "room"),
)

BY_KEY = {s.key: s for s in SETTINGS}
GROUPS: list[str] = []
for _s in SETTINGS:
    if _s.group not in GROUPS:
        GROUPS.append(_s.group)


def _read_env() -> dict[str, str]:
    """Parse .env without executing anything in it."""
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _mask(value: str) -> str:
    """Enough to recognise a key, never enough to use one."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{'•' * 8}{value[-4:]}"


def _live_value(key: str, file_values: dict[str, str]) -> tuple[str, str]:
    """(value to display, source) — the process env wins over the file.

    The file is what a write changes; the live process env is what is
    actually in use right now. Showing only the file would claim a change
    took effect when it has not, which is the whole class of bug this page
    exists to end.
    """
    if key in os.environ and os.environ[key]:
        return os.environ[key], "process"
    if key in file_values:
        return file_values[key], "file"
    return "", "unset"


@router.get("/settings")
async def read_settings():
    """Every writable setting, with secrets masked.

    Reports a `source` per key so the UI can distinguish "you changed this and
    it is live" from "you changed this and it needs a restart".
    """
    file_values = _read_env()
    items = []
    for setting in SETTINGS:
        value, source = _live_value(setting.key, file_values)
        items.append(
            {
                "key": setting.key,
                "label": setting.label,
                "group": setting.group,
                "secret": setting.secret,
                "help": setting.help,
                "source": source,
                "set": bool(value),
                "value": _mask(value) if setting.secret else value,
            }
        )
    return {
        "settings": items,
        "groups": GROUPS,
        "env_path": str(ENV_PATH),
        "env_exists": ENV_PATH.exists(),
        "requires_restart": True,
    }


@router.post("/settings")
async def write_settings(payload: dict):
    """Save values. An empty value for a secret means "leave it"."""
    updates = payload.get("set")
    if not isinstance(updates, dict) or not updates:
        return JSONResponse({"ok": False, "error": "nothing to save — send a 'set' object"}, status_code=400)

    unknown = [k for k in updates if k not in BY_KEY]
    if unknown:
        return JSONResponse(
            {
                "ok": False,
                "error": f"not a configurable setting: {', '.join(sorted(unknown))}",
                "configurable": sorted(BY_KEY),
            },
            status_code=400,
        )

    current = _read_env()
    changed: list[str] = []
    skipped: list[str] = []
    cleared: list[str] = []

    for key, raw in updates.items():
        setting = BY_KEY[key]
        value = "" if raw is None else str(raw)
        # An empty secret field is an untouched field, not a delete. Deleting
        # needs to be explicit, because a form submit with a blank input would
        # otherwise wipe a working key.
        if setting.secret and not value.strip():
            skipped.append(key)
            continue
        if not value.strip():
            current.pop(key, None)
            cleared.append(key)
        else:
            if current.get(key) != value:
                current[key] = value
                changed.append(key)

    if not (changed or cleared):
        return {"ok": True, "changed": [], "cleared": [], "skipped": skipped, "restart_required": False}

    _write_env(current)
    log.info("settings written: changed=%s cleared=%s", changed, cleared)
    # The process env is NOT mutated here. Doing so would make the gateway
    # disagree with the file it will read on next start, and would leave a
    # half-applied config that no restart can reproduce.
    return {
        "ok": True,
        "changed": changed,
        "cleared": cleared,
        "skipped": skipped,
        "restart_required": True,
        "message": f"written to {ENV_PATH} — restart HERMUS for it to take effect",
    }


def _write_env(values: dict[str, str]) -> None:
    """Rewrite .env, preserving comments and unrelated keys.

    A settings page that strips every comment out of your .env the first time
    you save a key is a settings page nobody will use twice.
    """
    preserved: list[str] = []
    seen: set[str] = set()
    out: list[str] = []

    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                preserved.append(line)
                continue
            key = stripped.partition("=")[0].strip()
            if key in values:
                out.append(f"{key}={values[key]}")
                seen.add(key)
            elif key in BY_KEY:
                # A known setting that was cleared — drop it.
                continue
            else:
                preserved.append(line)
    else:
        preserved.append("# HERMUS settings — written by the settings surface")

    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file and move it into place. A crash mid-write would
    # otherwise leave a truncated .env and take the whole gateway down on next
    # start, which is the one outcome worse than losing a setting.
    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join([*preserved, *out, ""]), encoding="utf-8")
    tmp.replace(ENV_PATH)
    # Defence in depth: if this ever gets committed by accident, do not let it.
    gitignore = ENV_PATH.parent / ".gitignore"
    if gitignore.exists() and ".env" not in gitignore.read_text(encoding="utf-8", errors="replace"):
        with gitignore.open("a", encoding="utf-8") as fh:
            fh.write("\n# settings surface writes this file\n.env\n")
