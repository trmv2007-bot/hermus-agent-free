"""Crash-safe file writes and advisory file locks.

Mission state is durable background state: multiple queue workers, the CLI,
the scheduler and the dashboard can all touch the same ``msn_*.json`` file, and
an interrupted process must never leave a half-written document behind.

Everything here is stdlib-only and best-effort: on platforms without ``fcntl``
locking degrades to a no-op instead of failing the caller.

Guarantees
----------
``atomic_write_*``
    write to a temp file in the *same* directory → ``flush`` → ``fsync`` →
    ``os.replace`` → ``fsync`` the directory. A reader therefore either sees
    the complete previous document or the complete new one, never a truncated
    mix (a plain ``Path.write_text`` truncates first, so a crash or a
    concurrent reader can observe invalid JSON).

``file_lock``
    exclusive advisory lock (``flock``) around read-modify-write cycles such as
    ``load mission → change budget → save mission``, so two workers cannot
    clobber each other's edits.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Union

try:  # pragma: no cover - platform dependent
    import fcntl
except Exception:  # pragma: no cover - Windows / exotic platforms
    fcntl = None  # type: ignore[assignment]

PathLike = Union[str, Path]


def _fsync_dir(path: Path) -> None:
    """Persist the directory entry created/renamed by ``os.replace``."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def atomic_write_bytes(path: PathLike, data: bytes) -> None:
    """Atomically replace ``path`` with ``data`` (tmp + fsync + rename)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(target))
        _fsync_dir(target.parent)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: PathLike, obj: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(obj, indent=indent, default=str))


@contextmanager
def file_lock(path: PathLike, *, exclusive: bool = True) -> Iterator[None]:
    """Best-effort advisory lock around a read-modify-write cycle.

    The lock file lives next to the protected document (``<name>.lock``). It is
    never removed: unlinking a lock file while another process holds it creates
    the classic "two processes, two inodes, both locked" race.
    """
    lock_path = Path(str(path) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if fcntl is None:  # pragma: no cover - non-POSIX
        yield
        return
    flags = os.O_RDWR | os.O_CREAT
    try:
        fd = os.open(str(lock_path), flags, 0o644)
    except OSError:  # pragma: no cover - unwritable dir
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


def read_json(path: PathLike, default: Optional[Any] = None) -> Any:
    """Read a JSON document, returning ``default`` when it is missing/invalid."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default
