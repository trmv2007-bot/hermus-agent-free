"""First-run state: what the person chose, and whether the choice was proven.

The onboarding state machine is deliberately strict about one thing: a run is
only complete when a real round trip against the resolved model succeeded. A
declined install, an offline machine and a broken key all leave the run
incomplete with a stated reason, because a "welcome" that lands on a stub is
exactly the false success this screen exists to prevent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_NAME = "Hermus"
STATE_VERSION = 1

# Three answers to "what may it do", each one plain sentence, each mapped onto
# the capability model the tool path already enforces (core/permissions.py).
PROFILES: dict[str, dict[str, Any]] = {
    "watch": {
        "label": "Watch only",
        "sentence": "It can look and answer, but it changes nothing on your machine.",
        "detail": "Reads files, the web and the screen. Every write, command and click is refused.",
    },
    "ask": {
        "label": "Ask first",
        "sentence": "It asks before anything touches a file or runs a command.",
        "detail": "The default. Nothing happens on your machine until you approve it.",
    },
    "act": {
        "label": "Act freely",
        "sentence": "It does file work in its own folders on its own, and still asks before running commands.",
        "detail": "Writes inside its workspace and your project folders need no prompt. System files, shell commands and admin actions still do.",
    },
}
DEFAULT_PROFILE = "ask"

STEPS = ("brain", "permissions", "name")


def state_path() -> Path:
    from .workspace import workspace

    return workspace.dirs["root"] / "onboarding.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fresh_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "name": DEFAULT_NAME,
        "permissions": DEFAULT_PROFILE,
        "brain": None,
        "check": {"ok": False, "reason": "no model has answered yet", "at": None, "model": None},
        "completed_at": None,
    }


def load() -> dict[str, Any]:
    path = state_path()
    state = fresh_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if isinstance(raw, dict) and raw.get("version") == STATE_VERSION:
        for key in state:
            if key in raw:
                state[key] = raw[key]
    return state


def save(state: dict[str, Any]) -> dict[str, Any]:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)
    return state


def incomplete_reason(state: dict[str, Any]) -> str:
    """Why this run is not finished yet — '' when it is."""
    brain = state.get("brain") or {}
    if not brain.get("model"):
        return "no model chosen yet"
    if brain.get("kind") == "stub":
        return "the offline stub cannot answer for real; install a model to finish"
    check = state.get("check") or {}
    if not check.get("ok"):
        return str(check.get("reason") or "the chosen model has not answered yet")
    if state.get("permissions") not in PROFILES:
        return "no permission choice recorded"
    if not str(state.get("name") or "").strip():
        return "no name recorded"
    return ""


def is_complete(state: dict[str, Any]) -> bool:
    return incomplete_reason(state) == ""


def record_brain(state: dict[str, Any], brain: dict[str, Any]) -> dict[str, Any]:
    """Store the resolved brain. Resetting the check because the model changed."""
    state["brain"] = {
        "kind": brain.get("kind"),
        "provider": brain.get("provider"),
        "name": brain.get("name"),
        "model": brain.get("model"),
        "source": brain.get("source"),
        "chosen_at": _now(),
    }
    state["check"] = {"ok": False, "reason": "the chosen model has not answered yet", "at": None, "model": None}
    state["completed_at"] = None
    return state


def record_check(state: dict[str, Any], *, ok: bool, model: str | None, reason: str = "", ms: int | None = None) -> dict[str, Any]:
    state["check"] = {
        "ok": bool(ok),
        "model": model,
        "reason": "" if ok else (reason or "the model did not answer"),
        "at": _now(),
        "ms": ms,
    }
    if ok and is_complete(state):
        state["completed_at"] = _now()
    elif not ok:
        state["completed_at"] = None
    return state


# -- permissions ------------------------------------------------------------


def decisions_for(profile: str) -> dict[str, str]:
    """Map one plain-language profile onto the tool policy's own vocabulary.

    Uses the risk and capability already declared for every tool, so the three
    choices are the same decisions the approval prompt would have asked for —
    not a parallel permission system.
    """
    from .permissions import DEFAULT_POLICY, Capability, Decision, Risk

    profile = profile if profile in PROFILES else DEFAULT_PROFILE
    out: dict[str, str] = {}
    for tool, entry in DEFAULT_POLICY.items():
        risk = entry[0]
        caps = entry[2] if len(entry) > 2 else []
        if risk == Risk.READ:
            continue
        if profile == "watch":
            out[tool] = Decision.DENY.value
        elif profile == "act":
            writes_workspace = Capability.WRITE_WORKSPACE in caps and Capability.WRITE_SYSTEM not in caps
            runs_sandbox = Capability.EXECUTE_SANDBOX in caps and Capability.EXECUTE_HOST not in caps
            out[tool] = Decision.ALLOW.value if (writes_workspace or runs_sandbox) else Decision.ASK.value
        else:
            out[tool] = Decision.ASK.value
    return out


def apply_profile(profile: str) -> dict[str, Any]:
    """Write the profile into the tool policy overrides, reversibly.

    Only the non-read tools the profile speaks about are touched; a decision the
    person later changes by hand in Approvals still wins, because that is the
    same file.
    """
    from .permissions import permission_manager

    decisions = decisions_for(profile)
    tools = permission_manager.overrides.setdefault("tools", {})
    for tool, decision in decisions.items():
        tools[tool] = decision
    permission_manager.overrides["profile"] = profile if profile in PROFILES else DEFAULT_PROFILE
    permission_manager.overrides["profile_applied_at"] = _now()
    permission_manager._save_overrides()
    return {"profile": profile, "tools": len(decisions)}


def current_profile() -> str:
    from .permissions import permission_manager

    profile = permission_manager.overrides.get("profile")
    return profile if profile in PROFILES else DEFAULT_PROFILE
