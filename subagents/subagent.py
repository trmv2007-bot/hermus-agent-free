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

import time
import uuid
from typing import Any, Optional
from collections.abc import Callable



def _engine():
    from core.delegation import delegation

    return delegation


def spawn_subagent(
    task: str,
    *,
    model: str = "",
    max_steps: int = 4,
    timeout: Optional[float] = None,
    on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Spawn one isolated sub-agent (separate process, JSON-RPC worker)."""
    subagent_id = f"sub_{uuid.uuid4().hex[:6]}"
    engine = _engine()
    started = time.time()
    try:
        out = engine.fanout(
            [task], goal=task[:120], model=model, max_steps=max_steps,
            timeout=timeout, aggregate="concat", on_event=on_event,
            tree_id=subagent_id, max_children=1,
        )
        node = (out.get("nodes") or [{}])[0]
        result = node.get("result") or {
            "response": node.get("answer", ""),
            "tool_calls": node.get("tool_calls", []),
        }
        success = node.get("status") in ("done", "partial")
        return {
            "subagent_id": subagent_id,
            "task": task,
            "result": result,
            "response": result.get("answer") or result.get("response") or "",
            "success": bool(success),
            "error": node.get("error") or ("" if success else "subagent produced no result"),
            "backend": node.get("backend"),
            "pid": node.get("pid"),
            "duration_ms": int((time.time() - started) * 1000),
            "tree": {k: out.get(k) for k in
                     ("status", "children", "succeeded", "failed", "duration_ms")},
        }
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
    """Spawn many sub-agents in parallel — independent workstreams, one process each."""
    if not tasks:
        return []
    engine = _engine()
    try:
        out = engine.fanout(
            tasks, goal="", model=model, max_steps=max_steps, timeout=timeout,
            aggregate=aggregate, on_event=on_event,
        )
        results: list[dict[str, Any]] = []
        for node in out.get("nodes") or []:
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
    """Plan the work (unless ``tasks`` given), run it in parallel, return one answer."""
    engine = _engine()
    if tasks:
        return engine.fanout(tasks, goal=goal, model=model, aggregate=aggregate, on_event=on_event)
    return engine.decompose_and_run(goal, model=model, max_children=max_children,
                                    aggregate=aggregate, on_event=on_event)


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
