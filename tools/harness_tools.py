"""Tools for the jcode-inspired harness: sessions, swarm, bus, file-shift."""
from __future__ import annotations

from typing import Any, Dict


def harness_sessions() -> Dict[str, Any]:
    from core.harness import sessions

    items = sessions.list_sessions()
    return {"sessions": items, "count": len(items)}


def harness_message(body: str, sender: str, to: str = "", channel: str = "", kind: str = "dm") -> Dict[str, Any]:
    from core.harness import bus

    k = kind if not channel else "channel"
    if not to and k == "dm":
        k = "broadcast"
    return bus.send(body, sender, to=to or None, channel=channel or None, kind=k)


def harness_inbox(session_id: str, unread_only: bool = True) -> Dict[str, Any]:
    from core.harness import bus

    items = bus.inbox(session_id, unread_only=unread_only)
    return {"messages": items, "count": len(items)}


def harness_swarm_spawn(task: str, parent: str, count: int = 1, model: str = "") -> Dict[str, Any]:
    from core.harness.swarm import spawn

    return spawn(task, parent, model=model, count=count)


def harness_file_note_read(session_id: str, path: str) -> Dict[str, Any]:
    from core.harness import files

    return files.note_read(session_id, path)


def harness_cascade_recall(query: str, limit: int = 5) -> Dict[str, Any]:
    from core.harness.memory_graph import cascade_recall

    return cascade_recall(query, limit=limit)


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
                },
                "required": ["session_id"],
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
                    "limit": {"type": "integer", "default": 5},
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
    "harness_swarm_spawn": harness_swarm_spawn,
    "harness_file_note_read": harness_file_note_read,
    "harness_cascade_recall": harness_cascade_recall,
}
