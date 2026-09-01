"""Subagents — spawn isolated sub-agents for parallel workstreams, free.

Historically this module used ``multiprocessing`` + a blocking ``join(timeout)``
with no way to observe progress and no cancellation. It is now a thin, API-stable
facade over :mod:`core.delegation`, which runs each sub-agent as a separate process
speaking newline-delimited **JSON-RPC 2.0** over stdio (structured progress
notifications, cooperative cancel, depth budget, structured result contract).

Kept for backward compatibility:
    ``spawn_subagent(task) -> {subagent_id, task, result, success}``
    ``spawn_parallel_subagents([tasks]) -> [ {...}, ... ]``

New: ``delegate(goal, tasks=…)`` for plan→fan-out→aggregate, plus
``subagent_status()`` / ``cancel_subagent()``.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional
from collections.abc import Callable

# Canonical job kinds for delegation on the JobQueue.
DELEGATE_JOB = "subagent.delegate"


def _engine():
    from core.delegation import delegation

    return delegation


def register_delegate_handler(queue) -> None:
    """Register the canonical ``subagent.delegate`` Job handler on the queue.

    Idempotent — the gateway registers it at startup; a standalone agent that
    delegates must register it here before submitting. Lazy imports keep the
    agent path from pulling the whole gateway in unless delegation is used.
    """
    from gateway.handlers import make_delegate_handler

    queue.register(DELEGATE_JOB, make_delegate_handler(), overwrite=True)


# Backwards-compatible private alias.
_register_delegate_handler = register_delegate_handler


async def _submit_job(q, kind, payload, session_key, run_id, job_id):
    return q.submit(kind, payload, session_key=session_key, run_id=run_id, job_id=job_id)


async def _job_status(q, job_id):
    return q.status(job_id)


def submit_and_wait(
    kind: str,
    payload: dict[str, Any],
    *,
    session_key: str = "",
    timeout: float = 300.0,
    run_id: str = "",
    job_id: str = "",
    register: Optional[Callable[[Any], None]] = None,
) -> dict[str, Any]:
    """Submit a canonical Job and block until it is terminal (or times out).

    Safe to call from *any* thread — an agent tool loop, the CLI, or a test:
    * if the canonical ``job_queue`` already owns a running loop (the gateway),
      submission + status reads are marshalled onto that loop with
      ``run_coroutine_threadsafe`` (so ``loop.create_task`` is never called from a
      worker thread — the classic way to break a nested queue submit);
    * otherwise a dedicated event loop drives ``start → submit → poll → stop``.

    This is the ONE path by which an agent delegates work to a sub-agent: the
    execution lifecycle (queued→running→done/failed/cancelled, retry, timeout,
    cancellation, persistence, restart recovery) is owned by the canonical queue,
    not by the delegation module.
    """
    from gateway.queue import job_queue, STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED

    q = job_queue
    if register is not None:
        register(q)
    elif kind == DELEGATE_JOB:
        # Standalone agents that delegate must ensure the handler is registered.
        register_delegate_handler(q)
    # Correlation: if this job is spawned from inside another queued handler (the
    # common case — an agent delegating within a queued turn), inherit the parent
    # run/mission/task ids unless the caller supplied explicit ones.
    if kind == DELEGATE_JOB:
        try:
            from gateway.queue import current_job_context

            cur = current_job_context()
            payload = dict(payload)
            payload.setdefault("mission_id", cur.get("mission_id") or "")
            payload.setdefault("run_id", cur.get("run_id") or "")
            payload.setdefault("parent_task_id", cur.get("task_id") or "")
        except Exception:
            pass
    if getattr(q, "_started", False) and getattr(q, "_loop", None) is not None and not q._loop.is_closed():
        loop = q._loop
        job = asyncio.run_coroutine_threadsafe(
            _submit_job(q, kind, payload, session_key, run_id, job_id), loop
        ).result(timeout=max(10.0, float(timeout)))
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() <= deadline:
            st = asyncio.run_coroutine_threadsafe(_job_status(q, job.id), loop).result()
            if st.get("status") in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED):
                return st
            time.sleep(0.05)
        return {**q.status(job.id), "found": True, "status": "timeout",
                "error": f"job did not finish within {timeout:g}s"}

    async def _drive() -> dict[str, Any]:
        await q.start()
        job = q.submit(kind, payload, session_key=session_key, run_id=run_id, job_id=job_id)
        st: dict[str, Any] = {}
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() <= deadline:
            st = q.status(job.id)
            if st.get("status") in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED):
                break
            await asyncio.sleep(0.05)
        await q.stop()
        if st.get("status") not in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED):
            st = {**st, "found": True, "status": "timeout",
                  "error": f"job did not finish within {timeout:g}s"}
        return st

    return asyncio.run(_drive())


def _delegate_result_to_task_result(node: dict[str, Any], *, subagent_id: str,
                                    task: str, started: float) -> dict[str, Any]:
    """Adapt a delegation node into the legacy ``spawn_subagent`` return shape.

    ``out["nodes"]`` carries the node's ``to_dict()`` projection (answer/evidence/
    tool_calls/confidence), not the full ``result`` dict — so reconstruct the
    result from those fields.
    """
    result = node.get("result") or {
        "answer": node.get("answer", ""),
        "confidence": node.get("confidence"),
        "tool_calls": node.get("tool_calls", []),
        "evidence": node.get("evidence", []),
        "status": node.get("status"),
    }
    success = node.get("status") in ("done", "partial")
    return {
        "subagent_id": subagent_id,
        "task": task,
        "result": result,
        "response": result.get("answer") or node.get("answer") or "",
        "success": bool(success),
        "error": node.get("error") or ("" if success else "subagent produced no result"),
        "backend": node.get("backend") or "queue",
        "pid": node.get("pid") or 0,
        "duration_ms": int((time.time() - started) * 1000),
        "tree": {"status": node.get("status"), "children": 1,
                 "succeeded": 1 if success else 0, "failed": 0 if success else 1,
                 "duration_ms": int((time.time() - started) * 1000)},
    }


def spawn_subagent(
    task: str,
    *,
    model: str = "",
    max_steps: int = 4,
    timeout: Optional[float] = None,
    on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Spawn one isolated sub-agent through the canonical JobQueue.

    The single sub-agent runs as a canonical ``subagent.delegate`` job, so its
    execution lifecycle (queued→running→done/failed, retry, timeout, cancel,
    persistence) is owned by the JobQueue; the delegation module is only the
    worker implementation invoked by that job.
    """
    subagent_id = f"sub_{uuid.uuid4().hex[:6]}"
    if not str(task or "").strip():
        return {"subagent_id": subagent_id, "task": task,
                "error": "task required", "success": False}
    started = time.time()
    try:
        st = submit_and_wait(
            DELEGATE_JOB,
            {"tasks": [str(task)], "goal": str(task)[:120], "depth": 1,
             "max_children": 1, "aggregate": "concat", "model": model,
             "max_steps": int(max_steps)},
            session_key=f"delegate:{subagent_id}",
            timeout=float(timeout) if timeout else 300.0,
            register=_register_delegate_handler,
        )
        if st.get("status") == "failed":
            return {"subagent_id": subagent_id, "task": task,
                    "error": st.get("error") or "subagent job failed", "success": False}
        result = st.get("result") or {}
        node = (result.get("nodes") or [{}])[0]
        return _delegate_result_to_task_result(node, subagent_id=subagent_id,
                                               task=str(task), started=started)
    except Exception as e:
        return {"subagent_id": subagent_id, "task": task, "error": str(e), "success": False}


def spawn_parallel_subagents(
    tasks: list[str],
    *,
    model: str = "",
    max_steps: int = 4,
    timeout: Optional[float] = None,
    aggregate: str = "concat",
    on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> list[dict[str, Any]]:
    """Spawn many sub-agents in parallel — one canonical delegate job each step."""
    tasks = [str(t) for t in (tasks or []) if str(t).strip()]
    if not tasks:
        return []
    try:
        st = submit_and_wait(
            DELEGATE_JOB,
            {"tasks": tasks, "goal": "", "depth": 1, "max_children": len(tasks),
             "aggregate": aggregate, "model": model, "max_steps": int(max_steps)},
            session_key=f"delegate:{uuid.uuid4().hex[:6]}",
            timeout=float(timeout) if timeout else 300.0,
            register=_register_delegate_handler,
        )
        if st.get("status") == "failed":
            return [{"subagent_id": f"sub_{i}", "task": t,
                     "error": st.get("error") or "subagent job failed", "success": False}
                    for i, t in enumerate(tasks)]
        results: list[dict[str, Any]] = []
        for node in (st.get("result") or {}).get("nodes") or []:
            results.append({
                "subagent_id": node.get("id"),
                "task": node.get("task"),
                "result": node.get("result") or {},
                "response": (node.get("result") or {}).get("answer", ""),
                "success": node.get("status") == "done",
                "error": node.get("error") or "",
                "backend": node.get("backend"),
                "pid": node.get("pid"),
            })
        return results
    except Exception as e:
        return [{"subagent_id": f"sub_{i}", "task": t, "error": str(e), "success": False}
                for i, t in enumerate(tasks)]


def delegate(
    goal: str,
    tasks: Optional[list[str]] = None,
    *,
    model: str = "",
    max_children: int = 4,
    aggregate: str = "synthesize",
    on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Plan the work (unless ``tasks`` given), run it in parallel, return one answer.

    The plan→fan-out→aggregate runs as a single canonical ``subagent.delegate`` job
    so the whole delegation inherits the JobQueue lifecycle (retry/timeout/cancel/
    persistence), and is traceable by the parent mission/run via correlation IDs.
    """
    payload: dict[str, Any] = {"goal": str(goal), "max_children": int(max_children),
                               "aggregate": aggregate, "model": model}
    if tasks:
        payload["tasks"] = [str(t) for t in tasks]
    try:
        st = submit_and_wait(DELEGATE_JOB, payload,
                             session_key=f"delegate:{uuid.uuid4().hex[:6]}",
                             timeout=300.0, register=_register_delegate_handler)
        if st.get("status") == "failed":
            return {"ok": False, "error": st.get("error") or "delegate job failed",
                    "status": "failed", "nodes": []}
        return st.get("result") or {"ok": False, "error": "no result", "nodes": []}
    except Exception as e:
        return {"ok": False, "error": str(e), "status": "failed", "nodes": []}


def subagent_status() -> dict[str, Any]:
    return _engine().status()


def cancel_subagents(tree_id: str) -> dict[str, Any]:
    return _engine().cancel_tree(tree_id)


def write_python_tool_via_rpc(tool_name: str, steps: list[str]) -> dict:
    """Collapse a multi-step pipeline into one script that calls tools via RPC.

    Same idea as before (zero-context-cost turns), but the generated file now goes
    through the skill forge so it is validated + indexed instead of dropped in.
    """
    from pathlib import Path

    from core.skill_forge import SkillForge, DistilledStep

    code_steps = [
        DistilledStep(index=i + 1, tool=str(s).split("(")[0].strip() or "shell_execute",
                      args={"step": i + 1}, intent=str(s))
        for i, s in enumerate(steps or [])
    ]
    cand = None
    try:
        forge = SkillForge()
        from core.skill_forge import SkillCandidate

        cand = SkillCandidate(
            name=str(tool_name), title=str(tool_name).replace("_", " ").capitalize(),
            description=f"RPC-collapsed pipeline: {', '.join(str(s) for s in steps)[:200]}",
            goal=str(tool_name), steps=code_steps,
            when_to_use="Use when the same multi-step tool pipeline is needed again.",
            inputs=["task", "query"], tags=["rpc", "auto"],
            verification="All steps report success and the final step returns a non-empty result.",
            provenance={"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "generator": "write_python_tool_via_rpc",
                        "hash": uuid.uuid4().hex[:12], "evaluation": {}},
        )
        res = forge.install(cand)
        if res.get("installed"):
            return {**res, "tool_name": cand.name,
                    "path": str(Path(forge.skills_dir) / cand.name),
                    "code": f"# {len(code_steps)} steps collapsed into one RPC-driven skill"}
        return {"success": False, "error": res.get("report", {}).get("error") or "validation failed",
                "detail": res}
    except Exception as e:
        return {"success": False, "error": str(e)}
