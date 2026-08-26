"""Inter-agent message bus — DM, broadcast, named channels.

Inspired by jcode swarm messaging. Persistence is a single JSON file so
sessions on different processes can talk without a socket server.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import config

_LOCK = threading.RLock()


def _path() -> Path:
    p = config.resolve_path("data/harness/bus.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> Dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"messages": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"messages": []}


def _save(data: Dict[str, Any]) -> None:
    _path().write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def send(
    body: str,
    sender: str,
    *,
    to: Optional[str] = None,
    channel: Optional[str] = None,
    kind: str = "dm",
) -> Dict[str, Any]:
    """kind: dm | broadcast | channel."""
    msg = {
        "id": uuid.uuid4().hex[:10],
        "ts": datetime.now().astimezone().isoformat(),
        "sender": sender,
        "to": to,
        "channel": channel,
        "kind": kind if not channel else "channel",
        "body": body,
        "read_by": [],
    }
    if kind == "broadcast":
        msg["to"] = "*"
    with _LOCK:
        data = _load()
        data.setdefault("messages", []).append(msg)
        data["messages"] = data["messages"][-500:]
        _save(data)
    return msg


def inbox(session_id: str, channel: Optional[str] = None, unread_only: bool = True) -> List[Dict[str, Any]]:
    with _LOCK:
        data = _load()
        out = []
        for msg in data.get("messages", []):
            if unread_only and session_id in (msg.get("read_by") or []):
                continue
            kind = msg.get("kind")
            if kind == "broadcast":
                if msg.get("sender") != session_id:
                    out.append(msg)
            elif kind == "channel":
                if channel and msg.get("channel") == channel:
                    out.append(msg)
                elif not channel:
                    out.append(msg)
            elif msg.get("to") == session_id:
                out.append(msg)
        return out


def mark_read(session_id: str, message_ids: Optional[List[str]] = None) -> int:
    with _LOCK:
        data = _load()
        n = 0
        for msg in data.get("messages", []):
            if message_ids and msg.get("id") not in message_ids:
                continue
            readers = msg.setdefault("read_by", [])
            if session_id not in readers:
                readers.append(session_id)
                n += 1
        _save(data)
        return n
