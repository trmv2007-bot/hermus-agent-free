"""SQLite connection lifecycle registry — no connection outlives the process.

Hermus keeps several long-lived SQLite handles on purpose: ``core.memory``
caches one connection per worker thread, ``core.memory2`` shares one behind a
lock, ``hybrid_search`` wraps a shared handle in ``LockedConnection`` and
``tools.internet_eyes`` keeps a web-read cache open for the life of the
process.  All of that is correct while the gateway runs, but nothing ever
*closed* those handles: at interpreter shutdown CPython finalised them during
GC and printed a wall of

    <sys>:0: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x...>
    ResourceWarning: Enable tracemalloc to get the object allocation traceback

— one line per leaked handle (a busy gateway leaks dozens, because the
thread-local cache multiplies per worker thread).

This module is the single place that owns those handles' lifetime:

* :func:`open_db` opens *and* registers a connection (use it for the
  long-lived ones; short-lived per-call connections should use :func:`using`
  so an exception cannot leak them either).
* :meth:`ConnectionRegistry.close_all` closes everything still open.  The
  gateway lifespan calls it in its ``finally:`` block, and an ``atexit`` hook
  covers CLI/TUI runs — so a ``Ctrl+C`` on the gateway is a clean exit.

The registry holds **strong** references on purpose: ``sqlite3.Connection``
does not support weak references (``weakref.ref`` raises ``TypeError``), and a
handle nobody closed is exactly the handle we need to be able to close.
Closing is best-effort and never raises — shutdown must not become a new
failure mode just because a worker thread is mid-commit.
"""
from __future__ import annotations

import atexit
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from collections.abc import Iterator


class _Handle:
    """Bookkeeping for one registered connection."""

    __slots__ = ("conn", "path", "owner", "thread", "closed")

    def __init__(self, conn: sqlite3.Connection, path: str, owner: str, thread: str) -> None:
        self.conn = conn
        self.path = path
        self.owner = owner
        self.thread = thread
        self.closed = False

    def brief(self) -> dict[str, Any]:
        return {"path": self.path, "owner": self.owner, "thread": self.thread}


class ConnectionRegistry:
    """Tracks every long-lived SQLite connection Hermus opens."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handles: dict[int, _Handle] = {}
        self._closed_total = 0
        # Bumped by every close_all(). Long-lived owners (core.memory's
        # thread-local handle, memory2's shared handle) compare the generation
        # they opened with: a gateway shutdown closes their connection, and the
        # next access after that must reopen instead of touching a closed
        # database ("Cannot operate on a closed database").
        self._generation = 0

    @property
    def generation(self) -> int:
        """Monotonic counter of completed ``close_all`` sweeps."""
        with self._lock:
            return self._generation

    # ------------------------------------------------------------------ bookkeeping
    def register(
        self,
        conn: sqlite3.Connection,
        *,
        path: str = "",
        owner: str = "",
    ) -> sqlite3.Connection:
        """Record ``conn`` so :meth:`close_all` can release it at shutdown."""
        handle = _Handle(
            conn,
            str(path or ""),
            str(owner or ""),
            threading.current_thread().name,
        )
        with self._lock:
            self._handles[id(conn)] = handle
        return conn

    def unregister(self, conn: sqlite3.Connection) -> None:
        """Stop tracking ``conn`` (the caller owns closing it)."""
        with self._lock:
            self._handles.pop(id(conn), None)

    def open_handles(self) -> list[dict[str, Any]]:
        """Snapshot of the connections currently held open (for telemetry)."""
        with self._lock:
            return [h.brief() for h in self._handles.values()]

    def is_open(self, conn: sqlite3.Connection) -> bool:
        """``True`` while this connection is still registered."""
        with self._lock:
            return id(conn) in self._handles

    # ------------------------------------------------------------------ lifecycle
    def close_all(self, reason: str = "shutdown") -> dict[str, Any]:
        """Close every registered connection exactly once.

        Safe to call repeatedly (gateway lifespan + atexit both call it) and
        safe to call while a worker thread is mid-query: a concurrent close
        surfaces as ``sqlite3.ProgrammingError`` here and is swallowed rather
        than propagated out of the shutdown path.
        """
        closed, already, errors = 0, 0, []
        with self._lock:
            handles = list(self._handles.values())
            self._handles.clear()
        for handle in handles:
            try:
                handle.conn.close()
                closed += 1
            except sqlite3.ProgrammingError:
                already += 1  # caller already closed it — nothing to do
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                errors.append(f"{handle.owner or handle.path or 'sqlite'}: {exc}")
            finally:
                handle.closed = True
        with self._lock:
            self._closed_total += closed
            self._generation += 1
        return {
            "closed": closed,
            "already_closed": already,
            "errors": errors,
            "reason": reason,
            "generation": self._generation,
        }

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "open": len(self._handles),
                "closed_total": self._closed_total,
                "handles": [h.brief() for h in self._handles.values()],
            }


# Process-wide registry (imported as ``from core.db_registry import db_registry``).
db_registry = ConnectionRegistry()


def open_db(
    path: Any,
    *,
    owner: str = "",
    **kwargs: Any,
) -> sqlite3.Connection:
    """``sqlite3.connect`` + registration, for handles that live for a while.

    ``path`` may be a ``Path``; ``kwargs`` are passed straight through
    (``timeout=``, ``check_same_thread=`` …) so existing call sites keep their
    exact semantics.
    """
    conn = sqlite3.connect(str(path), **kwargs)
    return db_registry.register(conn, path=str(path), owner=owner or Path(str(path)).name)


@contextmanager
def using(path: Any, *, owner: str = "", **kwargs: Any) -> Iterator[sqlite3.Connection]:
    """Short-lived connection that is closed even when the body raises.

    The bare ``conn = sqlite3.connect(...)`` + ``conn.close()`` pattern leaks
    the handle whenever the code in between throws (a locked database, a
    missing table, a ``KeyboardInterrupt``), which is how a busy gateway
    accumulated unclosed connections in the first place.
    """
    conn = sqlite3.connect(str(path), **kwargs)
    db_registry.register(conn, path=str(path), owner=owner or Path(str(path)).name)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        db_registry.unregister(conn)


def close_all(reason: str = "shutdown") -> dict[str, Any]:
    """Module-level shortcut used by the gateway lifespan and ``atexit``."""
    return db_registry.close_all(reason=reason)


# Backstop for entry points that never reach the gateway lifespan (CLI, TUI,
# one-off scripts, pytest): interpreter exit still closes what we opened.
atexit.register(close_all, "atexit")
