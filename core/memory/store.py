"""Canonical memory facade — the ONLY writable memory path.

Clean-slate (spec §9): one memory owner. The facade exposes the full union of the
system's memory concerns behind a single class:

* **typed semantic/working/procedural memory** — backed by ``core.memory2``
  (MemoryStore / Memory2) with hybrid retrieval, decay and prompt-budget packing;
* **session history + curated memory + user model + token usage** — backed by the
  legacy v1 store (now an *internal backend*, not a competing public writer).

Every module that used to ``import memory`` from the legacy singleton now gets
this facade, so there is exactly one writable production path and no parallel
public ``Memory`` singleton.

The legacy v1 store lives in ``core.compat.legacy_memory`` and is treated as a
private backend: it is still instantiated (it owns the real SQLite schema the
session/curated/user-model features need), but it is no longer exposed as a second
public memory object.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

# Canonical memory kinds (must match memory2.KINDS order).
KINDS: tuple[str, ...] = ("working", "episodic", "semantic", "procedural", "project")


class MemoryFacade:
    """One canonical memory API covering typed memory + session/curated/ui state."""

    def __init__(self, store: Any = None, *, db_path: Optional[str] = None,
                 v1_store: Any = None):
        # --- typed memory backend (Memory2) -----------------------------------
        if store is None:
            from ..memory2 import Memory2  # type: ignore
            store = Memory2(db_path=db_path)
        self._store = store
        # --- session / curated / user-model / token backend (legacy v1) --------
        # Private backend owned by this facade; not a competing public singleton.
        if v1_store is None:
            from ..compat.legacy_memory import Memory as _V1  # type: ignore
            try:
                v1_store = _V1()
            except Exception:
                v1_store = None
        self._v1 = v1_store
        self._lock = threading.RLock()

    @property
    def store(self) -> Any:
        """The underlying typed Memory2 store (used for advanced ops)."""
        return self._store

    @property
    def v1(self) -> Any:
        """The underlying session/curated/user-model backend (private)."""
        return self._v1

    # -- typed memory (Memory2) -------------------------------------------------
    def remember(self, kind: str, content: str, **kwargs) -> dict[str, Any]:
        if kind not in KINDS:
            return {"success": False, "error": f"unknown kind '{kind}' (choose {KINDS})"}
        return self._store.remember(kind, content, **kwargs)

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

    def recall_context(self, query: str, *, budget: int = 2000, **kw) -> Any:
        """Ranked memories + eviction report from the typed backend.

        Proxies the real ``memory2.recall_context(query, limit, max_tokens, hybrid,
        per_kind_cap, project, ...)`` signature so callers get the exact dict-shaped
        result (``text``/``kept``/``evicted``); ``budget`` maps to the store's token
        budget. Never raises: on error it returns an explicit empty report rather
        than pretending recall succeeded.
        """
        kw = dict(kw)
        # `budget` is the facade's public knob; the store calls it `max_tokens`.
        if "max_tokens" not in kw:
            kw["max_tokens"] = budget
        try:
            return self._store.recall_context(query, **kw)
        except TypeError:
            # Store doesn't accept these kwargs — retry with the core ones only.
            try:
                subset = {k: kw[k] for k in ("limit", "hybrid", "per_kind_cap",
                                             "project", "max_tokens") if k in kw}
                return self._store.recall_context(query, **subset)
            except Exception:
                return {"text": "", "kept": [], "evicted": [], "tokens": 0,
                        "budget_tokens": budget, "mode": "empty", "ids": [], "index": {}}
        except Exception:
            # Fall back to a text-only block when the typed backend is unavailable.
            try:
                text = self._store.recall_prompt_block(query, **{k: kw[k] for k in
                                                                 ("limit", "project")
                                                                 if k in kw})
                return {"text": text or "", "kept": [], "evicted": [], "tokens": 0,
                        "budget_tokens": budget, "mode": "fallback", "ids": [], "index": {}}
            except Exception:
                return {"text": "", "kept": [], "evicted": [], "tokens": 0,
                        "budget_tokens": budget, "mode": "empty", "ids": [], "index": {}}

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

    def sweep(self, *, project: Optional[str] = None, dry_run: bool = True) -> dict[str, Any]:
        """Run the typed backend's decay lifecycle pass (archive/purge/consolidate)."""
        if self._store is not None and hasattr(self._store, "sweep"):
            return self._store.sweep(project=project, dry_run=bool(dry_run))
        return {"ok": True, "note": "sweep unavailable"}

    def reindex(self) -> dict[str, Any]:
        if self._store is not None and hasattr(self._store, "reindex"):
            return self._store.reindex()
        return {"ok": False, "note": "reindex unavailable"}

    def index_stats(self) -> dict[str, Any]:
        st = getattr(self._store, "store", None)
        if st is not None and hasattr(st, "index_stats"):
            return st.index_stats()
        return {}

    def access_log(self, memory_id: int, limit: int = 20) -> list[dict[str, Any]]:
        st = getattr(self._store, "store", None)
        if st is not None and hasattr(st, "access_log"):
            return st.access_log(memory_id, limit=limit)
        return []

    # -- session / curated / user-model / token (legacy v1 backend) --------------
    @property
    def db_path(self) -> Any:
        if self._v1 is not None:
            return getattr(self._v1, "db_path", None)
        return getattr(self._store, "db_path", None)

    def add_session_message(self, *args, **kw):
        return self._v1.add_session_message(*args, **kw) if self._v1 else None

    def search_sessions(self, *args, **kw):
        return self._v1.search_sessions(*args, **kw) if self._v1 else []

    def summarize_search_results(self, *args, **kw):
        return self._v1.summarize_search_results(*args, **kw) if self._v1 else ""

    def curate_memory(self, *args, **kw):
        return self._v1.curate_memory(*args, **kw) if self._v1 else None

    def get_curated_memory(self, *args, **kw):
        return self._v1.get_curated_memory(*args, **kw) if self._v1 else []

    def periodic_nudges(self, *args, **kw):
        return self._v1.periodic_nudges(*args, **kw) if self._v1 else []

    def load_user_model(self, *args, **kw):
        return self._v1.load_user_model(*args, **kw) if self._v1 else {}

    def update_user_model(self, *args, **kw):
        return self._v1.update_user_model(*args, **kw) if self._v1 else None

    def add_token_usage(self, *args, **kw):
        return self._v1.add_token_usage(*args, **kw) if self._v1 else None

    def get_token_usage(self, *args, **kw):
        return self._v1.get_token_usage(*args, **kw) if self._v1 else {}

    # -- lifecycle ---------------------------------------------------------------
    def close(self) -> None:
        for backend in (self._store, self._v1):
            try:
                if hasattr(backend, "store"):
                    backend.store.close()
                elif hasattr(backend, "close"):
                    backend.close()
            except Exception:
                pass


_facade: Optional[MemoryFacade] = None
_facade_lock = threading.Lock()


def get_memory() -> MemoryFacade:
    """Return the process-wide canonical memory facade (single writable path)."""
    global _facade
    with _facade_lock:
        if _facade is None:
            _facade = MemoryFacade()
        return _facade
