"""Durable JSON store for harness state.

One read-modify-write implementation for the three harness documents
(``data/harness/bus.json``, ``data/harness/filewatch.json`` and the per-session
files under ``data/harness/sessions/``). Before this, each module had its own
copy of ``_load``/``_save`` using ``Path.write_text``, which truncates first:
a crash or a concurrent swarm worker could leave a half-written document and
the next read silently returned the empty default, losing every message.

Guarantees
----------
* In-process serialisation via a module ``RLock`` (swarm workers are threads).
* Best-effort cross-process exclusion: ``flock`` on POSIX, an ``O_EXCL``
  sidecar on Windows (``fcntl`` is unavailable there). A stuck lock is broken
  after ``stale_after`` seconds and, as a last resort, the write proceeds
  unguarded rather than hanging a turn.
* Every write is an atomic replace (``atomic_io.atomic_write_json``), so a
  reader always sees the complete previous document or the complete new one.

Known limit: on Windows two processes can still lose an update (last writer
wins) because ``O_EXCL`` cannot be held across a crash. Corruption is
prevented; concurrent updates from *threads* (the normal swarm case) are not
lost.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import atomic_io

_LOCK = threading.RLock()
_WINDOWS_LOCK_TIMEOUT = 5.0
_WINDOWS_STALE_AFTER = 30.0


def _acquire_windows_lock(lock_path: Path) -> int | None:
    """Open an exclusive sidecar lock, returning the fd (``None`` on timeout)."""
    deadline = time.monotonic() + _WINDOWS_LOCK_TIMEOUT
    while True:
        try:
            return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > _WINDOWS_STALE_AFTER:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)
        except OSError:
            return None


@contextmanager
def _exclusive(target: Path) -> Iterator[None]:
    """Serialise access to ``target`` across threads and, best-effort, processes."""
    if atomic_io.fcntl is not None:
        with atomic_io.file_lock(target):
            yield
        return

    lock_path = Path(str(target) + ".winlock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    fd = _acquire_windows_lock(lock_path)
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            finally:
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass


def _load(target: Path, default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Read a document, quarantining an unreadable file instead of silently wiping it."""
    data = atomic_io.read_json(target, None)
    if isinstance(data, dict):
        return data
    if target.exists():
        try:
            target.replace(target.with_suffix(target.suffix + f".corrupt-{int(time.time())}"))
        except OSError:
            pass
    return default_factory()


@contextmanager
def json_store(
    path: str | Path,
    *,
    default_factory: Callable[[], dict[str, Any]] = dict,
) -> Iterator[dict[str, Any]]:
    """Read-modify-write one JSON document atomically.

    ``with json_store(path) as data: data["x"] = 1`` persists on exit.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, _exclusive(target):
        data = _load(target, default_factory)
        yield data
        atomic_io.atomic_write_json(target, data)


def read_doc(
    path: str | Path,
    *,
    default_factory: Callable[[], dict[str, Any]] = dict,
) -> dict[str, Any]:
    """Read a document without taking a write lock (atomic replace makes this safe)."""
    target = Path(path)
    data = atomic_io.read_json(target, None)
    return data if isinstance(data, dict) else default_factory()


def utc_now() -> str:
    """ISO-8601 local-aware timestamp, the format every harness record uses."""
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


__all__ = ["json_store", "read_doc", "utc_now"]
