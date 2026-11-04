"""File-shift awareness — notify a session when a file it read was edited.

jcode fires ``file_changed_under_you`` so parallel agents do not silently
overwrite each other. We track the last-known mtime per (session, path) and
emit one event per affected reader on write.

Persistence goes through :mod:`core.harness._store`, so a crash cannot leave a
half-written watch file that makes every session look like it read nothing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import config
from ._store import json_store, read_doc, utc_now

MAX_EVENTS = 400


def _path() -> Path:
    p = config.resolve_path("data/harness/filewatch.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _blank() -> dict[str, Any]:
    return {"reads": {}, "events": []}


def _resolve(file_path: str) -> tuple[str, float]:
    resolved = str(Path(file_path).expanduser().resolve())
    try:
        return resolved, os.path.getmtime(resolved)
    except OSError:
        return resolved, 0.0


def note_read(session_id: str, file_path: str) -> dict[str, Any]:
    resolved, mtime = _resolve(file_path)
    key = f"{session_id}::{resolved}"
    with json_store(_path(), default_factory=_blank) as data:
        data.setdefault("reads", {})[key] = {
            "session_id": session_id,
            "path": resolved,
            "mtime": mtime,
            "ts": utc_now(),
        }
    return {"ok": True, "path": resolved, "mtime": mtime}


def note_write(file_path: str, writer: str = "") -> list[dict[str, Any]]:
    """Record a write and emit events for every other session that read the file."""
    resolved, mtime = _resolve(file_path)
    events: list[dict[str, Any]] = []
    with json_store(_path(), default_factory=_blank) as data:
        already_open = {
            (e.get("session_id"), e.get("new_mtime"))
            for e in data.get("events", [])
            if not e.get("ack") and e.get("path") == resolved
        }
        for _key, rec in list(data.get("reads", {}).items()):
            if rec.get("path") != resolved:
                continue
            if rec.get("session_id") == writer:
                rec["mtime"] = mtime
                continue
            if (rec.get("session_id"), mtime) in already_open:
                # This reader already has an unacknowledged notice for this version.
                rec["mtime"] = mtime
                continue
            events.append(
                {
                    "type": "file_changed_under_you",
                    "session_id": rec["session_id"],
                    "path": resolved,
                    "writer": writer,
                    "old_mtime": rec.get("mtime"),
                    "new_mtime": mtime,
                    "ts": utc_now(),
                    "ack": False,
                }
            )
            rec["mtime"] = mtime
        if events:
            data.setdefault("events", []).extend(events)
            data["events"] = data["events"][-MAX_EVENTS:]
    return events


def pending(session_id: str) -> list[dict[str, Any]]:
    data = read_doc(_path(), default_factory=_blank)
    return [e for e in data.get("events", []) if e.get("session_id") == session_id and not e.get("ack")]


def ack(session_id: str) -> int:
    with json_store(_path(), default_factory=_blank) as data:
        n = 0
        for event in data.get("events", []):
            if event.get("session_id") == session_id and not event.get("ack"):
                event["ack"] = True
                n += 1
        return n


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """Newest file-shift events first (read-only projection)."""
    data = read_doc(_path(), default_factory=_blank)
    return list(reversed(data.get("events", [])[-max(1, int(limit)) :]))


def stats() -> dict[str, Any]:
    data = read_doc(_path(), default_factory=_blank)
    events = data.get("events", [])
    return {
        "tracked_reads": len(data.get("reads", {})),
        "events": len(events),
        "pending": sum(1 for e in events if not e.get("ack")),
    }


__all__ = ["note_read", "note_write", "pending", "ack", "recent", "stats"]
