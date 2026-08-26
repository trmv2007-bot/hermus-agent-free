"""Same-repo swarm coordinator.

One session can spawn workers. The parent becomes coordinator; workers
register on the session store and talk over the bus. File writes notify
siblings via file-shift events.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import bus, sessions


def spawn(task: str, parent: str, *, model: str = "", count: int = 1) -> Dict[str, Any]:
    count = max(1, min(int(count or 1), 8))
    sessions.touch(parent, role="coordinator", status="coordinating", task=task)
    workers = []
    for i in range(count):
        rec = sessions.create(task=task, role="worker", parent=parent, model=model)
        bus.send(
            f"You are worker {i + 1}/{count} for: {task}",
            sender=parent,
            to=rec["id"],
            kind="dm",
        )
        workers.append(rec)
    bus.send(
        f"Spawned {count} worker(s) for: {task}",
        sender=parent,
        kind="broadcast",
    )
    return {"ok": True, "parent": parent, "workers": workers, "count": count}


def run_workers(task: str, parent: str, *, model: str = "", count: int = 1) -> Dict[str, Any]:
    """Spawn workers and execute each via an isolated HermusAgent chat."""
    spec = spawn(task, parent, model=model, count=count)
    results: List[Dict[str, Any]] = []
    from core.agent import HermusAgent

    for worker in spec["workers"]:
        sessions.touch(worker["id"], status="running")
        try:
            agent = HermusAgent(model=model or None, session_id=worker["id"], max_steps=4)
            out = agent.chat(task)
            sessions.touch(worker["id"], status="done", last_error=None)
            results.append({
                "id": worker["id"],
                "success": True,
                "response": (out.get("response") or "")[:2000],
            })
            bus.send((out.get("response") or "")[:500], sender=worker["id"], to=parent, kind="dm")
        except Exception as e:
            sessions.touch(worker["id"], status="failed", last_error=str(e))
            results.append({"id": worker["id"], "success": False, "error": str(e)})
    return {"ok": True, "parent": parent, "results": results, "workers": spec["workers"]}


def status(parent: Optional[str] = None) -> Dict[str, Any]:
    all_s = sessions.list_sessions()
    if parent:
        kids = [s for s in all_s if s.get("parent") == parent or s.get("id") == parent]
        return {"sessions": kids, "count": len(kids)}
    return {"sessions": all_s, "count": len(all_s)}
