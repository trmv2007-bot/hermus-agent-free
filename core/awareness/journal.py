"""A 24-hour rolling summary of desktop activity the person can delete.

The journal exists so HERMUS can answer "how has my day gone" and so a
long-sitting or low-battery suggestion is based on evidence rather than a guess.
It keeps three things and nothing else: which application had focus, how long the
person was active, and which suggestions were offered or refused. Titles, window
text, clipboard values and file paths are never written here.

Anything older than 24 hours is pruned on the next write, so the file cannot grow
into a behavioural archive. ``clear()`` deletes it outright.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

RETENTION_HOURS = 24
MAX_RECORDS = 5000
SAMPLE_INTERVAL_SECONDS = 30.0
IDLE_LIMIT_SECONDS = 300.0

DEFAULT_PATH = "data/awareness/journal.jsonl"


class AwarenessJournal:
    """Append-only, self-pruning, deletable activity summary."""

    def __init__(self, path: str | Path | None = None, *, clock=time.time, retention_hours: float = RETENTION_HOURS):
        configured = path or os.getenv("HERMUS_AWARENESS_JOURNAL") or DEFAULT_PATH
        self.path = Path(configured)
        self.clock = clock
        self.retention_seconds = float(retention_hours) * 3600.0
        self._lock = threading.RLock()
        self._last_sample = 0.0

    # -- writing ------------------------------------------------------------

    def record(self, kind: str, data: dict[str, Any] | None = None) -> None:
        """Append one small record; failures are silent by design."""
        record = {"ts": float(self.clock()), "kind": str(kind)[:40], "data": _safe(data or {})}
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                return
            self._prune_if_needed()

    def sample(self, snapshot: dict[str, Any], *, force: bool = False) -> bool:
        """Record at most one focus sample per interval. Returns whether it wrote."""
        now = float(self.clock())
        with self._lock:
            if not force and now - self._last_sample < SAMPLE_INTERVAL_SECONDS:
                return False
            self._last_sample = now
        focused = snapshot.get("focused") or {}
        process = None
        if focused.get("ok"):
            value = focused.get("value") or {}
            process = None if value.get("excluded") else value.get("process")
        idle = snapshot.get("idle") or {}
        power = snapshot.get("power") or {}
        self.record(
            "sample",
            {
                "process": (process or "")[:60] or None,
                "idle": (idle.get("value") or {}).get("seconds") if idle.get("ok") else None,
                "on_ac": (power.get("value") or {}).get("on_ac") if power.get("ok") else None,
                "percent": (power.get("value") or {}).get("percent") if power.get("ok") else None,
            },
        )
        return True

    # -- reading ------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        with self._lock:
            records = self._load()
        cutoff = float(self.clock()) - self.retention_seconds
        records = [row for row in records if float(row.get("ts") or 0) >= cutoff]
        samples = [row for row in records if row.get("kind") == "sample"]
        focus_minutes: dict[str, float] = {}
        active_seconds = 0.0
        for row in samples:
            data = row.get("data") or {}
            idle = data.get("idle")
            if isinstance(idle, (int, float)) and idle < IDLE_LIMIT_SECONDS:
                active_seconds += SAMPLE_INTERVAL_SECONDS
                process = data.get("process")
                if process:
                    focus_minutes[process] = focus_minutes.get(process, 0.0) + SAMPLE_INTERVAL_SECONDS / 60.0
        offered = [row for row in records if row.get("kind") == "suggestion_offered"]
        accepted = [row for row in records if row.get("kind") == "suggestion_accepted"]
        dismissed = [row for row in records if row.get("kind") == "suggestion_dismissed"]
        top_apps = sorted(
            ({"process": name, "minutes": round(minutes, 1)} for name, minutes in focus_minutes.items()),
            key=lambda row: row["minutes"],
            reverse=True,
        )[:5]
        return {
            "window_hours": round(self.retention_seconds / 3600.0, 1),
            "records": len(records),
            "active_minutes": round(active_seconds / 60.0, 1),
            "top_apps": top_apps,
            "suggestions": {
                "offered": len(offered),
                "accepted": len(accepted),
                "dismissed": len(dismissed),
                "by_pattern": _count_patterns(offered),
            },
            "first_record": _iso(records[0]["ts"]) if records else None,
            "last_record": _iso(records[-1]["ts"]) if records else None,
            "path": str(self.path),
        }

    def clear(self) -> dict[str, Any]:
        """Delete the rolling summary. Settings are untouched."""
        with self._lock:
            existed = self.path.exists()
            removed = 0
            if existed:
                try:
                    removed = self.path.stat().st_size
                    self.path.unlink()
                except OSError:
                    return {"cleared": False, "path": str(self.path), "reason": "the journal could not be deleted"}
            self._last_sample = 0.0
            return {"cleared": True, "existed": existed, "bytes_removed": removed, "path": str(self.path)}

    # -- internals ----------------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        rows = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _prune_if_needed(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < 512_000:
            return
        cutoff = float(self.clock()) - self.retention_seconds
        rows = [row for row in self._load() if float(row.get("ts") or 0) >= cutoff][-MAX_RECORDS:]
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            return


def _safe(data: dict[str, Any]) -> dict[str, Any]:
    """Only scalars, only short values: nothing free-form can reach the journal."""
    clean: dict[str, Any] = {}
    for key, value in list(data.items())[:12]:
        if isinstance(value, (bool, int, float)) or value is None:
            clean[str(key)[:40]] = value
        elif isinstance(value, str):
            clean[str(key)[:40]] = value[:60]
    return clean


def _count_patterns(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        pattern = str((row.get("data") or {}).get("pattern") or "")[:40]
        if pattern:
            counts[pattern] = counts.get(pattern, 0) + 1
    return counts


def _iso(ts: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")


__all__ = ["AwarenessJournal", "DEFAULT_PATH", "RETENTION_HOURS"]
