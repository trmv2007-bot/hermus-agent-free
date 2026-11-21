"""Inter-agent message bus — DM, broadcast, named channels.

Inspired by jcode swarm messaging. Persistence is a single JSON file so
sessions on different processes can talk without a socket server; writes go
through :mod:`core.harness._store` (atomic replace + lock), so a concurrent
writer can no longer truncate the inbox.

Retention never silently drops unread mail: a DM is retained until *its* recipient
has read it, and unread messages are kept up to a hard cap. Broadcast and channel
traffic is best-effort chatter — it may be trimmed once anyone has read it.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..config import config
from ._store import json_store, read_doc, utc_now

#: Normal retention target for fully-read traffic.
MAX_MESSAGES = 500
#: Hard cap on retained unread traffic (a reader that never reads must not grow the file forever).
MAX_UNREAD = 2000


def _path() -> Path:
    p = config.resolve_path("data/harness/bus.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _blank() -> dict[str, Any]:
    return {"messages": [], "channels": {}}


def _pending(msg: dict[str, Any]) -> bool:
    """True while the mail still has a reader it owes.

    A DM is owed to its recipient specifically — ``read_by`` entries from other
    sessions must not make it look consumed, or a blanket read by one agent
    would let retention delete another agent's unread mail.
    """
    readers = msg.get("read_by") or []
    if not readers:
        return True
    to = msg.get("to")
    return bool(to) and to != "*" and to not in readers


def _retain(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim read history first; keep unread mail up to the hard cap."""
    if len(messages) <= MAX_MESSAGES:
        return messages
    unread = [m for m in messages if _pending(m)][-MAX_UNREAD:]
    read = [m for m in messages if not _pending(m)]
    keep_ids = {id(m) for m in unread}
    room = max(0, MAX_MESSAGES - len(unread))
    for msg in read[-room:] if room else []:
        keep_ids.add(id(msg))
    kept = [m for m in messages if id(m) in keep_ids]
    return kept[-max(MAX_MESSAGES, len(unread)) :]


def send(
    body: str,
    sender: str,
    *,
    to: str | None = None,
    channel: str | None = None,
    kind: str = "dm",
) -> dict[str, Any]:
    """Publish one message. ``kind``: dm | broadcast | channel."""
    text = str(body or "")[:4000]
    msg = {
        "id": uuid.uuid4().hex[:10],
        "ts": utc_now(),
        "sender": str(sender or "unknown")[:120],
        "to": to,
        "channel": channel,
        "kind": "channel" if channel else kind,
        "body": text,
        "read_by": [],
    }
    if kind == "broadcast":
        msg["to"] = "*"
    with json_store(_path(), default_factory=_blank) as data:
        data.setdefault("channels", {})
        data.setdefault("messages", []).append(msg)
        data["messages"] = _retain(data["messages"])
        # Sending on a channel subscribes the sender, so replies reach them.
        if channel:
            subs = data["channels"].setdefault(msg["sender"], [])
            if channel not in subs:
                subs.append(channel)
        return dict(msg)


def subscribe(session_id: str, channel: str) -> dict[str, Any]:
    """Join a channel so its traffic reaches this session's inbox."""
    name = str(channel or "").strip()
    if not name:
        return {"ok": False, "error": "channel name required"}
    with json_store(_path(), default_factory=_blank) as data:
        subs = data.setdefault("channels", {}).setdefault(str(session_id), [])
        if name not in subs:
            subs.append(name)
        return {"ok": True, "session": session_id, "channels": list(subs)}


def unsubscribe(session_id: str, channel: str) -> dict[str, Any]:
    with json_store(_path(), default_factory=_blank) as data:
        subs = data.setdefault("channels", {}).get(str(session_id), [])
        if channel in subs:
            subs.remove(channel)
        return {"ok": True, "session": session_id, "channels": list(subs)}


def _visible(
    msg: dict[str, Any],
    session_id: str,
    subscribed: set[str],
    channel: str | None = None,
) -> bool:
    """Whether ``msg`` belongs in ``session_id``'s inbox.

    Channel traffic is delivered only to subscribed sessions, or when a caller
    explicitly asks for ``channel``. The previous version delivered every
    channel message to every session, which injected unrelated swarm chatter
    into every agent's prompt.
    """
    kind = msg.get("kind")
    if kind == "broadcast":
        return msg.get("sender") != session_id
    if kind == "channel":
        name = msg.get("channel")
        if channel is not None:
            return name == channel
        return name in subscribed
    return msg.get("to") == session_id


def inbox(session_id: str, channel: str | None = None, unread_only: bool = True) -> list[dict[str, Any]]:
    """Messages addressed to ``session_id``."""
    data = read_doc(_path(), default_factory=_blank)
    subscribed = set((data.get("channels") or {}).get(str(session_id), []))
    out: list[dict[str, Any]] = []
    for msg in data.get("messages", []):
        if unread_only and session_id in (msg.get("read_by") or []):
            continue
        if _visible(msg, str(session_id), subscribed, channel):
            out.append(msg)
    return out


def mark_read(session_id: str, message_ids: list[str] | None = None) -> int:
    """Acknowledge this session's own mail.

    Only messages the session could actually see are marked. A blanket read used
    to stamp every DM in the bus, which made another agent's unread mail look
    consumed and let retention trim it away.
    """
    sid = str(session_id)
    with json_store(_path(), default_factory=_blank) as data:
        n = 0
        wanted = set(message_ids or [])
        subscribed = set((data.get("channels") or {}).get(sid, []))
        for msg in data.get("messages", []):
            if wanted and msg.get("id") not in wanted:
                continue
            if not _visible(msg, sid, subscribed):
                continue
            readers = msg.setdefault("read_by", [])
            if sid not in readers:
                readers.append(sid)
                n += 1
        return n


def recent(limit: int = 50, *, session_id: str | None = None) -> list[dict[str, Any]]:
    """Newest messages first, for the read-only API and the Control Room."""
    data = read_doc(_path(), default_factory=_blank)
    rows = data.get("messages", [])
    if session_id:
        rows = [m for m in rows if m.get("to") in (session_id, "*") or m.get("sender") == session_id]
    return list(reversed(rows[-max(1, int(limit)) :]))


def stats() -> dict[str, Any]:
    data = read_doc(_path(), default_factory=_blank)
    rows = data.get("messages", [])
    per_channel: dict[str, int] = {}
    for names in (data.get("channels") or {}).values():
        for name in names or []:
            per_channel[str(name)] = per_channel.get(str(name), 0) + 1
    return {
        "messages": len(rows),
        "unread": sum(1 for m in rows if _pending(m)),
        "channels": per_channel,
        "last_ts": rows[-1]["ts"] if rows else None,
    }


__all__ = ["send", "subscribe", "unsubscribe", "inbox", "mark_read", "recent", "stats", "MAX_MESSAGES"]
