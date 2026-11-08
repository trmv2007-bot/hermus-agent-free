"""Read-only harness projections for the gateway API and the console panel.

One owner for "what is the harness doing right now". Both
``GET /api/v1/harness/*`` and the Systems panel (``core/console.py::_p_harness``)
call these functions, so the Control Room cannot grow a second, divergent
answer to the same question.

Nothing here invents data: counts come from the session store, the bus file and
the file-shift log; the "last turn" block is the newest real
``harness.turn_prepared`` event on the dashboard bus.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..config import config
from . import bus, files, sessions


def _last_turn() -> dict[str, Any] | None:
    try:
        from ..dashboard_events import dashboard_event_bus

        for event in dashboard_event_bus.recent(300):
            if event.get("type") == "harness.turn_prepared":
                data = dict(event.get("data") or {})
                data["ts"] = event.get("ts")
                return data
    except Exception:
        pass
    return None


def overview() -> dict[str, Any]:
    """Aggregate counts, pending work and the newest observed turn.

    ``active`` means a live status whose heartbeat is fresh (the same window
    pruning uses). A session left behind by a crashed process is not "active"
    just because its file still says running.
    """
    rows = sessions.list_sessions()
    by_status = Counter(str(r.get("status") or "unknown") for r in rows)
    active = [
        r for r in sessions.active_since(sessions.ACTIVE_WINDOW_HOURS) if str(r.get("status") or "") in sessions.LIVE_STATUSES
    ]
    return {
        "enabled": bool(getattr(config, "harness_enabled", True)),
        "sessions": {
            "total": len(rows),
            "active": len(active),
            "by_status": dict(by_status),
        },
        "bus": bus.stats(),
        "files": files.stats(),
        "last_turn": _last_turn(),
        "budget": {
            "tokens": int(getattr(config, "harness_budget_tokens", 6000)),
            "keep_recent": int(getattr(config, "harness_keep_recent", 6)),
            "tool_payload_chars": int(getattr(config, "harness_tool_payload_chars", 1200)),
            "recall_limit": int(getattr(config, "harness_recall_limit", 4)),
            "recall_timeout_ms": int(getattr(config, "harness_recall_timeout_ms", 800)),
        },
    }


def _row(record: dict[str, Any]) -> dict[str, Any]:
    """A session as the UI needs it: no unbounded task text, no secrets."""
    return {
        "id": record.get("id"),
        "task": str(record.get("task") or "")[:160],
        "role": record.get("role"),
        "parent": record.get("parent"),
        "status": record.get("status"),
        "model": record.get("model"),
        "project": record.get("project"),
        "turns": int(record.get("turns") or 0),
        "attachments": int(record.get("attachments") or 0),
        "created": record.get("created"),
        "updated": record.get("updated"),
        "last_error": str(record.get("last_error") or "")[:200] or None,
        "stats": record.get("stats") or {},
    }


def sessions_view(limit: int = 50) -> dict[str, Any]:
    rows = [_row(r) for r in sessions.list_sessions()]
    rows.sort(key=lambda r: str(r.get("updated") or ""), reverse=True)
    limit = max(1, min(int(limit or 50), 200))
    return {"sessions": rows[:limit], "count": len(rows), "limit": limit}


def session_view(session_id: str) -> dict[str, Any] | None:
    record = sessions.get(session_id)
    if not record:
        return None
    return {
        "session": _row(record),
        "record": record,
        "pending_notices": len(files.pending(session_id)),
        "unread_mail": len(bus.inbox(session_id, unread_only=True)),
    }


def events_view(limit: int = 50) -> dict[str, Any]:
    """File-shift notices and swarm mail, newest first, bodies truncated."""
    limit = max(1, min(int(limit or 50), 200))
    rows: list[dict[str, Any]] = []
    for event in files.recent(limit):
        rows.append(
            {
                "kind": "file_shift",
                "type": event.get("type"),
                "ts": event.get("ts"),
                "session_id": event.get("session_id"),
                "path": event.get("path"),
                "writer": event.get("writer"),
                "ack": event.get("ack"),
            }
        )
    for msg in bus.recent(limit):
        rows.append(
            {
                "kind": "mail",
                "type": msg.get("kind"),
                "ts": msg.get("ts"),
                "session_id": msg.get("to"),
                "sender": msg.get("sender"),
                "channel": msg.get("channel"),
                "preview": str(msg.get("body") or "")[:160],
            }
        )
    rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return {"events": rows[:limit], "count": len(rows), "limit": limit}


__all__ = ["overview", "sessions_view", "session_view", "events_view"]
