"""Server-owned sessions — runtime lives independently of any TUI surface.

A session is not a window. Clients attach/detach; the session record
(status, last prompt, swarm role, turn/token stats) stays on disk.

Records are written through :mod:`core.harness._store`, so two swarm workers
touching the same session cannot truncate each other's file. Nothing here
invents progress: ``status`` only changes when a caller reports it, and the
``stats`` block is accumulated from real turn observations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import config
from ._store import json_store, read_doc, utc_now

#: Statuses that are live while their heartbeat is fresh.
LIVE_STATUSES = ("running", "coordinating", "attached", "queued")
#: A live status older than this is treated as abandoned (crashed mid-turn).
#: A turn refreshes the heartbeat, so 30 minutes without one means the process
#: that owned the session is gone; waiting two hours just hid dead sessions.
STALE_LIVE_HOURS = 0.5
#: Display window for "active". Much shorter than the prune window: a session
#: that stopped heartbeating five minutes ago is not doing work right now,
#: even though its record is kept for longer (pruning is destructive).
ACTIVE_WINDOW_HOURS = 5.0 / 60.0


def _dir() -> Path:
    p = config.resolve_path("data/harness/sessions")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
    return _dir() / f"{safe}.json"


def _new_record(
    sid: str,
    *,
    task: str = "",
    role: str = "worker",
    parent: str | None = None,
    model: str = "",
    project: str = "",
) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": sid,
        "task": task,
        "role": role,
        "parent": parent,
        "model": model,
        "project": project,
        "status": "idle",
        "created": now,
        "updated": now,
        "heartbeat": now,
        "turns": 0,
        "last_error": None,
        "attachments": 0,
        "stats": {
            "compactions": 0,
            "tokens_in": 0,
            "elapsed_ms": 0,
            "notices": 0,
            "recall_hits": 0,
        },
    }


def create(
    session_id: str | None = None,
    *,
    task: str = "",
    role: str = "worker",
    parent: str | None = None,
    model: str = "",
    project: str = "",
) -> dict[str, Any]:
    sid = session_id or f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    record = _new_record(sid, task=task, role=role, parent=parent, model=model, project=project)
    with json_store(_file(sid)) as data:
        data.update(record)
        return dict(data)


def get(session_id: str) -> dict[str, Any] | None:
    rec = read_doc(_file(session_id), default_factory=dict)
    return rec or None


def touch(session_id: str, **fields: Any) -> dict[str, Any]:
    """Merge ``fields`` into a session record, creating it when missing.

    ``turns_inc`` increments the turn counter; ``stats`` merges one level deep
    so callers can report a single observation without clobbering the rest.
    """
    inc = int(fields.pop("turns_inc", 0) or 0)
    stats = fields.pop("stats", None)
    with json_store(_file(session_id)) as rec:
        if not rec:
            rec.update(_new_record(session_id))
        rec.update({k: v for k, v in fields.items() if v is not None})
        if inc:
            rec["turns"] = int(rec.get("turns") or 0) + inc
        if isinstance(stats, dict) and stats:
            merged = dict(rec.get("stats") or {})
            for key, value in stats.items():
                if isinstance(value, (int, float)) and key in ("compactions", "tokens_in", "elapsed_ms", "notices", "recall_hits"):
                    merged[key] = merged.get(key, 0) + value
                else:
                    merged[key] = value
            rec["stats"] = merged
        rec["updated"] = utc_now()
        rec["heartbeat"] = rec["updated"]
        return dict(rec)


def begin(session_id: str, *, task: str = "", model: str = "", project: str = "", role: str | None = None) -> dict[str, Any]:
    """Mark the start of a turn: running, heartbeat now, turns +1."""
    fields: dict[str, Any] = {"status": "running", "turns_inc": 1}
    if task:
        fields["task"] = task[:200]
    if model:
        fields["model"] = model
    if project:
        fields["project"] = project
    if role:
        fields["role"] = role
    return touch(session_id, **fields)


def heartbeat(session_id: str, **fields: Any) -> dict[str, Any]:
    """Refresh liveness without changing status."""
    return touch(session_id, **fields)


def record(session_id: str, **stats: Any) -> dict[str, Any]:
    """Accumulate turn observations without touching status."""
    return touch(session_id, stats=stats) if stats else touch(session_id)


def finish(session_id: str, *, status: str = "idle", error: str | None = None, **stats: Any) -> dict[str, Any]:
    """Mark the end of a turn with its observed outcome."""
    fields: dict[str, Any] = {"status": status, "last_error": error}
    if stats:
        fields["stats"] = stats
    return touch(session_id, **fields)


def attach(session_id: str) -> dict[str, Any]:
    rec = get(session_id) or create(session_id)
    return touch(session_id, attachments=int(rec.get("attachments") or 0) + 1, status="attached")


def detach(session_id: str) -> dict[str, Any]:
    rec = get(session_id) or create(session_id)
    n = max(0, int(rec.get("attachments") or 1) - 1)
    return touch(session_id, attachments=n, status="idle" if n == 0 else "attached")


def list_sessions() -> list[dict[str, Any]]:
    out = []
    for path in sorted(_dir().glob("*.json")):
        rec = read_doc(path, default_factory=dict)
        if rec:
            out.append(rec)
    return out


def _age_hours(record: dict[str, Any]) -> float:
    stamp = record.get("updated") or record.get("created") or ""
    try:
        seen = datetime.fromisoformat(str(stamp))
    except Exception:
        return 0.0
    return max(0.0, (datetime.now().astimezone() - seen).total_seconds() / 3600.0)


def prune(*, max_age_hours: float = 168.0, max_sessions: int = 500) -> dict[str, Any]:
    """Delete stale, non-live sessions. Returns what was removed and kept.

    A session that is running, coordinating, attached or queued is never
    removed while it is fresh: deleting a live worker's record would silently
    lose the only trace of that work. A ``running`` record whose heartbeat is
    older than ``STALE_LIVE_HOURS`` is considered abandoned (the process died
    mid-turn) and becomes prunable, so a crash cannot pin a session forever.
    """
    records = list_sessions()

    def _is_live(rec: dict[str, Any]) -> bool:
        if str(rec.get("status") or "") not in LIVE_STATUSES:
            return False
        limit = 24.0 if str(rec.get("status")) == "attached" else STALE_LIVE_HOURS
        return _age_hours(rec) <= limit

    live = [r for r in records if _is_live(r)]
    candidates = [r for r in records if not _is_live(r)]
    candidates.sort(key=lambda r: str(r.get("updated") or r.get("created") or ""))
    removed = 0
    keep_ids = {str(r.get("id")) for r in live}
    allowed = max(0, int(max_sessions) - len(live))

    for rec in candidates:
        sid = str(rec.get("id") or "")
        if not sid:
            continue
        expired = _age_hours(rec) > float(max_age_hours)
        over_cap = allowed <= 0
        if not expired and not over_cap:
            allowed -= 1
            keep_ids.add(sid)
            continue
        try:
            _file(sid).unlink(missing_ok=True)
            removed += 1
        except OSError:
            keep_ids.add(sid)
    return {"removed": removed, "kept": len(keep_ids), "live": len(live)}


def gc_once_per_hour() -> dict[str, Any] | None:
    """Run :func:`prune` at most once an hour (marker file, no scheduler needed)."""
    marker = _dir().parent / ".gc"
    try:
        if marker.exists() and (datetime.now() - datetime.fromtimestamp(marker.stat().st_mtime)).total_seconds() < 3600:
            return None
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(utc_now(), encoding="utf-8")
    except OSError:
        return None
    return prune(
        max_age_hours=float(getattr(config, "harness_session_ttl_hours", 168.0)),
        max_sessions=int(getattr(config, "harness_max_sessions", 500)),
    )


def active_since(hours: float = 24.0) -> list[dict[str, Any]]:
    cutoff = datetime.now().astimezone() - timedelta(hours=hours)
    out = []
    for rec in list_sessions():
        stamp = rec.get("updated") or rec.get("created") or ""
        try:
            if datetime.fromisoformat(str(stamp)) >= cutoff:
                out.append(rec)
        except Exception:
            continue
    return out


__all__ = [
    "LIVE_STATUSES",
    "STALE_LIVE_HOURS",
    "ACTIVE_WINDOW_HOURS",
    "create",
    "get",
    "touch",
    "begin",
    "heartbeat",
    "finish",
    "attach",
    "detach",
    "list_sessions",
    "prune",
    "gc_once_per_hour",
    "active_since",
]
