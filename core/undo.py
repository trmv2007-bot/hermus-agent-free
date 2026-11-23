"""Undo as a guarantee, not a feature.

Every mutating action the tool path can perform is journaled *before* it runs:
the exact prior bytes go into a content-addressed blob store, the inverse steps
go into a journal entry, and only then is the action allowed to proceed. If the
inverse of an action cannot be known — a shell command outside a git work tree,
a file above the journaling size cap, a pointer action that moved the cursor —
the entry says so *before* the fact, and the action is recorded as
``not_reversible`` instead of pretending it can be taken back.

Layout (under the workspace root, next to ``checkpoints/``)::

    <workspace>/undo/
        entries/<id>.json      # one entry per action: state, steps, targets
        blobs/<sha256>         # content-addressed prior bytes

The store is bounded: at most :data:`MAX_ENTRIES` entries and
:data:`MAX_STORE_BYTES` on disk, oldest first when trimming. Undoing an entry
verifies that the live state still matches what the action left behind, so an
undo can never overwrite a newer edit by the person; ``force=True`` is the
explicit override.

The five mutating classes the tool path can reach, and what an inverse means
for each:

* file write / append / edit / delete / move — exact prior bytes at the same path
* directory creation / removal — recreated or removed, deepest first
* shell command inside a git work tree — pre-state captured through git, with
  every touched path restored from journaled bytes and HEAD
* settings and policy changes — the prior value of the changed key
* computer control — declared not reversible before the pointer moves
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_ENTRIES = 200
MAX_STORE_BYTES = 64 * 1024 * 1024
MAX_BLOB_BYTES = 8 * 1024 * 1024
MAX_GIT_PATHS = 2000

PENDING = "pending"
APPLIED = "applied"
NOT_REVERSIBLE = "not_reversible"
FAILED = "failed"
UNDONE = "undone"
ABORTED = "aborted"

FILE_WRITE_TOOLS = frozenset({"file_write", "write_file", "create_file", "overwrite_file"})
FILE_APPEND_TOOLS = frozenset({"append_file", "file_append"})
FILE_EDIT_TOOLS = frozenset({"file_edit", "str_replace", "edit_file"})
FILE_DELETE_TOOLS = frozenset({"delete_file", "file_delete", "remove_file"})
FILE_MOVE_TOOLS = frozenset({"move_file", "file_move", "rename_file"})
SHELL_TOOLS = frozenset({"shell_execute", "shell", "run_backend"})

MUTATING_TOOLS = FILE_WRITE_TOOLS | FILE_APPEND_TOOLS | FILE_EDIT_TOOLS | FILE_DELETE_TOOLS | FILE_MOVE_TOOLS | SHELL_TOOLS

_PATH_KEYS = ("path", "file", "filename", "target")
_DEST_KEYS = ("destination", "dst", "dest", "to", "new_path")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"undo_{int(time.time() * 1000)}_{os.getpid():d}_{secrets.token_hex(2)}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path | str) -> str | None:
    """sha256 of a file's bytes, or ``None`` when it cannot be read."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def live_state(path: Path | str) -> dict[str, Any]:
    """What is at ``path`` right now, in the shape steps store as ``expected``."""
    p = Path(path)
    if p.is_dir():
        return {"exists": True, "dir": True, "sha256": None}
    digest = file_sha256(p)
    return {"exists": digest is not None, "dir": False, "sha256": digest}


def _matches(path: Path | str, expected: dict[str, Any] | None) -> bool:
    if not expected:
        return True
    return live_state(path) == expected


@dataclass
class UndoEntry:
    """One journaled action: what ran, what would take it back, and where it stands."""

    id: str
    created_at: str
    tool: str
    kind: str
    description: str
    targets: list[str] = field(default_factory=list)
    reversible: bool = False
    reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    state: str = PENDING
    undone_at: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UndoEntry:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def summary(self) -> dict[str, Any]:
        """The entry as the product shows it: no blob paths, no internal diff."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "tool": self.tool,
            "kind": self.kind,
            "description": self.description,
            "targets": list(self.targets),
            "reversible": self.reversible,
            "reason": self.reason,
            "state": self.state,
            "undone_at": self.undone_at,
            "step_count": len(self.steps),
            "error": self.error,
        }


def _path_arg(args: dict[str, Any], keys: tuple[str, ...] = _PATH_KEYS) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _missing_ancestors(path: Path) -> list[Path]:
    """Directories above ``path`` that do not exist yet, shallowest first."""
    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    return list(reversed(missing))


class UndoJournal:
    """Bounded, atomic journal of inverses for mutating actions."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        max_entries: int = MAX_ENTRIES,
        max_store_bytes: int = MAX_STORE_BYTES,
        max_blob_bytes: int = MAX_BLOB_BYTES,
    ):
        if root is None:
            from .workspace import workspace

            root = workspace.dirs["undo"]
        self.root = Path(root)
        self.entries_dir = self.root / "entries"
        self.blobs_dir = self.root / "blobs"
        self.max_entries = int(max_entries)
        self.max_store_bytes = int(max_store_bytes)
        self.max_blob_bytes = int(max_blob_bytes)
        self._lock = threading.RLock()
        self.entries_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)

    # -- blobs ----------------------------------------------------------
    def put_blob(self, data: bytes) -> str:
        digest = sha256_bytes(data)
        dest = self.blobs_dir / digest
        if not dest.exists():
            tmp = dest.with_name(f"{digest}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        return digest

    def get_blob(self, digest: str) -> bytes:
        data = (self.blobs_dir / digest).read_bytes()
        if sha256_bytes(data) != digest:
            raise ValueError(f"journal blob {digest} is corrupt")
        return data

    # -- entries --------------------------------------------------------
    def _entry_path(self, entry_id: str) -> Path:
        return self.entries_dir / f"{entry_id}.json"

    def _write(self, entry: UndoEntry) -> None:
        path = self._entry_path(entry.id)
        tmp = path.with_name(f"{entry.id}.json.tmp")
        tmp.write_text(json.dumps(entry.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def begin(
        self,
        *,
        tool: str,
        kind: str,
        description: str,
        targets: list[str] | tuple[str, ...] = (),
        steps: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        reversible: bool = True,
        reason: str = "",
        meta: dict[str, Any] | None = None,
        deferred_steps: bool = False,
    ) -> UndoEntry:
        """Journal an intent before anything is touched.

        ``steps`` are listed in the order the action created its effects; the
        undo applies them in reverse. An entry with no steps cannot claim to be
        reversible unless it passes ``deferred_steps`` — the shape of a shell
        command's inverse is only known once the command has run.
        """
        steps = [dict(step) for step in steps]
        claimable = bool(reversible) and (bool(steps) or deferred_steps)
        entry = UndoEntry(
            id=_new_id(),
            created_at=_now(),
            tool=str(tool),
            kind=str(kind),
            description=str(description),
            targets=[str(t) for t in targets],
            steps=steps,
            reversible=claimable,
            reason="" if claimable else (reason or "no known inverse"),
            meta=dict(meta or {}),
        )
        with self._lock:
            self._write(entry)
        return entry

    def finalize(self, entry: UndoEntry, *, ok: bool = True, note: str = "", steps: list[dict] | None = None) -> UndoEntry:
        """Seal an entry after the action: record the state it left behind."""
        with self._lock:
            if steps is not None:
                entry.steps = [dict(step) for step in steps]
            if not ok:
                entry.state = FAILED
                entry.error = note or entry.error or "action failed"
            elif entry.reversible:
                for step in entry.steps:
                    verify = step.get("verify")
                    if verify:
                        step["expected"] = live_state(verify)
                entry.state = APPLIED
            else:
                entry.state = NOT_REVERSIBLE
                if not entry.reason:
                    entry.reason = note or "no known inverse"
            if note and ok:
                entry.error = note
            self._write(entry)
            self.prune()
        return entry

    def abort(self, entry: UndoEntry, reason: str) -> UndoEntry:
        """Mark an entry whose action was prevented from running (brake, refusal)."""
        with self._lock:
            entry.state = ABORTED
            entry.error = str(reason)
            self._write(entry)
        return entry

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            entries = self._load_all()
        return [entry.summary() for entry in entries[: max(1, int(limit))]]

    def get(self, entry_id: str) -> UndoEntry:
        path = self._entry_path(str(entry_id))
        if not path.exists():
            raise KeyError(f"no undo entry {entry_id}")
        return UndoEntry.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _load_all(self) -> list[UndoEntry]:
        entries: list[UndoEntry] = []
        for path in self.entries_dir.glob("*.json"):
            try:
                entries.append(UndoEntry.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001 - a torn entry must not hide the rest
                continue
        entries.sort(key=lambda e: (e.created_at, e.id), reverse=True)
        return entries

    def last(self) -> UndoEntry | None:
        for entry in self._load_all():
            if entry.state in (APPLIED, NOT_REVERSIBLE):
                return entry
        return None

    # -- undo -----------------------------------------------------------
    def undo(self, entry_id: str, *, force: bool = False) -> dict[str, Any]:
        try:
            entry = self.get(entry_id)
        except KeyError:
            return {"success": False, "error": f"no undo entry {entry_id}", "id": str(entry_id)}
        return self._undo_entry(entry, force=force)

    def undo_last(self, *, force: bool = False) -> dict[str, Any]:
        entry = self.last()
        if entry is None:
            return {"success": False, "error": "nothing to undo"}
        return self._undo_entry(entry, force=force)

    def _undo_entry(self, entry: UndoEntry, *, force: bool) -> dict[str, Any]:
        if not entry.reversible:
            return {
                "success": False,
                "error": f"{entry.id} was declared not reversible: {entry.reason}",
                "id": entry.id,
                "reversible": False,
            }
        if entry.state == UNDONE:
            return {"success": False, "error": f"{entry.id} is already undone", "id": entry.id}
        results = []
        for step in reversed(entry.steps):
            results.append(self._apply_step(step, force=force))
        ok = all(item["ok"] for item in results)
        with self._lock:
            entry.state = UNDONE if ok else APPLIED
            entry.undone_at = _now() if ok else ""
            entry.error = "" if ok else "; ".join(item["error"] for item in results if item.get("error"))
            self._write(entry)
        return {
            "success": ok,
            "id": entry.id,
            "description": entry.description,
            "steps": results,
            "nothing_to_do": not entry.steps,
            "error": "" if ok else entry.error,
        }

    def _apply_step(self, step: dict[str, Any], *, force: bool) -> dict[str, Any]:
        op = str(step.get("op") or "")
        handler = {
            "restore_file": self._step_restore_file,
            "remove_file": self._step_remove_file,
            "create_dir": self._step_create_dir,
            "remove_dir": self._step_remove_dir,
            "move": self._step_move,
            "set_setting": self._step_set_setting,
        }.get(op)
        if handler is None:
            return {"op": op, "ok": False, "error": f"unknown undo step '{op}'"}
        try:
            return handler(step, force=force)
        except Exception as exc:  # noqa: BLE001 - one step's failure never aborts the rest
            return {"op": op, "path": step.get("path") or step.get("src"), "ok": False, "error": str(exc)}

    def _step_restore_file(self, step: dict[str, Any], *, force: bool) -> dict[str, Any]:
        path = Path(step["path"])
        expected = step.get("expected")
        if path.exists() and not _matches(path, expected) and not force:
            return {
                "op": "restore_file",
                "path": str(path),
                "ok": False,
                "error": f"{path} changed after the action (sha256 mismatch); rerun with force to restore anyway",
            }
        data = self.get_blob(step["blob"])
        missing = _missing_ancestors(path)
        for directory in missing:
            directory.mkdir(exist_ok=True)
        tmp = path.with_name(f".{path.name}.undo-{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        mode = step.get("mode")
        if mode is not None:
            try:
                os.chmod(path, int(mode))
            except OSError:
                pass
        return {"op": "restore_file", "path": str(path), "ok": True, "sha256": sha256_bytes(data), "created_dirs": len(missing)}

    def _step_remove_file(self, step: dict[str, Any], *, force: bool) -> dict[str, Any]:
        path = Path(step["path"])
        if not path.exists():
            return {"op": "remove_file", "path": str(path), "ok": True, "note": "already absent"}
        if path.is_dir():
            return {"op": "remove_file", "path": str(path), "ok": False, "error": "path is a directory"}
        if not _matches(path, step.get("expected")) and not force:
            return {
                "op": "remove_file",
                "path": str(path),
                "ok": False,
                "error": f"{path} changed after the action (sha256 mismatch); rerun with force to remove it anyway",
            }
        path.unlink()
        return {"op": "remove_file", "path": str(path), "ok": True}

    def _step_create_dir(self, step: dict[str, Any], *, force: bool) -> dict[str, Any]:
        path = Path(step["path"])
        if path.is_dir():
            return {"op": "create_dir", "path": str(path), "ok": True, "note": "already present"}
        if path.exists():
            return {"op": "create_dir", "path": str(path), "ok": False, "error": "path exists and is not a directory"}
        path.mkdir(parents=True)
        return {"op": "create_dir", "path": str(path), "ok": True}

    def _step_remove_dir(self, step: dict[str, Any], *, force: bool) -> dict[str, Any]:
        path = Path(step["path"])
        if not path.exists():
            return {"op": "remove_dir", "path": str(path), "ok": True, "note": "already absent"}
        if not path.is_dir():
            return {"op": "remove_dir", "path": str(path), "ok": False, "error": "path is not a directory"}
        if any(path.iterdir()):
            return {
                "op": "remove_dir",
                "path": str(path),
                "ok": False,
                "error": "directory is not empty; refusing to delete content this action did not create",
            }
        path.rmdir()
        return {"op": "remove_dir", "path": str(path), "ok": True}

    def _step_move(self, step: dict[str, Any], *, force: bool) -> dict[str, Any]:
        source = Path(step["src"])
        dest = Path(step["dst"])
        if not source.exists():
            if dest.exists():
                return {"op": "move", "src": str(source), "dst": str(dest), "ok": True, "note": "already back"}
            return {"op": "move", "src": str(source), "dst": str(dest), "ok": False, "error": "neither the moved file nor its original path exists"}
        if not _matches(source, step.get("expected")) and not force:
            return {
                "op": "move",
                "src": str(source),
                "dst": str(dest),
                "ok": False,
                "error": f"{source} changed after the move (sha256 mismatch); rerun with force to move it back anyway",
            }
        if dest.exists() and not force:
            return {"op": "move", "src": str(source), "dst": str(dest), "ok": False, "error": f"{dest} exists again; refusing to overwrite it"}
        for directory in _missing_ancestors(dest):
            directory.mkdir(exist_ok=True)
        os.replace(source, dest)
        return {"op": "move", "src": str(source), "dst": str(dest), "ok": True}

    def _step_set_setting(self, step: dict[str, Any], *, force: bool) -> dict[str, Any]:
        path = Path(step["path"])
        keys = list(step.get("keys") or [])
        if not keys:
            return {"op": "set_setting", "path": str(path), "ok": False, "error": "no key path recorded"}
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception as exc:  # noqa: BLE001
            return {"op": "set_setting", "path": str(path), "ok": False, "error": f"unreadable settings file: {exc}"}
        expected = step.get("expected")
        live = _nested_value(data, keys)
        expected_value = (expected or {}).get("value") if isinstance(expected, dict) else None
        if expected and live != expected_value and not force:
            return {
                "op": "set_setting",
                "path": str(path),
                "ok": False,
                "error": f"{'.'.join(keys)} changed after the action (was {live!r}, action left {expected_value!r})",
            }
        if step.get("present"):
            _set_nested(data, keys, step.get("value"))
        else:
            _delete_nested(data, keys)
        if step.get("drop_if_empty") and not _has_values(data):
            path.unlink(missing_ok=True)
            return {"op": "set_setting", "path": str(path), "ok": True, "removed_empty_file": True}
        tmp = path.with_name(f".{path.name}.undo-{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        return {"op": "set_setting", "path": str(path), "ok": True, "value": step.get("value"), "present": bool(step.get("present"))}

    # -- pruning --------------------------------------------------------
    def prune(self) -> dict[str, int]:
        """Trim oldest entries past the count and size caps, then drop dead blobs."""
        removed = 0
        with self._lock:
            entries = self._load_all()
            keep = entries[: self.max_entries]
            for entry in entries[self.max_entries :]:
                self._entry_path(entry.id).unlink(missing_ok=True)
                removed += 1
            while keep and self._store_bytes(keep) > self.max_store_bytes:
                entry = keep.pop()
                self._entry_path(entry.id).unlink(missing_ok=True)
                removed += 1
            referenced = {step.get("blob") for entry in keep for step in entry.steps if step.get("blob")}
            blobs = 0
            for blob in self.blobs_dir.glob("*"):
                if blob.suffix == ".tmp":
                    blob.unlink(missing_ok=True)
                    continue
                if blob.name not in referenced:
                    blob.unlink(missing_ok=True)
                    blobs += 1
        return {"entries_removed": removed, "blobs_removed": blobs}

    def _store_bytes(self, entries: list[UndoEntry]) -> int:
        total = 0
        for entry in entries:
            try:
                total += self._entry_path(entry.id).stat().st_size
            except OSError:
                pass
        for digest in {step.get("blob") for entry in entries for step in entry.steps if step.get("blob")}:
            try:
                total += (self.blobs_dir / digest).stat().st_size
            except OSError:
                pass
        return total


def _has_values(data: Any) -> bool:
    """True when a settings document still holds any setting at all."""
    if isinstance(data, dict):
        return any(_has_values(value) for value in data.values())
    if isinstance(data, (list, tuple)):
        return any(_has_values(value) for value in data)
    return data is not None


def _nested_value(data: dict[str, Any], keys: list[str]) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _set_nested(data: dict[str, Any], keys: list[str], value: Any) -> None:
    current = data
    for key in keys[:-1]:
        nxt = current.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            current[key] = nxt
        current = nxt
    current[keys[-1]] = value


def _delete_nested(data: dict[str, Any], keys: list[str]) -> None:
    parents: list[dict[str, Any]] = [data]
    current = data
    for key in keys[:-1]:
        nxt = current.get(key)
        if not isinstance(nxt, dict):
            return
        current = nxt
        parents.append(current)
    current.pop(keys[-1], None)
    for depth in range(len(keys) - 1, 0, -1):
        if parents[depth]:
            break
        parents[depth - 1].pop(keys[depth - 1], None)


# ---------------------------------------------------------------------------
# File and directory captures
# ---------------------------------------------------------------------------


def capture_path(journal: UndoJournal, path: Path | str) -> dict[str, Any]:
    """Everything needed to restore one path exactly, or an honest refusal."""
    p = Path(path)
    if not p.exists():
        return {"existed": False}
    if p.is_dir():
        return {"existed": True, "dir": True}
    size = p.stat().st_size
    if size > journal.max_blob_bytes:
        return {"existed": True, "dir": False, "too_large": size}
    try:
        data = p.read_bytes()
        mode = p.stat().st_mode & 0o777
    except OSError as exc:
        return {"existed": True, "dir": False, "unreadable": str(exc)}
    return {"existed": True, "dir": False, "blob": journal.put_blob(data), "size": size, "mode": mode}


# ---------------------------------------------------------------------------
# Git pre-state for shell commands
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout if binary else result.stdout.decode("utf-8", "surrogateescape")


def git_root(path: Path | str) -> Path | None:
    candidate = Path(path)
    if candidate.is_file():
        candidate = candidate.parent
    out = _git(candidate, "rev-parse", "--show-toplevel")
    if not out:
        return None
    return Path(str(out).strip())


def _status(repo: Path) -> dict[str, str]:
    """`git status --porcelain -z` as ``{posix relpath: XY}``."""
    raw = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all")
    if raw is None:
        return {}
    tokens = str(raw).split("\0")
    out: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if len(token) < 4:
            continue
        code, rel = token[:2], token[3:]
        out[rel] = code
        if "R" in code or "C" in code:
            index += 1  # the rename/copy source follows as its own token
    return out


def capture_git_pre_state(journal: UndoJournal, cwd: Path | str) -> dict[str, Any] | None:
    """Snapshot a git work tree so a shell command's effects can be undone."""
    root = git_root(cwd)
    if root is None:
        return None
    head = _git(root, "rev-parse", "HEAD")
    head_sha = str(head).strip() if head else ""
    status = _status(root)
    if len(status) > MAX_GIT_PATHS:
        return {"root": str(root), "head": head_sha, "error": f"{len(status)} changed paths exceed the {MAX_GIT_PATHS} journaling cap"}
    blobs: dict[str, str] = {}
    modes: dict[str, int] = {}
    unrestorable: list[str] = []
    for rel in status:
        path = root / rel
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > journal.max_blob_bytes:
                unrestorable.append(rel)
                continue
            blobs[rel] = journal.put_blob(path.read_bytes())
            modes[rel] = path.stat().st_mode & 0o777
        except OSError:
            unrestorable.append(rel)
    return {
        "root": str(root),
        "head": head_sha,
        "status": status,
        "blobs": blobs,
        "modes": modes,
        "unrestorable": unrestorable,
    }


def _head_paths(root: Path, head: str) -> set[str] | None:
    raw = _git(root, "ls-tree", "-r", "--name-only", "-z", head)
    if raw is None:
        return None
    return {item for item in str(raw).split("\0") if item}


def build_shell_inverse(journal: UndoJournal, pre: dict[str, Any], post_cwd: Path | str) -> tuple[list[dict], list[str]]:
    """The steps that put a work tree back, plus paths that could not be known."""
    root = Path(pre["root"])
    pre_status: dict[str, str] = dict(pre.get("status") or {})
    pre_blobs: dict[str, str] = dict(pre.get("blobs") or {})
    unrestorable = list(pre.get("unrestorable") or [])
    if unrestorable:
        return [], unrestorable
    post = capture_git_pre_state(journal, post_cwd)
    if post is None or post["root"] != pre["root"]:
        return [], [str(root)]
    if post.get("error"):
        return [], [f"{root}: {post['error']}"]
    if post["head"] != pre["head"]:
        before, after = pre["head"][:8] or "(none)", post["head"][:8] or "(none)"
        return [], [f"{root} HEAD moved {before} -> {after}; undoing git history is not supported"]
    post_status: dict[str, str] = dict(post.get("status") or {})

    touched: set[str] = {rel for rel in set(pre_status) | set(post_status) if pre_status.get(rel) != post_status.get(rel)}
    for rel, digest in pre_blobs.items():
        if (file_sha256(root / rel) or "") != digest:
            touched.add(rel)
    head_set = _head_paths(root, pre["head"]) if pre["head"] else set()

    steps: list[dict] = []
    unknown: list[str] = []
    for rel in sorted(touched):
        path = root / rel
        pre_code = (pre_status.get(rel) or "").strip()
        in_head = rel in head_set
        pre_existed = rel in pre_blobs or (in_head and not pre_code.startswith("D"))
        post_exists = path.exists()
        if not pre_existed and not post_exists:
            continue
        if not pre_existed:
            steps.append({"op": "remove_file", "path": str(path), "verify": str(path)})
            continue
        data: bytes | None = None
        mode = (pre.get("modes") or {}).get(rel)
        if rel in pre_blobs:
            data = journal.get_blob(pre_blobs[rel])
        elif in_head:
            shown = _git(root, "show", f"{pre['head']}:{rel}", binary=True)
            data = shown if isinstance(shown, bytes) else None
            tree = _git(root, "ls-tree", pre["head"], "--", rel)
            if tree and str(tree).strip():
                mode = 0o755 if str(tree).split()[0] == "100755" else 0o644
        if data is None:
            unknown.append(rel)
            continue
        if post_exists and file_sha256(path) == sha256_bytes(data):
            continue
        steps.append({"op": "restore_file", "path": str(path), "blob": journal.put_blob(data), "mode": mode, "verify": str(path)})
        for directory in reversed(_missing_ancestors(path)):
            steps.append({"op": "create_dir", "path": str(directory), "verify": str(directory)})
    if unknown:
        return [], unknown
    return steps, []


# ---------------------------------------------------------------------------
# The tool-path front end
# ---------------------------------------------------------------------------


def _file_steps(journal: UndoJournal, tool: str, args: dict[str, Any]) -> tuple[list[dict], list[str], list[str]]:
    """Inverse steps + targets + unrestorable reasons for one file mutation."""
    steps: list[dict] = []
    targets: list[str] = []
    reasons: list[str] = []
    raw = _path_arg(args)
    if raw is None:
        return [], [], ["no path argument"]
    path = Path(raw).expanduser()
    targets.append(str(path))
    before = capture_path(journal, path)
    if before.get("too_large"):
        reasons.append(f"{path} is {before['too_large']} bytes, above the journaling cap")
    elif before.get("unreadable"):
        reasons.append(f"{path} could not be read: {before['unreadable']}")

    if tool in FILE_DELETE_TOOLS:
        if before.get("existed") and before.get("blob"):
            steps.append({"op": "restore_file", "path": str(path), "blob": before["blob"], "mode": before.get("mode")})
        elif not before.get("existed"):
            reasons.append(f"{path} does not exist, so there is nothing to delete")
        else:
            reasons.append(f"{path} could not be captured before deleting it")
        return steps, targets, reasons

    if tool in FILE_MOVE_TOOLS:
        dest_raw = _path_arg(args, _DEST_KEYS)
        if dest_raw is None:
            return [], targets, ["no destination argument"]
        dest = Path(dest_raw).expanduser()
        targets.append(str(dest))
        if not before.get("existed"):
            reasons.append(f"{path} does not exist, so there is nothing to move")
            return steps, targets, reasons
        steps.append({"op": "move", "src": str(dest), "dst": str(path), "verify": str(dest)})
        return steps, targets, reasons

    if not before.get("existed"):
        for directory in _missing_ancestors(path):
            steps.append({"op": "remove_dir", "path": str(directory)})
        steps.append({"op": "remove_file", "path": str(path), "verify": str(path)})
        return steps, targets, reasons

    if before.get("dir"):
        return [], targets, [f"{path} is a directory"]

    if before.get("blob"):
        steps.append(
            {"op": "restore_file", "path": str(path), "blob": before["blob"], "mode": before.get("mode"), "verify": str(path)}
        )
    return steps, targets, reasons


_DESCRIPTIONS = {
    "write": "write {path}",
    "append": "append to {path}",
    "edit": "edit {path}",
    "delete": "delete {path}",
    "move": "move {path} to {dest}",
    "shell": "run a shell command",
}


def describe_tool(tool: str, args: dict[str, Any]) -> str:
    path = _path_arg(args) or "(no path)"
    if tool in FILE_WRITE_TOOLS:
        return _DESCRIPTIONS["write"].format(path=path)
    if tool in FILE_APPEND_TOOLS:
        return _DESCRIPTIONS["append"].format(path=path)
    if tool in FILE_EDIT_TOOLS:
        return _DESCRIPTIONS["edit"].format(path=path)
    if tool in FILE_DELETE_TOOLS:
        return _DESCRIPTIONS["delete"].format(path=path)
    if tool in FILE_MOVE_TOOLS:
        return _DESCRIPTIONS["move"].format(path=path, dest=_path_arg(args, _DEST_KEYS) or "(no destination)")
    if tool in SHELL_TOOLS:
        command = str(args.get("command") or "")[:120]
        return f"run shell command: {command}"
    return tool


def _kind_of(tool: str) -> str:
    if tool in FILE_WRITE_TOOLS:
        return "file_write"
    if tool in FILE_APPEND_TOOLS:
        return "file_append"
    if tool in FILE_EDIT_TOOLS:
        return "file_edit"
    if tool in FILE_DELETE_TOOLS:
        return "file_delete"
    if tool in FILE_MOVE_TOOLS:
        return "file_move"
    if tool in SHELL_TOOLS:
        return "shell"
    return "other"


def begin_tool(journal: UndoJournal, tool: str, args: dict[str, Any], *, cwd: Path | str | None = None) -> UndoEntry | None:
    """Journal the inverse of a known mutating tool before it runs.

    Returns ``None`` for tools this store does not know how to invert — the
    registry's permission gate still applies to those, but no undo is claimed.
    """
    if tool not in MUTATING_TOOLS:
        return None
    description = describe_tool(tool, args)
    if tool in SHELL_TOOLS:
        workdir = Path(str(args.get("cwd") or cwd or Path.cwd()))
        command = str(args.get("command") or "")
        pre = capture_git_pre_state(journal, workdir)
        targets = [str(workdir)]
        meta: dict[str, Any] = {"command": command, "cwd": str(workdir)}
        if pre is None:
            return journal.begin(
                tool=tool,
                kind="shell",
                description=description,
                targets=targets,
                reversible=False,
                reason=f"{workdir} is not a git work tree, so a shell command's effects cannot be known",
                meta=meta,
            )
        if pre.get("error"):
            return journal.begin(
                tool=tool,
                kind="shell",
                description=description,
                targets=targets,
                reversible=False,
                reason=str(pre["error"]),
                meta=meta,
            )
        meta["git_pre"] = pre
        return journal.begin(
            tool=tool,
            kind="shell",
            description=description,
            targets=targets,
            reversible=True,
            meta=meta,
            deferred_steps=True,
        )

    steps, targets, reasons = _file_steps(journal, tool, args)
    if reasons:
        return journal.begin(
            tool=tool,
            kind=_kind_of(tool),
            description=description,
            targets=targets,
            reversible=False,
            reason="; ".join(reasons),
        )
    return journal.begin(tool=tool, kind=_kind_of(tool), description=description, targets=targets, steps=steps)


def finish_tool(journal: UndoJournal, entry: UndoEntry | None, result: Any) -> UndoEntry | None:
    """Seal the journaled entry once the tool has returned."""
    if entry is None:
        return None
    ok = not (isinstance(result, dict) and result.get("error")) and not (
        isinstance(result, dict) and str(result.get("success", "")).lower() == "false"
    )
    note = str(result.get("error")) if isinstance(result, dict) and result.get("error") else ""
    if entry.kind == "shell" and ok and entry.meta.get("git_pre"):
        pre = entry.meta["git_pre"]
        steps, unknown = build_shell_inverse(journal, pre, Path(entry.meta.get("cwd") or Path.cwd()))
        if unknown:
            entry.reversible = False
            entry.reason = "cannot know the pre-state of: " + ", ".join(unknown[:10])
            entry.steps = []
            return journal.finalize(entry, ok=True, note="")
        return journal.finalize(entry, ok=ok, note=note, steps=steps)
    return journal.finalize(entry, ok=ok, note=note)


class UndoJournalUnavailableError(RuntimeError):
    """The inverse could not be journaled, so the mutation must not run."""

    def __init__(self, tool: str, reason: str):
        super().__init__(f"undo journal unavailable for {tool}: {reason}")
        self.tool = tool
        self.reason = reason


def journaled_tool_call(journal: UndoJournal, tool: str, args: dict[str, Any], fn: Any) -> Any:
    """Run ``fn`` with its inverse journaled before and verified after.

    A mutation whose inverse cannot be recorded is refused: the product never
    acts without an undo record (CREW rule 2), so a journal that cannot write is
    a stop condition rather than a reason to proceed unprotected.
    """
    entry = None
    try:
        entry = begin_tool(journal, tool, args)
    except Exception as exc:  # noqa: BLE001 - reported, never silently ignored
        if tool in MUTATING_TOOLS:
            raise UndoJournalUnavailableError(tool, str(exc)) from exc
        entry = None
    try:
        result = fn()
    except Exception as exc:
        try:
            finish_tool(journal, entry, {"error": str(exc)})
        except Exception:  # noqa: BLE001
            pass
        raise
    try:
        finish_tool(journal, entry, result)
    except Exception:  # noqa: BLE001
        pass
    return result


# ---------------------------------------------------------------------------
# Settings / policy journaling
# ---------------------------------------------------------------------------


def journal_setting(
    journal: UndoJournal,
    *,
    tool: str,
    description: str,
    path: Path | str,
    keys: list[str],
    value: Any,
    present: bool,
    targets: list[str] | None = None,
    drop_if_empty: bool = False,
) -> UndoEntry:
    """Journal a settings/policy key change before it is written."""
    return journal.begin(
        tool=tool,
        kind="settings",
        description=description,
        targets=targets or [str(path), ".".join(keys)],
        steps=[
            {
                "op": "set_setting",
                "path": str(path),
                "keys": list(keys),
                "value": value,
                "present": bool(present),
                "drop_if_empty": bool(drop_if_empty),
                "verify": "",
                "expected": {"value": value if present else None},
            }
        ],
        reversible=True,
    )


def finish_setting(journal: UndoJournal, entry: UndoEntry, value: Any, *, ok: bool = True, note: str = "") -> UndoEntry:
    """Seal a settings entry with the value the change left behind."""
    for step in entry.steps:
        step["expected"] = {"value": value}
    return journal.finalize(entry, ok=ok, note=note)


# ---------------------------------------------------------------------------
# Computer control
# ---------------------------------------------------------------------------


def begin_computer_action(journal: UndoJournal, action: str, args: dict[str, Any]) -> UndoEntry:
    """Journal a pointer/keyboard action as not reversible, before it happens."""
    preview = ", ".join(f"{k}={v}" for k, v in args.items() if v is not None)[:200]
    return journal.begin(
        tool="computer_action",
        kind="computer",
        description=f"computer action {action} ({preview})",
        targets=[],
        reversible=False,
        reason="pointer and keyboard actions cannot be replayed backwards",
        meta={"action": action, "args": dict(args)},
    )


_journal: UndoJournal | None = None
_journal_lock = threading.Lock()


def get_undo_journal(root: Path | str | None = None) -> UndoJournal:
    global _journal
    if root is not None:
        return UndoJournal(root)
    with _journal_lock:
        if _journal is None:
            _journal = UndoJournal()
        return _journal


def reset_undo_journal() -> None:
    """Drop the process-wide journal (tests, or a HERMUS_HOME change)."""
    global _journal
    with _journal_lock:
        _journal = None


__all__ = [
    "ABORTED",
    "APPLIED",
    "FAILED",
    "MUTATING_TOOLS",
    "NOT_REVERSIBLE",
    "PENDING",
    "UNDONE",
    "UndoEntry",
    "UndoJournal",
    "UndoJournalUnavailableError",
    "begin_computer_action",
    "begin_tool",
    "build_shell_inverse",
    "capture_git_pre_state",
    "capture_path",
    "describe_tool",
    "file_sha256",
    "finish_setting",
    "finish_tool",
    "get_undo_journal",
    "git_root",
    "journal_setting",
    "journaled_tool_call",
    "live_state",
    "reset_undo_journal",
    "sha256_bytes",
]
