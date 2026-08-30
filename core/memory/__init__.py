"""Canonical Memory subsystem (Rebuild spec §12).

The canonical memory implementation lives in :mod:`core.memory2` (typed stores,
hybrid retrieval, access tracking, decay, prompt-budget packing). This package is
the single public facade. The legacy v1 store (``core.memory``) has been moved to
:mod:`core.compat.legacy_memory` and is re-exported here **only** for backward
compatibility during the migration window; it is never a writable production path.

``get_memory()`` returns one canonical facade. ``migrate_legacy()`` copies v1 data
into the canonical schema once and marks the migration complete.
"""

from .store import MemoryFacade, get_memory
from .migration import (MigrationReader, migrate_legacy, detect_legacy,
                        verify_migration)

# Backward-compatible re-exports of the legacy v1 module. These keep
# ``from core.memory import Memory`` and ``from core.memory import memory``
# working until their consumers are migrated. Marked as legacy.
from ..compat import legacy_memory  # noqa: F401  (module alias)
from ..compat.legacy_memory import Memory  # noqa: F401  (legacy class)
try:
    from ..compat.legacy_memory import memory  # noqa: F401  (legacy singleton)
except Exception:  # pragma: no cover
    memory = None  # type: ignore

__all__ = [
    "MemoryFacade",
    "get_memory",
    "MigrationReader",
    "migrate_legacy",
    "detect_legacy",
    "verify_migration",
    "Memory",       # legacy v1, read-only during migration
    "memory",       # legacy v1 singleton, read-only during migration
]
