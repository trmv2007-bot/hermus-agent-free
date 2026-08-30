"""Canonical Memory subsystem (Rebuild spec §12, clean-slate §9).

The **only** writable memory path is :class:`MemoryFacade`
(:func:`get_memory()`), which owns the union of:

* typed semantics/working/procedural memory (backed by ``core.memory2``), and
* session history / curated memory / user model / token usage (backed by the
  legacy v1 store, now a *private backend* of the facade, not a competing writer).

``from core.memory import memory`` resolves to the canonical facade singleton, so
every module that used the legacy singleton now shares one writable path. The
legacy ``Memory`` class is still exported for tests that exercise the session/
curated backend directly and is the implementation the facade owns.
"""

from .store import MemoryFacade, get_memory
from .migration import (MigrationReader, migrate_legacy, detect_legacy,
                        verify_migration)

# The public singleton is the canonical facade (single writable path).
memory = get_memory()

# The legacy session/curated/user-model/token backend class. It is the
# implementation the facade owns; the raw process-level singleton is no longer a
# public writer (use ``get_memory()`` / ``memory``).
from ..compat.legacy_memory import Memory  # noqa: F401  (backend class)

__all__ = [
    "MemoryFacade",
    "get_memory",
    "MigrationReader",
    "migrate_legacy",
    "detect_legacy",
    "verify_migration",
    "Memory",   # legacy session/curated backend class (owned by the facade)
    "memory",   # canonical facade singleton
]
