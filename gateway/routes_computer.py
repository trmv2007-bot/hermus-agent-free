"""Computer-agent dashboard API: live status, tasks/checkpoints, plan graphs,
world state, repairs, skills, recordings, episodes, benchmark, task control,
remote control approvals, resources, and the computer event WebSocket."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response

from core.config import config
from gateway.context import _token_matches

router = APIRouter()


def _permission_guard(tool: str, args: dict | None = None):
    """Route-level guard for computer actions that bypass ToolGateway wrappers.

    Dry-run / plan-only probes never execute a real action, so they are
    permitted (and still audited) without consuming an interactive approval.
    Red-zone and emergency-stop findings remain blocked even for a dry run.
    """
    try:
        from core.permissions import Decision, permission_manager

        args = args or {}
        dry_run = bool(args.get("dry_run") or args.get("plan_only") or args.get("preview"))
        check = permission_manager.check(tool, args=args)
        if dry_run and check.get("decision") != Decision.DENY.value:
            # Planning allows the user to inspect the plan before approving.
            # The real execution path still runs through the same permission
            # gate and will require an approval when dry_run is removed.
            permission_manager.audit(
                tool, "allow", None, check.get("risk"),
                extra={"safety": check.get("safety"), "dry_run": True},
            )
            return None
        if check.get("decision") == Decision.ALLOW.value:
            return None
        return JSONResponse({
            "success": False,
            "error": f"Permission {check.get('decision')} for route action '{tool}'",
            "permission": check,
        }, status_code=403)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": f"permission check failed closed: {exc}"}, status_code=403)


# Computer Agent dashboard API - autonomous desktop agent (live status,
# tasks/checkpoints, plan graphs, world state, repairs, skills, recordings)
# ===========================================================================



def _computer_task_store():
    from core.computer import TaskStore
    return TaskStore()


def _task_dir(task_id: str) -> Path:
    return _computer_task_store().directory(task_id)


def _read_task_json(task_id: str, filename: str, default):
    path = _task_dir(task_id) / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _recording_info(task_id: str) -> Optional[dict]:
    directory = _task_dir(task_id)
    candidates = sorted(directory.glob("recording.*")) if directory.exists() else []
    if not candidates:
        return None
    video = candidates[0]
    try:
        size_mb = round(video.stat().st_size / (1024 * 1024), 2)
    except OSError:
        size_mb = None
    suffix = video.suffix.lower()
    return {
        "path": str(video),
        "filename": video.name,
        "size_mb": size_mb,
        "media_type": "video/mp4" if suffix == ".mp4" else ("video/webm" if suffix == ".webm" else None),
        "url": f"/computer/recording/{task_id}/video",
    }


def _checkpoint_summary(task_id: str) -> Optional[dict]:
    """Compact live view of one persisted task checkpoint (world + counters)."""
    store = _computer_task_store()
    checkpoint = store.load(task_id)
    if checkpoint is None:
        return None
    data = checkpoint.to_dict()
    world = data.get("world_state") or {}
    completed = list(data.get("completed_states") or [])
    pending = list(data.get("pending_states") or [])
    total = max(1, len(completed) + len(pending))
    actions = _read_task_json(task_id, "actions.json", [])
    verifications = _read_task_json(task_id, "verification.json", [])
    repairs_payload = _read_task_json(task_id, "repairs.json", {})
    repairs = repairs_payload.get("repairs") if isinstance(repairs_payload, dict) else []
    retries = 0
    for action in actions if isinstance(actions, list) else []:
        if isinstance(action, dict) and int(action.get("attempt", 1) or 1) > 1 and action.get("phase") == "original_action":
            retries += 1
    timeline = _read_task_json(task_id, "timeline.json", {})
    return {
        "task_id": task_id,
        "task": data.get("task"),
        "status": data.get("status"),
        "current_state": data.get("current_state"),
        "task_state": world.get("task_state") or data.get("current_state"),
        "confidence": world.get("confidence", 0.0),
        "completed": len(completed),
        "pending": len(pending),
        "failed": len(list(data.get("failed_states") or [])),
        "progress": round(len(completed) / total * 100, 1),
        "plan_size": len(list(data.get("plan") or [])),
        "actions": len(actions) if isinstance(actions, list) else 0,
        "retries": retries,
        "repairs": len(repairs) if isinstance(repairs, list) else 0,
        "verifications": len(verifications) if isinstance(verifications, list) else 0,
        "timeline_events": len(timeline.get("events") or []) if isinstance(timeline, dict) else 0,
        "last_action": world.get("last_action"),
        "world": {
            key: world.get(key) for key in
            ("active_application", "active_window", "visible_targets", "dialogs", "task_state", "confidence")
        },
        "error": data.get("error"),
        "result": data.get("result"),
        "recordings": list(data.get("recordings") or []),
        "resume_count": data.get("resume_count", 0),
        "attempts": data.get("attempts", 0),
        "updated_at": data.get("updated_at"),
        "created_at": data.get("created_at"),
        "recording": _recording_info(task_id),
    }


def _repairs_aggregate(limit_recent: int = 12) -> dict:
    """Aggregate repair history across all persisted tasks."""
    import re

    tasks = _computer_task_store().list()
    by_kind: dict[str, dict] = {}
    recent: list = []
    total = successes = 0
    for task in tasks:
        task_id = task["task_id"]
        payload = _read_task_json(task_id, "repairs.json", {})
        if not isinstance(payload, dict):
            continue
        diagnoses_by_state: dict[str, list] = {}
        for d in payload.get("diagnoses") or []:
            if isinstance(d, dict):
                diagnoses_by_state.setdefault(str(d.get("state")), []).append(d)

        for repair in payload.get("repairs") or []:
            if not isinstance(repair, dict):
                continue
            candidates = diagnoses_by_state.get(
                str(repair.get("repair_for") or repair.get("state") or "")
            ) or []
            failure_text = repair.get("failure") or repair.get("failure_reason") or ""
            words = set(re.findall(r"[a-z0-9]+", str(failure_text).lower()))
            best, best_score = None, 0
            for candidate in candidates:
                score = len(words & set(re.findall(
                    r"[a-z0-9]+", str(candidate.get("failure_reason") or "").lower()
                )))
                if score > best_score:
                    best, best_score = candidate, score
            diagnosis = best or (candidates[-1] if candidates else None)
            kind = "unknown"
            if isinstance(diagnosis, dict) and isinstance(diagnosis.get("diagnosis"), dict):
                kind = str(diagnosis["diagnosis"].get("kind") or "unknown")
            ok = bool(repair.get("success", repair.get("outcome") == "success"))
            total += 1
            successes += 1 if ok else 0
            action = repair.get("action")
            action_name = action.get("action") if isinstance(action, dict) else None
            by_kind.setdefault(kind, {"count": 0, "successes": 0})
            by_kind[kind]["count"] += 1
            by_kind[kind]["successes"] += 1 if ok else 0
            recent.append({
                "task_id": task_id,
                "task": task.get("task"),
                "kind": kind,
                "state": repair.get("repair_for") or repair.get("state"),
                "action": action_name,
                "success": ok,
                "failure_reason": repair.get("failure") or repair.get("failure_reason"),
                "updated": task.get("updated_at"),
            })
    recent.sort(key=lambda r: str(r.get("updated") or ""), reverse=True)
    for kind in by_kind:
        by_kind[kind]["success_rate"] = round(
            by_kind[kind]["successes"] / max(1, by_kind[kind]["count"]) * 100, 1
        )
    return {
        "total": total,
        "successes": successes,
        "success_rate": round(successes / total * 100, 1) if total else None,
        "by_kind": by_kind,
        "recent": recent[:limit_recent],
    }


def _skill_stats(skills: list) -> dict:
    runs = sum(int(s.get("runs", 0) or 0) for s in skills)
    successes = sum(int(s.get("successes", 0) or 0) for s in skills)
    return {
        "count": len(skills),
        "total_runs": runs,
        "total_successes": successes,
        "avg_success_rate": round(successes / runs * 100, 1) if runs else None,
    }




@router.get("/computer/status")
async def computer_status():
    """Live dashboard status: control center, current task, world, repairs, skills."""
    from core.computer import (
        ComputerActionController,
        ComputerSkillStore,
        ControlCenter,
        emergency_stop,
    )
    from core.computer.events import computer_event_bus
    from core.integrations import _screen_recorder

    store = _computer_task_store()
    tasks = sorted(
        store.list(),
        key=lambda t: str(t.get("updated_at") or ""),
        reverse=True,
    )
    running = [t for t in tasks if t.get("status") == "running"]
    current_id = running[0]["task_id"] if running else (tasks[0]["task_id"] if tasks else None)
    current = _checkpoint_summary(current_id) if current_id else None

    skills = ComputerSkillStore().list_skills()
    try:
        recorder_status = _screen_recorder().status()
    except Exception:
        recorder_status = {"running": False}

    try:
        control = ControlCenter(ComputerActionController()).status()
    except Exception:
        control = {"active": not emergency_stop.halted, "halted": emergency_stop.halted, "backends": {}}

    stats = {"total": len(tasks), "running": 0, "interrupted": 0, "failed": 0, "success": 0}
    for task in tasks:
        status = str(task.get("status") or "created")
        stats[status] = stats.get(status, 0) + 1

    return {
        "active": not emergency_stop.halted,
        "halted": emergency_stop.halted,
        "halt_reason": emergency_stop.reason,
        "control": control,
        "recording": recorder_status,
        "current_task": current,
        "tasks": [_checkpoint_summary(t["task_id"]) for t in tasks[:20]],
        "task_stats": stats,
        "world": (current or {}).get("world"),
        "skills": {"skills": skills[:10], "stats": _skill_stats(skills)},
        "repair_stats": _repairs_aggregate(),
        "recent_events": computer_event_bus.recent(40),
        "timestamp": datetime.now().astimezone().isoformat(),
    }


@router.get("/computer/tasks")
async def computer_tasks():
    """Task history with resume/checkpoint metadata."""
    store = _computer_task_store()
    tasks = sorted(
        store.list(),
        key=lambda t: str(t.get("updated_at") or ""),
        reverse=True,
    )
    stats = {"total": len(tasks), "running": 0, "interrupted": 0, "failed": 0, "success": 0}
    for task in tasks:
        status = str(task.get("status") or "created")
        stats[status] = stats.get(status, 0) + 1
    return {
        "tasks": [_checkpoint_summary(t["task_id"]) for t in tasks],
        "stats": stats,
    }


@router.get("/computer/task/{task_id}")
async def computer_task(task_id: str):
    """Full detail for one task: plan, timeline, actions, verifications, repairs."""
    store = _computer_task_store()
    checkpoint = store.load(task_id)
    if checkpoint is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    data = checkpoint.to_dict()
    actions = _read_task_json(task_id, "actions.json", [])
    verifications = _read_task_json(task_id, "verification.json", [])
    repairs_payload = _read_task_json(task_id, "repairs.json", {})
    timeline = _read_task_json(task_id, "timeline.json", {})
    summary_path = _task_dir(task_id) / "summary.md"
    retries = 0
    for action in actions if isinstance(actions, list) else []:
        if isinstance(action, dict) and int(action.get("attempt", 1) or 1) > 1 and action.get("phase") == "original_action":
            retries += 1
    return {
        **data,
        "graph": store.load_graph(task_id),
        "timeline": timeline,
        "actions": actions,
        "verifications": verifications,
        "repairs": repairs_payload,
        "summary": summary_path.read_text(encoding="utf-8") if summary_path.exists() else "",
        "recording": _recording_info(task_id),
        "stats": {
            "actions": len(actions) if isinstance(actions, list) else 0,
            "retries": retries,
            "verifications": len(verifications) if isinstance(verifications, list) else 0,
            "repairs": len(repairs_payload.get("repairs") or []) if isinstance(repairs_payload, dict) else 0,
            "timeline_events": len(timeline.get("events") or []) if isinstance(timeline, dict) else 0,
        },
    }


@router.get("/computer/world")
async def computer_world():
    """Latest shared WorldState snapshot (application, window, dialogs, state)."""
    store = _computer_task_store()
    tasks = sorted(
        store.list(),
        key=lambda t: str(t.get("updated_at") or ""),
        reverse=True,
    )
    if not tasks:
        return {"task_id": None, "task": None, "world": None}
    task_id = tasks[0]["task_id"]
    checkpoint = store.load(task_id)
    world = (checkpoint.world_state if checkpoint else {}) or {}
    return {"task_id": task_id, "task": tasks[0].get("task"), "world": world}


@router.get("/computer/plan/{task_id}")
async def computer_plan(task_id: str):
    """Task graph for the visual plan (nodes, goals, transitions, validation)."""
    store = _computer_task_store()
    checkpoint = store.load(task_id)
    if checkpoint is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    graph = store.load_graph(task_id)
    return {
        "task_id": task_id,
        "task": checkpoint.task,
        "graph": graph,
        "current_state": checkpoint.current_state,
        "completed": list(checkpoint.completed_states),
        "failed": list(checkpoint.failed_states),
    }


@router.get("/computer/repairs/{task_id}")
async def computer_repairs(task_id: str):
    """Repair history for one task (diagnoses + repair steps)."""
    store = _computer_task_store()
    checkpoint = store.load(task_id)
    if checkpoint is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    payload = _read_task_json(task_id, "repairs.json", {})
    repairs = payload.get("repairs") if isinstance(payload, dict) else []
    diagnoses = payload.get("diagnoses") if isinstance(payload, dict) else []
    successes = sum(1 for r in repairs if isinstance(r, dict) and bool(r.get("success", r.get("outcome") == "success")))
    return {
        "task_id": task_id,
        "task": checkpoint.task,
        "diagnoses": diagnoses,
        "repairs": repairs,
        "total": len(repairs),
        "successes": successes,
        "success_rate": round(successes / max(1, len(repairs)) * 100, 1),
    }


@router.get("/computer/repairs")
async def computer_repairs_all():
    """Aggregate repair history + success rate across all tasks."""
    return _repairs_aggregate(limit_recent=30)


@router.get("/computer/recording/{task_id}")
async def computer_recording(task_id: str):
    """Recording metadata: video, timeline, detected/analyzed/verified events."""
    if _computer_task_store().load(task_id) is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    info = _recording_info(task_id)
    timeline = _read_task_json(task_id, "timeline.json", {})
    events = _read_task_json(task_id, "events.json", [])
    verifications = _read_task_json(task_id, "verification.json", [])
    actions = _read_task_json(task_id, "actions.json", [])
    return {
        "task_id": task_id,
        "recording": info,
        "timeline_events": len(timeline.get("events") or []) if isinstance(timeline, dict) else 0,
        "detected_events": len(events) if isinstance(events, list) else 0,
        "verified": len(verifications) if isinstance(verifications, list) else 0,
        "actions": len(actions) if isinstance(actions, list) else 0,
    }


@router.get("/computer/recording/{task_id}/video")
async def computer_recording_video(task_id: str):
    """Stream the recorded screen video for a task."""
    info = _recording_info(task_id)
    if info is None:
        return JSONResponse({"success": False, "error": f"no recording for task '{task_id}'"}, status_code=404)
    return FileResponse(info["path"], media_type=info["media_type"] or "video/mp4")


@router.get("/computer/skills")
async def computer_skills():
    """Learned computer skills with reliability analytics."""
    from core.computer import ComputerSkillStore

    skills = ComputerSkillStore().list_skills()
    return {"skills": skills, "stats": _skill_stats(skills)}


@router.get("/computer/skills/{skill_name}")
async def computer_skill_detail(skill_name: str):
    """Full detail for one learned skill (procedure, failures, repairs, history)."""
    from core.computer import ComputerSkillStore

    skill = ComputerSkillStore().get_skill(skill_name)
    if skill is None:
        return JSONResponse({"success": False, "error": f"skill '{skill_name}' not found"}, status_code=404)
    data = skill.to_dict()
    history = []
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    runs = evidence.get("runs")
    if isinstance(runs, list):
        history = runs[-25:]
    data["history"] = history
    return data


# ---- Episode Memory Endpoints -----------------------------------------------

@router.get("/computer/episodes")
async def computer_episodes(limit: int = 50, outcome: str = "", tag: str = ""):
    """List recorded episodes (task recordings with full action traces)."""
    from core.computer import get_episode_store
    store = get_episode_store()
    return {
        "episodes": store.list(
            limit=limit,
            outcome=outcome if outcome else None,
            tag=tag if tag else None,
        ),
        "stats": store.stats(),
    }


@router.get("/computer/episodes/search")
async def computer_episodes_search(q: str = "", limit: int = 10):
    """Search episodes by task description."""
    from core.computer import get_episode_store
    store = get_episode_store()
    return {"results": store.search(q, limit=limit)}


@router.get("/computer/episodes/stats")
async def computer_episodes_stats():
    """Aggregate statistics across all episodes."""
    from core.computer import get_episode_store
    store = get_episode_store()
    return store.stats()


@router.get("/computer/episodes/{task_id}")
async def computer_episode_detail(task_id: str):
    """Get full detail for one recorded episode."""
    from core.computer import get_episode_store
    store = get_episode_store()
    episode = store.load(task_id)
    if episode is None:
        return JSONResponse({"success": False, "error": f"episode '{task_id}' not found"}, status_code=404)
    return episode.to_dict()


@router.delete("/computer/episodes/{task_id}")
async def computer_episode_delete(task_id: str):
    """Delete a recorded episode."""
    from core.computer import get_episode_store
    store = get_episode_store()
    if store.delete(task_id):
        return {"success": True, "deleted": task_id}
    return JSONResponse({"success": False, "error": f"episode '{task_id}' not found"}, status_code=404)


@router.post("/computer/episodes/clear")
async def computer_episodes_clear():
    """Delete all episodes."""
    from core.computer import get_episode_store
    store = get_episode_store()
    count = store.clear()
    return {"success": True, "deleted_count": count}


@router.get("/computer/episodes/recall")
async def computer_episodes_recall(task: str = ""):
    """Recall the most recent successful episode for a task description."""
    from core.computer import get_episode_store
    store = get_episode_store()
    trajectory = store.recall_trajectory(task)
    if trajectory is None:
        return {"success": False, "error": "no matching episode found"}
    return {"success": True, "trajectory": trajectory}


# ---- Benchmark Endpoints ----------------------------------------------------

@router.get("/computer/benchmark/tasks")
async def computer_benchmark_tasks(category: str = "", max_difficulty: int = 3):
    """List available benchmark tasks."""
    from core.computer.benchmark import list_tasks, get_categories
    if category:
        tasks = list_tasks(category=category, max_difficulty=max_difficulty)
    else:
        tasks = list_tasks(max_difficulty=max_difficulty)
    return {
        "tasks": [t.to_dict() for t in tasks],
        "count": len(tasks),
        "categories": {cat: len(tasks) for cat, tasks in get_categories().items()},
    }


@router.post("/computer/benchmark/run")
async def computer_benchmark_run(payload: dict = None):
    """Run the benchmark and return results."""
    from core.computer.benchmark import run_benchmark
    payload = payload or {}
    dry_run = bool(payload.get("dry_run", True))
    if not dry_run:
        denied = _permission_guard("computer_task", {"task": "computer benchmark", **payload})
        if denied is not None:
            return denied
    result = run_benchmark(
        dry_run=dry_run,
        max_tasks=int(payload.get("max_tasks", 0)),
        categories=payload.get("categories"),
        max_difficulty=int(payload.get("max_difficulty", 3)),
    )
    return result.to_dict()


@router.get("/computer/benchmark/task/{task_id}")
async def computer_benchmark_task(task_id: str):
    """Get details for a specific benchmark task."""
    from core.computer.benchmark import get_task
    task = get_task(task_id)
    if task is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    return task.to_dict()


@router.post("/computer/run")
async def computer_run(payload: dict = None):
    """Start a new autonomous computer task in the background (dry-run by default
    so the dashboard never moves the mouse unless the user opts in)."""
    from core.computer.events import publish

    payload = payload or {}
    task = str(payload.get("task") or "").strip()
    if not task:
        return JSONResponse({"success": False, "error": "task is required"}, status_code=400)
    dry_run = bool(payload.get("dry_run", True))
    task_id = str(payload.get("task_id") or "").strip() or None
    denied = _permission_guard("computer_task", {"task": task, "task_id": task_id, "dry_run": dry_run})
    if denied is not None:
        return denied

    def _run() -> None:
        try:
            from core.computer import ComputerAgent

            agent = ComputerAgent()
            agent.run(task, task_id=task_id, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            publish("task_interrupted", {"task_id": task_id or task, "reason": f"run failed: {exc}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"success": True, "started": True, "task": task, "task_id": task_id,
            "dry_run": dry_run, "note": "task running in background; watch the Computer page"}


@router.post("/computer/resume/{task_id}")
async def computer_resume(task_id: str, payload: dict = None):
    """Resume a paused/interrupted task in the background."""
    from core.computer import TaskStore
    from core.computer.events import publish

    if TaskStore().load(task_id) is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    payload = payload or {}
    dry_run = bool(payload.get("dry_run", True))
    denied = _permission_guard("computer_task", {"task_id": task_id, "action": "resume", "dry_run": dry_run})
    if denied is not None:
        return denied

    def _run() -> None:
        try:
            from core.computer import ComputerAgent

            ComputerAgent().resume(task_id, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            publish("task_interrupted", {"task_id": task_id, "reason": f"resume failed: {exc}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"success": True, "started": True, "task_id": task_id, "dry_run": dry_run,
            "note": "resume running in background; watch the Computer page"}


@router.delete("/computer/task/{task_id}")
async def computer_task_delete(task_id: str):
    """Delete a persisted task and its artifacts."""
    store = _computer_task_store()
    if store.load(task_id) is None:
        return JSONResponse({"success": False, "error": f"task '{task_id}' not found"}, status_code=404)
    denied = _permission_guard("delete_file", {"path": str(store.directory(task_id)), "target": "computer task artifacts"})
    if denied is not None:
        return denied
    directory = store.directory(task_id)
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
    return {"success": True, "deleted": task_id}


@router.post("/computer/stop")
async def computer_stop(payload: dict = None):
    """Emergency stop - halt all mouse/keyboard/autonomous control."""
    from core.computer import emergency_stop
    from core.emergency_stop import get_emergency_stop

    reason = (payload or {}).get("reason") or "emergency stop from dashboard"
    get_emergency_stop().activate(reason, set_by="computer-route")
    emergency_stop.halt(reason)
    return {"success": True, "halted": True, "reason": reason,
            "note": "Computer actions are halted. Release via POST /computer/release."}


@router.post("/computer/release")
async def computer_release():
    """Release the emergency stop latch (re-enables computer control)."""
    from core.computer import emergency_stop
    from core.emergency_stop import get_emergency_stop

    get_emergency_stop().clear("computer release", set_by="computer-route")
    emergency_stop.release()
    return {"success": True, "halted": False}


# ---- Task Control Endpoints (Pause/Resume/Cancel) ---------------------------

@router.get("/computer/control/status")
async def computer_control_status():
    """Get overall task control status - all running/paused tasks and control state."""
    from core.computer.task_control import get_task_control

    control = get_task_control()
    return control.get_status()


@router.post("/computer/control/pause/{task_id}")
async def computer_control_pause(task_id: str, payload: dict = None):
    """Request pause for a task at next safe boundary.
    
    The task will pause after completing its current action.
    Use /computer/control/resume/{task_id} to continue.
    """
    from core.computer.task_control import get_task_control

    control = get_task_control()
    reason = (payload or {}).get("reason", "")
    success = control.request_pause(task_id, reason)
    
    if success:
        return {
            "success": True,
            "task_id": task_id,
            "action": "pause_requested",
            "note": "Task will pause at next safe boundary",
            "reason": reason,
        }
    else:
        ctx = control.get_task_context(task_id)
        return {
            "success": False,
            "task_id": task_id,
            "error": f"Cannot pause: task is {ctx.control_state.value if ctx else 'not found'}",
        }


@router.post("/computer/control/resume/{task_id}")
async def computer_control_resume(task_id: str):
    """Resume a paused task from its saved state."""
    from core.computer.task_control import get_task_control

    control = get_task_control()
    success = control.resume(task_id)
    
    if success:
        ctx = control.get_task_context(task_id)
        return {
            "success": True,
            "task_id": task_id,
            "action": "resumed",
            "resume_count": ctx.resume_count if ctx else 0,
            "note": "Task resumed from paused state",
        }
    else:
        ctx = control.get_task_context(task_id)
        return {
            "success": False,
            "task_id": task_id,
            "error": f"Cannot resume: task is {ctx.control_state.value if ctx else 'not found'}",
        }


@router.post("/computer/control/cancel/{task_id}")
async def computer_control_cancel(task_id: str, payload: dict = None):
    """Request cancellation of a task.
    
    The task will be marked as cancelled and terminated.
    This is different from pause - cancelled tasks cannot be resumed.
    """
    from core.computer.task_control import get_task_control

    control = get_task_control()
    reason = (payload or {}).get("reason", "")
    success = control.request_cancel(task_id, reason)
    
    if success:
        return {
            "success": True,
            "task_id": task_id,
            "action": "cancel_requested",
            "note": "Task cancellation requested",
            "reason": reason,
        }
    else:
        ctx = control.get_task_context(task_id)
        return {
            "success": False,
            "task_id": task_id,
            "error": f"Cannot cancel: task is {ctx.control_state.value if ctx else 'not found'}",
        }


@router.post("/computer/control/emergency-stop")
async def computer_emergency_stop(payload: dict = None):
    """EMERGENCY STOP - immediately block all computer actions.
    
    This is the safety override that stops ALL computer control instantly.
    Use /computer/control/emergency-release to re-enable control.
    """
    from core.computer.task_control import get_task_control

    from core.emergency_stop import get_emergency_stop

    control = get_task_control()
    reason = (payload or {}).get("reason", "")
    get_emergency_stop().activate(reason or "computer control emergency stop", set_by="computer-route")
    control.emergency_stop(reason)
    
    return {
        "success": True,
        "action": "emergency_stop_activated",
        "reason": reason,
        "note": "All computer actions blocked. Use POST /computer/control/emergency-release to restore.",
    }


@router.post("/computer/control/emergency-release")
async def computer_emergency_release():
    """Release emergency stop - re-enable computer control.
    
    After calling this, computer actions can resume normally.
    """
    from core.computer.task_control import get_task_control

    from core.emergency_stop import get_emergency_stop

    control = get_task_control()
    get_emergency_stop().clear("computer control emergency release", set_by="computer-route")
    success = control.release_emergency_stop()
    
    return {
        "success": success,
        "action": "emergency_stop_released" if success else "emergency_stop_not_active",
        "note": "Computer control restored" if success else "Emergency stop was not active",
    }


@router.get("/computer/control/{task_id}")
async def computer_control_task(task_id: str):
    """Get control context for a specific task."""
    from core.computer.task_control import get_task_control

    control = get_task_control()
    ctx = control.get_task_context(task_id)
    
    if ctx is None:
        return {"success": False, "error": f"Task '{task_id}' not found"}
    
    return {
        "success": True,
        **ctx.to_dict(),
    }


# ===========================================================================
# Phase C & D — remote control, resources, delegation, skill profiles, plugins
# ===========================================================================

# The HTML-only computer / remote UI surfaces were removed in the single
# control-room consolidation. Real capability is served by the /computer/* and
# /remote/* data APIs below and projected by /control.

@router.get("/computer/live-frame")
async def computer_live_frame():
    """Latest screen frame as JPEG (live screen for remote/dashboard panes)."""
    try:
        from core.integrations import _screen_recorder

        frame = _screen_recorder().latest()
    except Exception:  # noqa: BLE001
        frame = None
    if frame is None:
        return JSONResponse({"error": "no live frame available"}, status_code=404)
    data = frame.get("data")
    if not data:
        return JSONResponse({"error": "live frame empty"}, status_code=404)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/computer/resources")
async def computer_resources():
    """Performance / resource telemetry for the dashboard (Phase D)."""
    from core.computer import get_resource_monitor

    return get_resource_monitor().sample()


@router.get("/computer/skills/{skill_name}/profile")
async def computer_skill_profile(skill_name: str):
    """Reliability profile for one skill (Phase C skill optimization)."""
    from core.computer import ComputerSkillStore

    profile = ComputerSkillStore().profile(skill_name)
    if profile is None:
        return JSONResponse({"success": False, "error": f"skill '{skill_name}' not found"}, status_code=404)
    return profile


@router.post("/computer/delegate")
async def computer_delegate(payload: dict = None):
    """Delegate a task across persistent agents (Phase C multi-agent delegation).

    Accepts either a free-form ``task`` (decomposed heuristically) or a custom
    ``plan`` dict with ``units`` (WorkUnit records) for full control.
    """
    payload = payload or {}
    denied = _permission_guard("computer_task", {"task": payload.get("task", ""), "action": "delegate", "dry_run": payload.get("dry_run", False)})
    if denied is not None:
        return denied
    from core.computer import MultiAgentDelegator, DelegationPlan, WorkUnit

    delegator = MultiAgentDelegator()
    dry_run = bool(payload.get("dry_run", False))
    wait = not bool(payload.get("no_wait", False))
    if payload.get("plan") is not None:
        units = [
            WorkUnit(
                unit_id=str(u.get("unit_id")),
                role=str(u.get("role", "generic")),
                task=str(u.get("task", "")),
                depends_on=list(u.get("depends_on") or []),
                agent=u.get("agent"),
                payload=dict(u.get("payload") or {}),
            )
            for u in (payload["plan"].get("units") or [])
        ]
        plan = DelegationPlan(task=str(payload.get("task", "")), units=units)
    else:
        plan = delegator.plan(str(payload.get("task", "")))
    return delegator.execute(
        plan,
        wait=wait,
        timeout_per_unit=float(payload.get("timeout", 180.0)),
        dry_run=dry_run,
    )


@router.get("/computer/delegations")
async def computer_delegations():
    """Persisted delegation plans/results (Phase C)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "data" / "delegations"
    entries = []
    if root.is_dir():
        for path in sorted(root.glob("*.json"), reverse=True):
            try:
                entries.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
    return {"delegations": entries[:50], "total": len(entries)}


# -- Remote / approval control (Phase C) --------------------------------------

@router.get("/remote/status")
async def remote_status():
    """Consolidated remote view: approval gate, control state, events, emergency."""
    from core.computer import remote_control

    return remote_control.snapshot()


@router.get("/remote/approvals")
async def remote_approvals():
    """Pending + recent approval prompts."""
    from core.computer import remote_approval

    return {"status": remote_approval.status(), "history": remote_approval.history(20)}


@router.post("/remote/approval/enable")
async def remote_approval_enable(payload: dict = None):
    """Enable/disable the remote approval gate (per-action human approval)."""
    payload = payload or {}
    from core.computer import remote_approval
    from core.computer.permissions import RiskLevel

    try:
        risk = RiskLevel(str(payload.get("required_risk", "medium")).lower())
    except ValueError:
        risk = RiskLevel.MEDIUM
    return remote_approval.set_enabled(bool(payload.get("enabled", True)), required_risk=risk)


@router.post("/remote/approve")
async def remote_approve(payload: dict = None):
    """Approve a pending action prompt (by prompt_id)."""
    payload = payload or {}
    from core.computer import remote_approval

    return remote_approval.approve(str(payload.get("prompt_id", "")), by=str(payload.get("by") or "remote"))


@router.post("/remote/reject")
async def remote_reject(payload: dict = None):
    """Reject a pending action prompt."""
    payload = payload or {}
    from core.computer import remote_approval

    return remote_approval.reject(
        str(payload.get("prompt_id", "")),
        reason=str(payload.get("reason", "") or ""),
        by=str(payload.get("by") or "remote"),
    )


@router.post("/remote/control")
async def remote_control_action(payload: dict = None):
    """Remote lifecycle control: pause / resume / cancel / emergency-stop / release."""
    payload = payload or {}
    from core.computer import remote_control

    action = str(payload.get("action", "")).lower()
    task_id = str(payload.get("task_id", "") or "")
    reason = str(payload.get("reason", "")) or f"remote {action}"
    if action == "pause":
        return remote_control.pause(task_id, reason)
    if action == "resume":
        return remote_control.resume(task_id)
    if action == "cancel":
        return remote_control.cancel(task_id, reason)
    if action in ("emergency-stop", "stop"):
        from core.emergency_stop import get_emergency_stop
        get_emergency_stop().activate(reason, set_by="remote-route")
        return remote_control.emergency_stop(reason)
    if action == "release":
        from core.emergency_stop import get_emergency_stop
        get_emergency_stop().clear("remote release", set_by="remote-route")
        return remote_control.release()
    return JSONResponse({"success": False, "error": f"unknown remote action '{action}'"}, status_code=400)


# -- Dashboard live events + local Talking Mode speech -----------------------



@router.websocket("/computer/events")
async def computer_events_ws(websocket: WebSocket):
    """Live event stream: task_started, state_changed, action_*, verification,
    repair_*, task_completed, emergency_stop, world_changed, ..."""
    from core.computer.events import computer_event_bus

    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    provided = websocket.query_params.get("token") or websocket.headers.get("X-Hermus-Token")
    if expected and not _token_matches(provided, expected):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    def enqueue(event: dict) -> None:
        def put() -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        loop.call_soon_threadsafe(put)

    unsubscribe = computer_event_bus.subscribe(enqueue)

    # Snapshot first, then stream. The cursor is taken *before* the snapshot is
    # rendered so nothing written in between is skipped; ids already shown in
    # the snapshot are suppressed once so they are not replayed as live.
    cursor = computer_event_bus.journal_offset()
    snapshot = computer_event_bus.recent(100)
    seen = deque(maxlen=4096)
    seen_ids = {str(event.get("id")) for event in snapshot if event.get("id")}
    seen.extend(seen_ids)

    def remember(event_id: str) -> bool:
        """Record an id; return True if it is new (not already delivered)."""
        if not event_id:
            return True
        if event_id in seen_ids:
            return False
        if len(seen) == seen.maxlen:
            seen_ids.discard(seen[0])
        seen.append(event_id)
        seen_ids.add(event_id)
        return True

    try:
        await websocket.send_json({"kind": "snapshot", "events": snapshot})
        last_send = time.monotonic()
        while True:
            # In-process events arrive instantly via the subscriber queue.
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
                if remember(str(event.get("id") or "")):
                    await websocket.send_json(event)
                    last_send = time.monotonic()
            except asyncio.TimeoutError:
                pass

            # Cross-process events (e.g. `hermus computer run` in a terminal)
            # arrive through the journal. Tail only the bytes appended since
            # the last cursor, so cost tracks new activity - not journal size -
            # and old rows are never replayed as live activity.
            events, cursor = computer_event_bus.tail(cursor)
            for event in events:
                if remember(str(event.get("id") or "")):
                    await websocket.send_json(event)
                    last_send = time.monotonic()

            # Keepalive: idle connections send nothing, and reverse proxies
            # drop silent WebSockets (commonly after 30-60s). A periodic ping
            # keeps the live stream alive through them.
            now = time.monotonic()
            if now - last_send >= 20:
                await websocket.send_json({"kind": "ping", "ts": datetime.now().astimezone().isoformat()})
                last_send = now
    except Exception:
        pass
    finally:
        unsubscribe()
        try:
            await websocket.close()
        except Exception:
            pass


