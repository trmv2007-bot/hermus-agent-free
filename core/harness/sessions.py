"""Server-owned sessions — runtime lives independently of any TUI surface.

A session is not a window. Clients attach/detach; the session record
(status, last prompt, trajectory pointer, swarm role) stays on disk.
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


def _dir() -> Path:
    p = config.resolve_path("data/harness/sessions")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
    return _dir() / f"{safe}.json"


def create(
    session_id: Optional[str] = None,
    *,
    task: str = "",
    role: str = "worker",
    parent: Optional[str] = None,
    model: str = "",
) -> Dict[str, Any]:
    sid = session_id or f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    rec = {
        "id": sid,
        "task": task,
        "role": role,
        "parent": parent,
        "model": model,
        "status": "idle",
        "created": datetime.now().astimezone().isoformat(),
        "updated": datetime.now().astimezone().isoformat(),
        "turns": 0,
        "last_error": None,
        "attachments": 0,
    }
    with _LOCK:
        _file(sid).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def get(session_id: str) -> Optional[Dict[str, Any]]:
    path = _file(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def touch(session_id: str, **fields: Any) -> Dict[str, Any]:
    with _LOCK:
        rec = get(session_id) or create(session_id)
        rec.update({k: v for k, v in fields.items() if v is not None})
        rec["updated"] = datetime.now().astimezone().isoformat()
        if "turns_inc" in fields:
            rec["turns"] = int(rec.get("turns") or 0) + 1
            rec.pop("turns_inc", None)
        _file(session_id).write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
        return rec


def attach(session_id: str) -> Dict[str, Any]:
    rec = get(session_id) or create(session_id)
    return touch(session_id, attachments=int(rec.get("attachments") or 0) + 1, status="attached")


def detach(session_id: str) -> Dict[str, Any]:
    rec = get(session_id) or create(session_id)
    n = max(0, int(rec.get("attachments") or 1) - 1)
    return touch(session_id, attachments=n, status="idle" if n == 0 else "attached")


def list_sessions() -> List[Dict[str, Any]]:
    out = []
    for path in sorted(_dir().glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out
