"""Mission-isolated file evidence.

Why this module exists
----------------------
File changes used to be discovered with a *global timestamp scan*: every file
under ``workspace.root`` and ``Path.cwd()`` whose ``mtime`` was newer than the
mission start counted as mission evidence. That is unsafe for concurrent
autonomous jobs:

* another mission (or a background agent, a cron job, the user's editor) writes
  ``notes.md``  →  this mission now claims ``notes.md`` as its own proof of work;
* a file touched *before* the mission but with a clock-skewed / preserved mtime
  is silently attributed too;
* nothing proves the file was written *by the mission*, only *when*.

This module replaces the timestamp heuristic with a **precise per-mission
baseline + snapshot diff** confined to a mission-scoped set of roots:

    FileSnapshot   path → (mtime_ns, size) fingerprint of a set of roots
    MissionFileScope  the roots one mission is allowed to see + diff helpers

Roots always include the mission's own workspace
(``<HERMUS_HOME>/missions/<mission_id>/workspace``) and, when
``HERMUS_MISSION_SCAN_CWD`` is enabled (default), the process CWD — but never
another mission's directory, and never the standard skip-list
(``.git``, ``node_modules``, caches, …).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from .run_events import record_issue
from .workspace import workspace

#: directories never treated as mission evidence
SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "target", ".next", ".cache",
    ".tox", "site-packages", ".mypy", ".idea", ".vscode", ".eggs", "htmlcov",
})

#: hard cap so a pathological tree cannot stall a mission round
MAX_FILES = 20_000

#: state files written by the engine itself — never evidence of user-facing work
STATE_SUFFIXES = (".json.tmp", ".lock")
STATE_NAMES = frozenset({"manifest.json", "artifacts.json", "checkpoints.json"})


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


#: files up to this size are content-hashed (mtime+size alone misses an
#: in-place rewrite that keeps the same length within one clock tick)
HASH_MAX_BYTES = 1_048_576
#: how much of such a file is hashed (cheap, but catches real edits)
HASH_READ_BYTES = 65_536


def _fingerprint(path: Path, st: os.stat_result) -> tuple[int, int, int]:
    """(mtime_ns, size, content-digest) for one file."""
    digest = 0
    if 0 < st.st_size <= HASH_MAX_BYTES:
        try:
            import hashlib

            with open(path, "rb") as handle:
                data = handle.read(HASH_READ_BYTES)
            digest = int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big")
        except OSError:
            digest = 0
    return (st.st_mtime_ns, st.st_size, digest)


class FileSnapshot:
    """Immutable fingerprint of a set of files.

    ``path → (mtime_ns, size, content_digest)`` — the digest makes an in-place
    rewrite detectable even when mtime granularity and file size stay the same.
    """

    __slots__ = ("entries",)

    def __init__(self, entries: dict[str, tuple[int, int, int]]):
        self.entries = entries

    # -- construction ---------------------------------------------------
    @classmethod
    def capture(
        cls,
        roots: Iterable[Path],
        *,
        skip_dirs: Iterable[str] = SKIP_DIRS,
        max_files: int = MAX_FILES,
    ) -> "FileSnapshot":
        entries: dict[str, tuple[int, int, int]] = {}
        skip = set(skip_dirs)
        seen = 0
        for raw_root in roots:
            if not raw_root:
                continue
            root = Path(raw_root)
            if not root.exists():
                continue
            try:
                if root.is_file():
                    st = root.stat()
                    entries[str(root)] = _fingerprint(root, st)
                    continue
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in skip]
                    for fname in filenames:
                        seen += 1
                        if seen > max_files:
                            return cls(entries)
                        if fname in STATE_NAMES or fname.endswith(STATE_SUFFIXES):
                            continue
                        fp = Path(dirpath) / fname
                        try:
                            st = fp.stat()
                        except OSError:
                            continue
                        entries[str(fp)] = _fingerprint(fp, st)
            except Exception as exc:
                record_issue(
                    "mission_files", "snapshot", exc, retryable=False,
                    fallback=f"snapshot incomplete for {root}",
                )
        return cls(entries)

    # -- queries --------------------------------------------------------
    def diff(self, newer: "FileSnapshot") -> list[str]:
        """Paths added or modified between this baseline and ``newer``."""
        changed: list[str] = []
        for path, fingerprint in newer.entries.items():
            if self.entries.get(path) != fingerprint:
                changed.append(path)
        return sorted(changed)

    def removed(self, newer: "FileSnapshot") -> list[str]:
        return sorted(p for p in self.entries if p not in newer.entries)

    def __len__(self) -> int:
        return len(self.entries)


class MissionFileScope:
    """The file-evidence scope of exactly one mission.

    ``roots`` are resolved once (mission workspace + optional extras, minus
    every *other* mission's directory) and every diff afterwards uses precise
    snapshot comparison instead of "mtime > mission start".
    """

    def __init__(self, mission_id: str, roots: list[Path]):
        self.mission_id = str(mission_id)
        self.roots: list[Path] = [Path(r) for r in roots if r]
        self.baseline: FileSnapshot = FileSnapshot.capture(self.roots)

    # -- construction ---------------------------------------------------
    @staticmethod
    def mission_workspace_root(mission_id: str) -> Path:
        try:
            return workspace.mission_workspace(mission_id)
        except Exception:
            return Path(os.getcwd()) / "missions" / str(mission_id) / "workspace"

    @classmethod
    def open(
        cls,
        mission_id: str,
        *,
        extra_roots: Optional[Iterable[Path]] = None,
        include_cwd: Optional[bool] = None,
    ) -> "MissionFileScope":
        """Create the scope and take the mission baseline snapshot.

        ``include_cwd`` defaults to ``HERMUS_MISSION_SCAN_CWD`` (on): real
        coding missions write into the checked-out project, so the CWD must
        stay visible — but only files *this* mission created or modified there
        are reported, and other missions' directories are excluded.
        """
        roots: list[Path] = [cls.mission_workspace_root(mission_id)]
        if include_cwd is None:
            include_cwd = _env_flag("HERMUS_MISSION_SCAN_CWD", True)
        if include_cwd and _env_flag("HERMUS_MISSION_ISOLATION", True):
            cwd = Path.cwd()
            if cwd not in roots:
                roots.append(cwd)
        for r in (extra_roots or []):
            if Path(r) not in roots:
                roots.append(Path(r))
        roots = cls._exclude_other_missions(mission_id, roots)
        return cls(mission_id, roots)

    @staticmethod
    def _exclude_other_missions(mission_id: str, roots: list[Path]) -> list[Path]:
        """Drop roots that belong to a *different* mission."""
        cleaned: list[Path] = []
        try:
            missions_root = Path(workspace.dirs["missions"]).resolve()
        except Exception:
            missions_root = None
        for root in roots:
            try:
                resolved = Path(root).resolve()
            except OSError:
                cleaned.append(root)
                continue
            if (
                missions_root is not None
                and resolved != missions_root
                and missions_root in resolved.parents
                # <missions>/<id>[/workspace|/artifacts|...]
                and resolved.relative_to(missions_root).parts[0] != str(mission_id)
            ):
                continue
            cleaned.append(root)
        return cleaned

    # -- evidence -------------------------------------------------------
    def snapshot(self) -> FileSnapshot:
        """Current fingerprint (used for per-node diffs)."""
        return FileSnapshot.capture(self.roots)

    def changed_since(self, snapshot: FileSnapshot) -> list[str]:
        """Files created/modified by the mission since ``snapshot`` was taken."""
        return snapshot.diff(self.snapshot())

    def changed_since_baseline(self) -> list[str]:
        """Everything this mission touched since it started."""
        return self.changed_since(self.baseline)

    def contains(self, path: str) -> bool:
        """Is ``path`` inside this mission's evidence scope?"""
        try:
            resolved = Path(path).resolve()
        except OSError:
            return False
        for root in self.roots:
            try:
                root_resolved = Path(root).resolve()
            except OSError:
                continue
            if resolved == root_resolved or root_resolved in resolved.parents:
                return True
        return False

    def filter(self, paths: Iterable[str]) -> list[str]:
        """Keep only paths inside this mission's scope (concurrency guard)."""
        return [p for p in paths if self.contains(p)]

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "roots": [str(r) for r in self.roots],
            "baseline_files": len(self.baseline),
        }
