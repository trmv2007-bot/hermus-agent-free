"""Preview before mutation — say exactly what an action will do, then do it.

The product's safety order is *preview → approve → act → record*. This module is
the first step: given a planned action it produces the consequence in the terms a
person reads — the old name and the new name of every path, the count, the bytes
before and after, the setting's prior value — and the last step's handle: a
confirmation token that the acting call must present.

Five classes are described:

* a file batch — write, append, edit, delete, move: path by path, old → new
* a shell command in a git work tree — the repo paths it would touch
* a settings or policy change — the old value → the new value
* a directory create / remove — the path and its depth
* a computer action — the pointer/keyboard effect, declared not reversible

An action this module cannot describe is *refused*: :func:`preview` returns a
``refusal`` and issues no token, so the acting path has nothing to confirm. It is
never attempted blind.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .undo import (
    _DEST_KEYS,
    FILE_APPEND_TOOLS,
    FILE_DELETE_TOOLS,
    FILE_EDIT_TOOLS,
    FILE_MOVE_TOOLS,
    FILE_WRITE_TOOLS,
    SHELL_TOOLS,
    _path_arg,
    git_root,
)

TOKEN_TTL_SECONDS = 120

EXACT = "exact"
ESTIMATED = "estimated"
UNKNOWN = "unknown"

_SHELL_WRITERS = frozenset(
    {"rm", "rmdir", "mv", "cp", "mkdir", "touch", "tee", "truncate", "unzip", "tar", "ln", "chmod", "chown"}
)
_GIT_WRITERS = frozenset({"checkout", "restore", "reset", "clean", "apply", "rm", "stash", "switch", "revert"})


class ActionNotPreviewableError(RuntimeError):
    """Raised when an action cannot be described ahead of time."""

    def __init__(self, tool: str, reason: str):
        super().__init__(f"{tool} cannot be previewed: {reason}")
        self.tool = tool
        self.reason = reason


@dataclass
class Change:
    """One path (or one key) the action will touch."""

    old: str | None
    new: str | None
    before: str
    after: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    """The exact consequence of a planned action."""

    tool: str
    summary: str
    changes: list[Change] = field(default_factory=list)
    certainty: str = EXACT
    reversible: bool = True
    refusal: str = ""
    detail: str = ""

    @property
    def count(self) -> int:
        return len(self.changes)

    @property
    def previewable(self) -> bool:
        return not self.refusal

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "summary": self.summary,
            "count": self.count,
            "certainty": self.certainty,
            "reversible": self.reversible,
            "refusal": self.refusal,
            "detail": self.detail,
            "changes": [change.to_dict() for change in self.changes],
        }


def _exists_kind(path: Path, assume: dict[str, str] | None = None) -> str:
    if assume and str(path) in assume:
        return assume[str(path)]
    if path.is_dir():
        return "directory"
    if path.is_file():
        try:
            return f"file ({path.stat().st_size} bytes)"
        except OSError:
            return "file"
    return "absent"


def _plan_file(tool: str, args: dict[str, Any], assume: dict[str, str] | None = None) -> Plan:
    raw = _path_arg(args)
    if raw is None:
        return Plan(tool=tool, summary="", refusal="no path or file argument to describe")
    path = Path(raw).expanduser()
    before = _exists_kind(path, assume)
    if tool in FILE_DELETE_TOOLS:
        if before == "absent":
            return Plan(tool=tool, summary=f"{path} is already absent", changes=[], detail="nothing to delete")
        return Plan(
            tool=tool,
            summary=f"delete {path}",
            changes=[Change(old=str(path), new=None, before=before, after="absent")],
        )
    if tool in FILE_MOVE_TOOLS:
        dest_raw = _path_arg(args, _DEST_KEYS)
        if dest_raw is None:
            return Plan(tool=tool, summary="", refusal="no destination argument to describe")
        dest = Path(dest_raw).expanduser()
        if before == "absent":
            return Plan(tool=tool, summary=f"{path} is absent", refusal=f"{path} does not exist, so it cannot be moved")
        exists_at_dest = assume.get(str(dest)) if assume and str(dest) in assume else None
        overwritten = dest.exists() if exists_at_dest is None else exists_at_dest != "absent"
        detail = f"{dest} exists and would be replaced" if overwritten else ""
        return Plan(
            tool=tool,
            summary=f"move {path} -> {dest}",
            changes=[Change(old=str(path), new=str(dest), before=before, after=before, detail=detail)],
        )
    content = args.get("content")
    if tool in FILE_WRITE_TOOLS:
        new_bytes = len(str(content).encode("utf-8")) if content is not None else 0
        detail = "overwrites existing bytes" if before != "absent" else "creates a new file"
        return Plan(
            tool=tool,
            summary=f"write {new_bytes} bytes to {path}",
            changes=[Change(old=str(path), new=str(path), before=before, after=f"file ({new_bytes} bytes)", detail=detail)],
        )
    if tool in FILE_APPEND_TOOLS:
        added = len(str(content).encode("utf-8")) if content is not None else 0
        return Plan(
            tool=tool,
            summary=f"append {added} bytes to {path}",
            changes=[Change(old=str(path), new=str(path), before=before, after=f"{before} + {added} bytes")],
        )
    if tool in FILE_EDIT_TOOLS:
        old_text = str(args.get("old_text") or args.get("old_string") or args.get("find") or "")
        new_text = str(args.get("new_text") or args.get("new_string") or args.get("replace") or "")
        occurrences = 0
        if path.is_file():
            try:
                occurrences = path.read_text(encoding="utf-8", errors="ignore").count(old_text) if old_text else 0
            except OSError:
                occurrences = 0
        if not old_text:
            return Plan(tool=tool, summary="", refusal="no text to replace")
        if before != "absent" and occurrences == 0:
            return Plan(tool=tool, summary="", refusal=f"{old_text[:40]!r} does not occur in {path}")
        return Plan(
            tool=tool,
            summary=f"replace {occurrences} occurrence(s) in {path}",
            changes=[
                Change(
                    old=str(path),
                    new=str(path),
                    before=before,
                    after="file (one occurrence replaced)",
                    detail=f"{len(new_text)} bytes replace {len(old_text)} bytes",
                )
            ],
            detail="one occurrence is replaced; the rest of the file is untouched",
        )
    return Plan(tool=tool, summary="", refusal=f"{tool} is not a file action this module knows")


def shell_targets(command: str, cwd: Path | str | None = None) -> tuple[list[Change], str]:
    """The repo paths a shell command names, and how sure the reading is."""
    base = Path(cwd or Path.cwd())
    changes: list[Change] = []
    unrecognized = False
    try:
        segments = [part for part in shlex.split(str(command).replace("&&", " ; ").replace("||", " ; "), posix=True) if part]
    except ValueError:
        return [], UNKNOWN
    current: list[str] = []
    commands: list[list[str]] = []
    for token in segments:
        if token == ";":
            if current:
                commands.append(current)
            current = []
            continue
        current.append(token)
    if current:
        commands.append(current)
    for argv in commands:
        redirect_targets: list[str] = []
        for index, token in enumerate(argv):
            if token in {">", ">>", "1>", "1>>"} and index + 1 < len(argv):
                redirect_targets.append(argv[index + 1])
            elif token.startswith(">") and len(token) > 1:
                redirect_targets.append(token[1:].lstrip(">"))
        for item in redirect_targets:
            target = _resolve(base, item)
            changes.append(Change(old=str(target), new=str(target), before=_exists_kind(target), after="file", detail="redirect"))
        verb = os.path.basename(argv[0]).lower()
        if verb == "git" and len(argv) > 1:
            sub = argv[1].lower()
            if sub in _GIT_WRITERS:
                targets = [item for item in argv[2:] if not item.startswith("-")]
                for item in targets:
                    target = _resolve(base, item)
                    changes.append(Change(old=str(target), new=str(target), before=_exists_kind(target), after="changed by git"))
                continue
            if sub not in {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "add", "commit"}:
                unrecognized = True
            continue
        if verb in _SHELL_WRITERS:
            targets = [item for item in argv[1:] if not item.startswith("-")]
            if not targets:
                unrecognized = True
                continue
            for item in targets:
                target = _resolve(base, item)
                gone = verb in {"rm", "rmdir"}
                changes.append(
                    Change(
                        old=str(target),
                        new=None if gone else str(target),
                        before=_exists_kind(target),
                        after="absent" if gone else "file",
                        detail=verb,
                    )
                )
            continue
        if verb not in {
            "echo",
            "echo.",
            "printf",
            "cat",
            "ls",
            "grep",
            "find",
            "true",
            "sleep",
            "python",
            "python3",
            "pytest",
            "pip",
        }:
            unrecognized = True
    if not changes:
        return [], UNKNOWN
    return changes, (ESTIMATED if unrecognized else EXACT)


def _resolve(base: Path, item: str) -> Path:
    candidate = Path(item).expanduser()
    return candidate if candidate.is_absolute() else (base / candidate)


def _plan_shell(tool: str, args: dict[str, Any]) -> Plan:
    command = str(args.get("command") or "").strip()
    if not command:
        return Plan(tool=tool, summary="", refusal="empty command")
    cwd_raw = args.get("cwd")
    cwd = Path(str(cwd_raw)) if cwd_raw else Path.cwd()
    if not cwd.exists():
        return Plan(tool=tool, summary="", refusal=f"working directory {cwd} does not exist")
    root = git_root(cwd)
    changes, certainty = shell_targets(command, cwd)
    detail = ""
    if root is not None:
        detail = f"inside {root}: undo restores every file this command changes"
    else:
        detail = "outside a git work tree: a change here cannot be undone"
    return Plan(
        tool=tool,
        summary=f"run: {command[:160]}",
        changes=changes,
        certainty=certainty,
        reversible=root is not None,
        detail=detail,
    )


def _plan_setting(tool: str, args: dict[str, Any]) -> Plan:
    if tool == "perms_set":
        name = str(args.get("tool") or "")
        decision = str(args.get("decision") or args.get("decide") or "")
        if not name or not decision:
            return Plan(tool=tool, summary="", refusal="a policy change needs a tool and a decision")
        current = "default"
        try:
            from .permissions import permission_manager

            current = str(permission_manager.check(name, args={}).get("decision") or "default")
        except Exception:  # noqa: BLE001 - a manager that cannot answer is reported honestly
            current = "unknown"
        return Plan(
            tool=tool,
            summary=f"set the permission for {name} to {decision}",
            changes=[Change(old=name, new=name, before=current, after=decision)],
        )
    key = str(args.get("key") or args.get("name") or "")
    if not key:
        return Plan(tool=tool, summary="", refusal="no setting key to describe")
    current = args.get("current")
    new = args.get("value")
    return Plan(
        tool=tool,
        summary=f"set {key}",
        changes=[Change(old=key, new=key, before=repr(current), after=repr(new))],
    )


_BATCH_ACTIONS = {
    "write": "file_write",
    "append": "append_file",
    "edit": "file_edit",
    "delete": "delete_file",
    "move": "move_file",
}


def plan_file_batch(ops: list[dict[str, Any]]) -> Plan:
    """The old name → new name of every path in a file batch, count included."""
    if not isinstance(ops, list) or not ops:
        return Plan(tool="file_batch", summary="", refusal="a batch needs at least one operation")
    changes: list[Change] = []
    assume: dict[str, str] = {}
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            return Plan(tool="file_batch", summary="", refusal=f"operation {index} is not an object")
        action = str(op.get("action") or "")
        tool = _BATCH_ACTIONS.get(action)
        if tool is None:
            return Plan(tool="file_batch", summary="", refusal=f"operation {index} has unknown action {action!r}")
        merged = {key: value for key, value in op.items() if key != "action"}
        plan = _plan_file(tool, merged, assume)
        if not plan.previewable:
            return Plan(tool="file_batch", summary="", refusal=f"operation {index}: {plan.refusal}")
        changes.extend(plan.changes)
        # Later operations in the batch see what this one leaves behind.
        for change in plan.changes:
            if change.new is None:
                assume[str(change.old)] = "absent"
            else:
                assume[str(change.old)] = change.after if change.old == change.new else "absent"
                assume[change.new] = change.after
    return Plan(
        tool="file_batch",
        summary=f"{len(changes)} path(s) change",
        changes=changes,
        detail="every path listed with its old and new name",
    )


def _plan_computer(tool: str, args: dict[str, Any]) -> Plan:
    action = str(args.get("action") or tool)
    detail_args = args.get("args") if isinstance(args.get("args"), dict) else args
    shown = ", ".join(f"{k}={v}" for k, v in detail_args.items() if v is not None)[:160]
    return Plan(
        tool=tool,
        summary=f"computer action {action}" + (f" ({shown})" if shown else ""),
        changes=[],
        reversible=False,
        detail="pointer and keyboard actions cannot be replayed backwards; this is declared before the action runs",
    )


def plan_tool(tool: str, args: dict[str, Any] | None = None) -> Plan | None:
    """Describe what ``tool(**args)`` would do, or ``None`` for a read-only tool."""
    args = dict(args or {})
    if tool in FILE_WRITE_TOOLS | FILE_APPEND_TOOLS | FILE_EDIT_TOOLS | FILE_DELETE_TOOLS | FILE_MOVE_TOOLS:
        return _plan_file(tool, args)
    if tool in SHELL_TOOLS:
        return _plan_shell(tool, args)
    if tool == "computer_action":
        return _plan_computer(tool, args)
    if tool in {"perms_set", "setting_set", "config_set"}:
        return _plan_setting(tool, args)
    if tool == "file_batch":
        return plan_file_batch(args.get("ops") or [])
    return None


def require_preview(tool: str, args: dict[str, Any] | None = None) -> Plan:
    """The plan, or :class:`ActionNotPreviewableError` when the action cannot be described."""
    plan = plan_tool(tool, args)
    if plan is None:
        raise ActionNotPreviewableError(tool, "not a mutating action this module can describe")
    if not plan.previewable:
        raise ActionNotPreviewableError(tool, plan.refusal)
    return plan


# ---------------------------------------------------------------------------
# One keystroke: a preview issues a token, and the act must present it
# ---------------------------------------------------------------------------

_tokens: dict[str, dict[str, Any]] = {}
_tokens_lock = threading.Lock()


def _fingerprint(tool: str, args: dict[str, Any]) -> str:
    payload = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def issue_token(plan: Plan, tool: str, args: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(18)
    with _tokens_lock:
        _expired = [key for key, value in _tokens.items() if value["expires"] < time.time()]
        for key in _expired:
            _tokens.pop(key, None)
        _tokens[token] = {
            "tool": tool,
            "fingerprint": _fingerprint(tool, args),
            "args": dict(args),
            "expires": time.time() + TOKEN_TTL_SECONDS,
        }
    return token


def consume_token(token: str) -> dict[str, Any] | None:
    """Validate and burn a confirmation token; the entry holds the exact args."""
    with _tokens_lock:
        entry = _tokens.pop(str(token), None)
    if entry is None:
        return None
    if entry["expires"] < time.time():
        return None
    return entry


def preview(tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Describe an action and, when it can be described, mint its confirmation token."""
    args = dict(args or {})
    plan = plan_tool(tool, args)
    if plan is None:
        return {"ok": False, "previewable": False, "tool": tool, "refusal": "not a mutating action this module can describe"}
    if not plan.previewable:
        return {"ok": False, "previewable": False, "tool": tool, "refusal": plan.refusal, "plan": plan.to_dict()}
    return {
        "ok": True,
        "previewable": True,
        "tool": tool,
        "plan": plan.to_dict(),
        "token": issue_token(plan, tool, args),
        "expires_in": TOKEN_TTL_SECONDS,
    }


def confirm(token: str, registry: Any = None) -> dict[str, Any]:
    """Act on a previewed action, through the same journaled tool path."""
    entry = consume_token(token)
    if entry is None:
        return {"ok": False, "error": "unknown or expired confirmation token"}
    if registry is None:
        from .tool_registry import tool_registry

        registry = tool_registry
    result = registry.execute(entry["tool"], dict(entry["args"]))
    return {"ok": not (isinstance(result, dict) and result.get("error")), "tool": entry["tool"], "result": result}


__all__ = [
    "ActionNotPreviewableError",
    "Change",
    "EXACT",
    "ESTIMATED",
    "Plan",
    "TOKEN_TTL_SECONDS",
    "UNKNOWN",
    "confirm",
    "consume_token",
    "issue_token",
    "plan_file_batch",
    "plan_tool",
    "preview",
    "require_preview",
    "shell_targets",
]
