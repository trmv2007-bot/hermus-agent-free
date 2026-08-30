"""Canonical memory facade.

``Memory2`` (in :mod:`core.memory2`) is the canonical implementation. This facade
exposes a single, stable API for the whole system and guarantees there is exactly
one writable memory path in production. It never routes writes through the legacy
:mod:`core.memory` v1 store.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

# Canonical memory kinds (must match memory2.KINDS order).
KINDS: tuple[str, ...] = ("working", "episodic", "semantic", "procedural", "project")


class MemoryFacade:
    """One canonical memory API backed by Memory2."""

    def __init__(self, store: Any = None, *, db_path: Optional[str] = None):
        if store is None:
            from ..memory2 import Memory2  # type: ignore
            store = Memory2(db_path=db_path)
        self._store = store
        self._lock = threading.RLock()

    @property
    def store(self) -> Any:
        """The underlying canonical Memory2 store (used for advanced ops)."""
        return self._store

    # -- write -----------------------------------------------------------------
    def remember(self, kind: str, content: str, **kwargs) -> dict[str, Any]:
        """Store one memory in the canonical schema."""
        if kind not in KINDS:
            return {"success": False, "error": f"unknown kind '{kind}' (choose {KINDS})"}
        return self._store.remember(kind, content, **kwargs)

    # -- read ------------------------------------------------------------------
    def recall(self, query: str, *, project: Optional[str] = None,
               kinds: Optional[list[str]] = None, limit: int = 10,
               record_access: bool = True) -> list[dict[str, Any]]:
        return self._store.recall(query, project=project, kinds=kinds,
                                  limit=limit, record_access=record_access)

    def hybrid_recall(self, query: str, *, project: Optional[str] = None,
                      kinds: Optional[list[str]] = None, limit: int = 10,
                      **kw) -> list[dict[str, Any]]:
        try:
            return self._store.hybrid_recall(query, project=project, kinds=kinds,
                                             limit=limit, **kw)
        except Exception:
            return self.recall(query, project=project, kinds=kinds, limit=limit)

    def recall_context(self, query: str, *, budget: int = 2000, **kw) -> str:
        try:
            return self._store.recall_context(query, budget=budget, **kw)
        except AttributeError:
            return ""

    def recall_prompt_block(self, *args, **kw) -> str:
        try:
            return self._store.recall_prompt_block(*args, **kw)
        except AttributeError:
            return ""

    def explain(self, query: str, limit: int = 5, **kw) -> dict[str, Any]:
        try:
            return self._store.explain(query, limit=limit, **kw)
        except AttributeError:
            return {"error": "no explain"}

    def all(self, *, kind: Optional[str] = None, project: Optional[str] = None,
            limit: int = 100) -> list[dict[str, Any]]:
        try:
            return self._store.store.all(kind=kind, project=project, limit=limit)
        except Exception:
            return []

    # -- lifecycle -------------------------------------------------------------
    def forget(self, memory_id: int, *, reason: str = "manual") -> dict[str, Any]:
        try:
            return self._store.forget(memory_id, reason=reason)
        except AttributeError:
            try:
                return self._store.store.forget(memory_id, reason=reason)
            except Exception as exc:
                return {"success": False, "error": str(exc)}

    def stats(self) -> dict[str, Any]:
        try:
            return self._store.stats()
        except AttributeError:
            return {}

    def close(self) -> None:
        try:
            self._store.store.close()
        except Exception:
            pass


_facade: Optional[MemoryFacade] = None
_facade_lock = threading.Lock()


def get_memory() -> MemoryFacade:
    """Return the process-wide canonical memory facade."""
    global _facade
    with _facade_lock:
        if _facade is None:
            _facade = MemoryFacade()
        return _facade
