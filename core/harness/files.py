"""File-shift awareness — notify a session when a file it read was edited.

jcode fires `file_changed_under_you` so parallel agents do not silently
overwrite each other. We track last-known mtime per (session, path).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import config

_LOCK = threading.RLock()


def _path() -> Path:
    p = config.resolve_path("data/harness/filewatch.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"reads": {}, "events": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"reads": {}, "events": []}


def _save(data: dict[str, Any]) -> None:
    _path().write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def note_read(session_id: str, file_path: str) -> dict[str, Any]:
    resolved = str(Path(file_path).expanduser().resolve())
    try:
        mtime = os.path.getmtime(resolved)
    except OSError:
        mtime = 0.0
    key = f"{session_id}::{resolved}"
    with _LOCK:
        data = _load()
        data.setdefault("reads", {})[key] = {
            "session_id": session_id,
            "path": resolved,
            "mtime": mtime,
            "ts": datetime.now().astimezone().isoformat(),
        }
        _save(data)
    return {"ok": True, "path": resolved, "mtime": mtime}


def note_write(file_path: str, writer: str = "") -> list[dict[str, Any]]:
    """Record a write and emit events for every session that had read the file."""
    resolved = str(Path(file_path).expanduser().resolve())
    try:
        mtime = os.path.getmtime(resolved)
    except OSError:
        mtime = 0.0
    events: list[dict[str, Any]] = []
    with _LOCK:
        data = _load()
        for _key, rec in list(data.get("reads", {}).items()):
            if rec.get("path") != resolved:
                continue
            if rec.get("session_id") == writer:
                rec["mtime"] = mtime
                continue
            ev = {
                "type": "file_changed_under_you",
                "session_id": rec["session_id"],
                "path": resolved,
                "writer": writer,
                "old_mtime": rec.get("mtime"),
                "new_mtime": mtime,
                "ts": datetime.now().astimezone().isoformat(),
                "ack": False,
            }
            data.setdefault("events", []).append(ev)
            rec["mtime"] = mtime
            events.append(ev)
        data["events"] = data.get("events", [])[-400:]
        _save(data)
    return events


def pending(session_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        data = _load()
        return [
            e for e in data.get("events", [])
            if e.get("session_id") == session_id and not e.get("ack")
        ]


def ack(session_id: str) -> int:
    with _LOCK:
        data = _load()
        n = 0
        for e in data.get("events", []):
            if e.get("session_id") == session_id and not e.get("ack"):
                e["ack"] = True
                n += 1
        _save(data)
        return n
