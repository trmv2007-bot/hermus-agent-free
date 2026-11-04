"""Tools for the jcode-inspired harness: sessions, swarm, bus, file-shift.

Every function validates its inputs before touching shared state. A tool is
reachable by a model, so ``sender``/``to`` are treated as untrusted: a sender
that does not exist is registered as a real session (no phantom identities),
a DM to an unknown recipient is refused instead of accumulating dead mail, and
payload sizes are capped.
"""

from __future__ import annotations

import re
from typing import Any

_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]")
MAX_BODY_CHARS = 2000


def _session_id(value: str, fallback: str) -> str:
    cleaned = _ID_RE.sub("", str(value or "").strip())[:120]
    return cleaned or fallback


def harness_sessions() -> dict[str, Any]:
    from core.harness import stats

    view = stats.sessions_view(50)
    return {"sessions": view["sessions"], "count": view["count"]}


def harness_message(body: str, sender: str, to: str = "", channel: str = "", kind: str = "dm") -> dict[str, Any]:
    from core.harness import bus, sessions

    text = str(body or "").strip()
    if not text:
        return {"ok": False, "error": "body is required"}
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + f"\n...(truncated at {MAX_BODY_CHARS} chars)"

    who = _session_id(sender, "")
    if not who:
        return {"ok": False, "error": "sender is required"}
    # A sender must be a real session; registering it keeps identities honest
    # so a later reply has somewhere to land.
    if sessions.get(who) is None:
        sessions.create(who, task="swarm participant", role="worker")

    recipient = _session_id(to, "") if to else ""
    if recipient and recipient != "*" and sessions.get(recipient) is None:
        return {"ok": False, "error": f"unknown recipient session: {recipient}"}

    mode = kind if kind in ("dm", "broadcast", "channel") else "dm"
    mode = "channel" if channel else mode
    if not recipient and mode == "dm":
        mode = "broadcast"
    return bus.send(text, who, to=recipient or None, channel=_session_id(channel, "") or None, kind=mode)


def harness_inbox(session_id: str, unread_only: bool = True, channel: str = "") -> dict[str, Any]:
    from core.harness import bus

    sid = _session_id(session_id, "")
    if not sid:
        return {"messages": [], "count": 0, "error": "session_id is required"}
    items = bus.inbox(sid, channel=_session_id(channel, "") or None, unread_only=bool(unread_only))
    return {"messages": items, "count": len(items)}


def harness_subscribe(session_id: str, channel: str, unsubscribe: bool = False) -> dict[str, Any]:
    """Join or leave a named swarm channel.

    Without this, channel traffic only reached agents that had already posted on
    the channel (``send`` auto-subscribes the sender), so a pure listener saw
    nothing.
    """
    from core.harness import bus

    sid = _session_id(session_id, "")
    name = str(channel or "").strip()
    if not sid or not name:
        return {"ok": False, "error": "session_id and channel are required"}
    if unsubscribe:
        return bus.unsubscribe(sid, name)
    return bus.subscribe(sid, name)


def harness_swarm_spawn(task: str, parent: str, count: int = 1, model: str = "") -> dict[str, Any]:
    from core.harness import sessions
    from core.harness.swarm import spawn

    goal = str(task or "").strip()
    if not goal:
        return {"ok": False, "error": "task is required"}
    who = _session_id(parent, "")
    if not who:
        return {"ok": False, "error": "parent session is required"}
    if sessions.get(who) is None:
        sessions.create(who, task=goal[:200], role="coordinator")
    return spawn(goal, who, model=str(model or ""), count=max(1, min(int(count or 1), 8)))


def harness_file_note_read(session_id: str, path: str) -> dict[str, Any]:
    from core.harness import files

    if not str(path or "").strip():
        return {"ok": False, "error": "path is required"}
    return files.note_read(_session_id(session_id, "tool"), str(path))


def harness_cascade_recall(query: str, limit: int = 5, project: str = "") -> dict[str, Any]:
    from core.harness.memory_graph import cascade_recall

    if not str(query or "").strip():
        return {"hits": [], "count": 0, "summary": "", "mode": "empty", "error": "query is required"}
    return cascade_recall(query, limit=max(1, min(int(limit or 5), 10)), project=str(project or ""))


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "harness_sessions",
            "description": "List server-owned harness sessions (not UI windows)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "harness_message",
            "description": "Send a swarm DM, broadcast, or channel message",
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {"type": "string"},
                    "sender": {"type": "string"},
                    "to": {"type": "string"},
                    "channel": {"type": "string"},
                    "kind": {"type": "string", "enum": ["dm", "broadcast", "channel"]},
                },
                "required": ["body", "sender"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "harness_inbox",
            "description": "Read swarm messages for a session",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "unread_only": {"type": "boolean", "default": True},
                    "channel": {"type": "string", "description": "Optional: read one channel explicitly"},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "harness_subscribe",
            "description": "Join (or leave) a named swarm channel so its traffic reaches this session",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "channel": {"type": "string"},
                    "unsubscribe": {"type": "boolean", "default": False},
                },
                "required": ["session_id", "channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "harness_swarm_spawn",
            "description": "Spawn same-repo swarm workers for a task",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "parent": {"type": "string"},
                    "count": {"type": "integer", "default": 1},
                    "model": {"type": "string"},
                },
                "required": ["task", "parent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "harness_file_note_read",
            "description": "Mark a file as read so later writes notify this session",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["session_id", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "harness_cascade_recall",
            "description": "Cascade memory recall (FTS then embeddings then memory2)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5, "maximum": 10},
                    "project": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_MAP = {
    "harness_sessions": harness_sessions,
    "harness_message": harness_message,
    "harness_inbox": harness_inbox,
    "harness_subscribe": harness_subscribe,
    "harness_swarm_spawn": harness_swarm_spawn,
    "harness_file_note_read": harness_file_note_read,
    "harness_cascade_recall": harness_cascade_recall,
}
