"""Same-repo swarm coordinator.

One session can spawn workers. The parent becomes coordinator; workers
register on the session store and talk over the bus.

Execution rule: workers run through the **canonical JobQueue** via
``subagents.subagent.delegate``. The previous implementation built the agent
class directly and called ``chat`` itself, which was a second execution path
with no queue lifecycle, no retry/timeout/cancel, no persistence and no
correlation ids — exactly the split-brain the architecture forbids.

``spawn`` remains the *intent* record (session + mail). ``run_workers`` is the
execution wrapper and keeps its historical return shape so the
``harness_swarm_spawn`` tool keeps working.
"""

from __future__ import annotations

from typing import Any

from . import bus, sessions


def spawn(task: str, parent: str, *, model: str = "", count: int = 1) -> dict[str, Any]:
    count = max(1, min(int(count or 1), 8))
    sessions.touch(parent, role="coordinator", status="coordinating", task=task)
    workers = []
    for i in range(count):
        rec = sessions.create(task=task, role="worker", parent=parent, model=model, project="")
        sessions.touch(rec["id"], status="queued")
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


def run_workers(task: str, parent: str, *, model: str = "", count: int = 1) -> dict[str, Any]:
    """Spawn workers and execute them as one canonical ``subagent.delegate`` job."""
    spec = spawn(task, parent, model=model, count=count)
    workers = spec["workers"]
    prompts = [
        f"{task}\n\nYou are worker {i + 1}/{len(workers)} of a same-repo swarm. "
        "Do your slice of the work, verify it, and report concrete evidence."
        for i in range(len(workers))
    ]

    for worker in workers:
        sessions.touch(worker["id"], status="running")

    try:
        from subagents.subagent import delegate

        out = delegate(task, tasks=prompts, model=model, max_children=len(workers), aggregate="synthesize")
    except Exception as exc:  # the queue itself refused: report honestly
        out = {"ok": False, "error": str(exc), "nodes": []}

    nodes = out.get("nodes") or []
    results: list[dict[str, Any]] = []
    for index, worker in enumerate(workers):
        node = nodes[index] if index < len(nodes) else {}
        answer = str(node.get("answer") or node.get("response") or "")
        error = node.get("error") or (out.get("error") if not out.get("ok") else "")
        success = bool(answer) and not error and bool(node.get("success", True))
        if success:
            sessions.touch(worker["id"], status="done", last_error=None)
            bus.send(answer[:500], sender=worker["id"], to=parent, kind="dm")
            results.append({"id": worker["id"], "success": True, "response": answer[:2000]})
        else:
            message = str(error or "worker produced no answer")
            sessions.touch(worker["id"], status="failed", last_error=message)
            results.append({"id": worker["id"], "success": False, "error": message})

    return {
        "ok": bool(out.get("ok", True)) and any(r["success"] for r in results),
        "parent": parent,
        "results": results,
        "workers": workers,
        "answer": out.get("answer") or out.get("summary") or "",
        "error": out.get("error") or "",
    }


def status(parent: str | None = None) -> dict[str, Any]:
    all_s = sessions.list_sessions()
    if parent:
        kids = [s for s in all_s if s.get("parent") == parent or s.get("id") == parent]
        return {"sessions": kids, "count": len(kids)}
    return {"sessions": all_s, "count": len(all_s)}


__all__ = ["spawn", "run_workers", "status"]
