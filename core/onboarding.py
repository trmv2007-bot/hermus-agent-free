"""First-run state: what the person chose, and whether the choice was proven.

The onboarding state machine is deliberately strict about one thing: a run is
only complete when a real round trip against the resolved model succeeded. A
declined install, an offline machine and a broken key all leave the run
incomplete with a stated reason, because a "welcome" that lands on a stub is
exactly the false success this screen exists to prevent.
"""

from __future__ import annotations

import json
import os
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

# What a stored record's decisions mean, once resolved. A profile is a name for a
# policy; the policy is what the tool path actually enforces, so it is the thing
# worth keeping on disk.
POLICY_VERSION = 1
_STRICTNESS = {"deny": 3, "ask": 2, "allow": 1}


class ProfileError(ValueError):
    """A profile the product cannot honour. Raised before anything is written."""


def resolve_profile(profile: Any, *, source: str = "an unnamed caller") -> str:
    """The known profile this names, or a refusal that says who sent it.

    Every writer of a permission choice goes through here: an unrecognised value
    is refused with its origin named, never quietly replaced by "ask".
    """
    key = str(profile or "").strip()
    if key in PROFILES:
        return key
    supported = ", ".join(PROFILES)
    raise ProfileError(
        f"unknown profile {key or 'an empty value'!r} from {source}: this build honours {supported}; nothing was written"
    )


def _profile_from_decisions(decisions: Any) -> str:
    """The profile a stored decision set resolves to, or "" when it resolves to none.

    Exact match first. Failing that, the narrowest profile the record is still no
    wider than — a hand-edited tool can only ever make a label more conservative,
    never more permissive.
    """
    if not isinstance(decisions, dict) or not decisions:
        return ""
    for key in PROFILES:
        if decisions_for(key) == decisions:
            return key
    for key in PROFILES:
        wanted = decisions_for(key)
        if all(_STRICTNESS.get(str(decisions.get(tool, want)), 0) >= _STRICTNESS.get(want, 0) for tool, want in wanted.items()):
            return key
    return ""


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:  # pragma: no cover - unreadable paths compare as text
        return str(left).lower() == str(right).lower()


def state_path() -> Path:
    """Where the first-run record lives.

    ``HERMUS_HOME`` is honoured when the workspace singleton is still parked in the
    real default home. That is the shape of the incident this guards against: the
    singleton captures its base directory at import, so a process that imports
    almost anything before pointing the environment at a temporary home would
    otherwise keep reading — and writing, and deleting — the person's real
    ``~/.hermus``.

    A session that started on an explicit home keeps it. Reading the variable
    unconditionally would let whichever module sets it last at import time own the
    state file for the whole process, which makes a suite's results depend on
    import order — the same class of surprise, in the other direction.
    """
    from .workspace import workspace

    root = workspace.dirs["root"]
    override = os.environ.get("HERMUS_HOME")
    if override:
        candidate = Path(override).expanduser()
        default_home = Path(os.path.expanduser("~")) / ".hermus"
        if _same_path(root, default_home) and not _same_path(candidate, root):
            return candidate / "onboarding.json"
    return root / "onboarding.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fresh_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "name": DEFAULT_NAME,
        "permissions": DEFAULT_PROFILE,
        "permissions_policy": None,
        "brain": None,
        "check": {"ok": False, "reason": "no model has answered yet", "at": None, "model": None},
        "upgrade": None,
        "upgrade_history": [],
        "completed_at": None,
    }


def _recorded_profile(raw: dict[str, Any]) -> str:
    """The profile a stored record resolves to: its label, else the policy beside it.

    An install written before the policy was kept has only a label; an install
    whose label has since been renamed or removed is read back through the
    decisions it was written with, so the meaning survives the renaming.
    """
    label = str(raw.get("permissions") or "").strip()
    if label in PROFILES:
        return label
    policy = raw.get("permissions_policy")
    if isinstance(policy, dict):
        return _profile_from_decisions(policy.get("decisions"))
    return ""


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
        resolved = _recorded_profile(raw)
        if resolved:
            state["permissions"] = resolved
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
    if not _recorded_profile(state):
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


def record_upgrade(state: dict[str, Any], brain: dict[str, Any], *, previous: str | None, reason: str) -> dict[str, Any]:
    """Replace a recorded model with a stronger one, keeping the run finished.

    A migration, not a new first run: the person's name and permission choice
    stay, and the new model is not treated as proven. ``upgrade.pending_check``
    says a fresh answer is still owed and the next real round trip clears it, so
    the only thing the person redoes is the one step that cannot be skipped.
    """
    at = _now()
    state["brain"] = {
        "kind": brain.get("kind"),
        "provider": brain.get("provider"),
        "name": brain.get("name"),
        "model": brain.get("model"),
        "source": brain.get("source"),
        "reason": reason,
        "upgraded_from": previous,
        "upgraded_at": at,
        "chosen_at": at,
    }
    state["upgrade"] = {"from": previous, "to": brain.get("model"), "reason": reason, "at": at, "pending_check": True}
    history = [row for row in (state.get("upgrade_history") or []) if isinstance(row, dict)]
    state["upgrade_history"] = (history + [dict(state["upgrade"])])[-10:]
    check = state.get("check") or {}
    if check.get("ok") or previous is None:
        state["check"] = {
            "ok": False,
            "model": None,
            "reason": f"the model changed from {previous}; it has not answered yet",
            "at": None,
        }
        state["completed_at"] = None
    save(state)
    return state


def install_has_history() -> bool:
    """Has this workspace been used before, even if its onboarding state is gone?

    Distinguishes a real first run from an install whose state file was lost or
    restored from elsewhere. The workspace's own folders are the evidence: a
    directory that only a used install has, with something in it.
    """
    root = state_path().parent
    for name in ("sessions", "memory", "missions", "checkpoints"):
        folder = root / name
        try:
            if folder.is_dir() and any(folder.iterdir()):
                return True
        except OSError:
            continue
    return False


def upgrade_pending(state: dict[str, Any]) -> bool:
    """Is a recorded model awaiting its first proven answer after an upgrade?"""
    return bool((state.get("upgrade") or {}).get("pending_check"))


def is_usable(state: dict[str, Any]) -> bool:
    """Can the app open on this install?

    A finished run, or one whose model was just upgraded and is about to answer
    for the first time. Kept separate from :func:`is_complete`, which stays
    strict: "the app may open" is not the same claim as "the model has answered".
    """
    return is_complete(state) or upgrade_pending(state)


def record_check(
    state: dict[str, Any], *, ok: bool, model: str | None, reason: str = "", ms: int | None = None
) -> dict[str, Any]:
    state["check"] = {
        "ok": bool(ok),
        "model": model,
        "reason": "" if ok else (reason or "the model did not answer"),
        "at": _now(),
        "ms": ms,
    }
    if ok and upgrade_pending(state):
        state["upgrade"]["pending_check"] = False
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
    not a parallel permission system. An unknown profile is refused rather than
    mapped to the permissive default.
    """
    from .permissions import DEFAULT_POLICY, Capability, Decision, Risk

    profile = resolve_profile(profile, source="decisions_for")
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


def apply_profile(profile: str, *, source: str = "an unnamed caller") -> dict[str, Any]:
    """Write the profile into the tool policy overrides, reversibly.

    Only the non-read tools the profile speaks about are touched; a decision the
    person later changes by hand in Approvals still wins, because that is the
    same file. What is recorded is the resolved policy, not just its name: a
    profile renamed or removed in a later build cannot change or widen what this
    install already agreed to.
    """
    from .permissions import permission_manager

    key = resolve_profile(profile, source=source)
    decisions = decisions_for(key)
    tools = permission_manager.overrides.setdefault("tools", {})
    for tool, decision in decisions.items():
        tools[tool] = decision
    policy = {"version": POLICY_VERSION, "profile": key, "decisions": decisions, "applied_at": _now()}
    permission_manager.overrides["profile"] = key
    permission_manager.overrides["profile_policy"] = policy
    permission_manager._save_overrides()
    return {"profile": key, "tools": len(decisions), "policy": policy}


def current_profile() -> str:
    """The profile in force, resolved from the policy that is enforced.

    The label is only a name. A record whose label has been renamed away still
    resolves to the decisions it was written with, so reading an old install can
    never widen what it is allowed to do.
    """
    from .permissions import permission_manager

    overrides = permission_manager.overrides
    stored = overrides.get("profile_policy")
    if isinstance(stored, dict):
        resolved = _profile_from_decisions(stored.get("decisions"))
        if resolved:
            return resolved
    resolved = _profile_from_decisions(overrides.get("tools"))
    if resolved:
        return resolved
    label = overrides.get("profile")
    return label if label in PROFILES else DEFAULT_PROFILE
