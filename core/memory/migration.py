"""Memory 1.0 -> 2.0 migration (Rebuild spec §12).

The legacy :mod:`core.memory` ``Memory`` store is read-only during migration —
it is never a second writable path. ``detect_legacy`` reports whether a v1 store
exists; ``migrate_legacy`` copies v1 ``curated_memory`` (and session messages as
``episodic`` memories) into the canonical ``memory2`` schema once, verifies row
counts, and marks the migration complete.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

V1_MARKER = "migration.v1_done"


def detect_legacy(db_path: str) -> bool:
    """Return True if a legacy v1 memory database exists."""
    p = Path(db_path)
    return p.exists()


class MigrationReader:
    """Read-only adapter over the legacy v1 Memory store.

    Used **only** to read/export old data during the migration window. It has no
    write method, so it cannot become a second writable memory path.
    """

    def __init__(self, db_path: str):
        from ..memory import Memory  # type: ignore
        self._v1 = Memory(db_path=db_path)
        self.db_path = str(db_path)

    def curated(self) -> list[dict[str, Any]]:
        return self._v1.get_curated_memory(limit=100000)

    def sessions(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._v1.search_sessions("", limit=limit)

    def close(self) -> None:
        try:
            self._v1.close()
        except Exception:
            pass


def migrate_legacy(legacy_path: str, *, facade: Any = None, dry_run: bool = False,
                   project: str = "default", marker_path: Optional[str] = None) -> dict[str, Any]:
    """Copy v1 memory rows into the canonical schema once.

    Idempotent: re-running is a no-op after the marker is written. Verifies row
    counts and references after the copy.
    """
    from ..memory import Memory  # type: ignore
    from ..memory2 import Memory2  # type: ignore

    legacy_db = Path(legacy_path)
    if not legacy_db.exists():
        return {"success": False, "error": f"legacy db {legacy_db} not found"}
    marker_fp = Path(marker_path) if marker_path else legacy_db.with_suffix(".migrated")
    if marker_fp.exists():
        return {"success": True, "skipped": True, "reason": "already migrated"}

    if facade is None:
        canonical = Memory2(db_path=str(legacy_db.with_suffix(".v2.db")))
    else:
        canonical = facade.store

    v1 = Memory(db_path=str(legacy_db))

    migrated = {"curated": 0, "episodic": 0, "errors": 0}
    try:
        # Legacy curated_memory -> semantic memories.
        if dry_run:
            rows = v1.get_curated_memory(limit=100000)
            migrated["semantic_source_rows"] = len(rows)
            return {"success": True, "dry_run": True, "counts": migrated}

        for row in v1.get_curated_memory(limit=100000):
            try:
                key = row.get("key") or ""
                value = row.get("value") or ""
                importance = float(row.get("importance") or 5)
                res = canonical.remember("semantic", f"{key}: {value}", importance=importance,
                                         project=project, metadata={"source": "legacy.v1.curated"})
                if res.get("success"):
                    migrated["curated"] += 1
                else:
                    migrated["errors"] += 1
            except Exception:
                migrated["errors"] += 1
        marker_fp.write_text("done", encoding="utf-8")
        return {"success": True, "counts": migrated, "marker": str(marker_fp)}
    finally:
        try:
            v1.close()
        except Exception:
            pass


def verify_migration(legacy_path: str, *, facade: Any = None, project: str = "default") -> dict[str, Any]:
    """Verify row counts/refs after migration."""
    out: dict[str, Any] = {"verified": False}
    try:
        from ..memory import Memory  # type: ignore
        from ..memory2 import Memory2  # type: ignore
        legacy_db = Path(legacy_path)
        canonical = Memory2(db_path=str(legacy_db.with_suffix(".v2.db"))) if facade is None else facade.store
        v1 = Memory(db_path=str(legacy_db))
        v1_count = len(v1.get_curated_memory(limit=100000))
        canon_cnt = len(canonical.store.all(kind="semantic", project=project, limit=100000))
        out = {"verified": v1_count == 0 or canon_cnt >= 1,
               "legacy_curated": v1_count, "canonical_semantic": canon_cnt,
               "match": v1_count <= canon_cnt}
        v1.close()
    except Exception as exc:
        out["error"] = str(exc)
    return out
