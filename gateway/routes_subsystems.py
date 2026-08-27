"""Architecture-upgrade endpoints: background agents, workspaces, memory2,
permissions, research, model router, screen recording/watching, watchdog,
and profiles."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


# ---- Architecture-upgrade endpoints ------------------------------------------

@router.get("/agents")
async def background_agents_list():
    from core.agent_manager import agent_manager

    return {"agents": agent_manager.list()}


@router.post("/agents/start")
async def background_agents_start(payload: dict):
    from core.agent_manager import agent_manager

    name = payload.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    return agent_manager.start(name)


@router.post("/agents/stop")
async def background_agents_stop(payload: dict):
    from core.agent_manager import agent_manager

    name = payload.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    return agent_manager.stop(name)


@router.post("/agents/create")
async def background_agents_create(payload: dict):
    from core.agent_manager import agent_manager

    name = payload.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    return agent_manager.create(name, role=payload.get("role", "generic"),
                                model=payload.get("model"), persona=payload.get("persona"))


@router.get("/workspace")
async def workspace_info():
    from core.workspace import workspace as ws

    return {"base_dir": str(ws.base_dir), "projects": ws.list_projects(),
            "current": ws.current_project(), "dirs": {k: str(v) for k, v in ws.dirs.items()}}


@router.post("/workspace/create")
async def workspace_create(payload: dict):
    from core.workspace import workspace as ws

    return ws.create_project(payload.get("name", ""), description=payload.get("description", ""))


@router.post("/workspace/use")
async def workspace_use(payload: dict):
    from core.workspace import workspace as ws

    return ws.set_current_project(payload.get("name", ""))


@router.post("/memory2/remember")
async def memory2_remember(payload: dict):
    from core.memory2 import memory2

    return memory2.remember(payload.get("kind", "semantic"), payload.get("content", ""),
                            importance=payload.get("importance", 5.0),
                            success=payload.get("success"), project=payload.get("project"))


@router.post("/memory2/recall")
async def memory2_recall(payload: dict):
    from core.memory2 import memory2

    return {"results": memory2.recall(payload.get("query", ""), limit=int(payload.get("limit", 10)),
                                      kinds=payload.get("kinds"), project=payload.get("project"))}


@router.get("/permissions/log")
async def permissions_log(limit: int = 20):
    from core.permissions import permission_manager

    return {"log": permission_manager.recent(limit=limit)}


@router.post("/permissions/check")
async def permissions_check(payload: dict):
    from core.permissions import permission_manager

    return permission_manager.check(payload.get("tool", ""), agent=payload.get("agent"),
                                    args=payload.get("args"))


@router.post("/permissions/set")
async def permissions_set(payload: dict):
    from core.permissions import permission_manager

    return permission_manager.set_policy(payload.get("tool", ""), payload.get("decision", "ask"),
                                         agent=payload.get("agent"))


@router.post("/research")
async def research_run(payload: dict):
    from core.research import research_pipeline

    return research_pipeline.run(payload.get("query", ""), limit=int(payload.get("limit", 10)))


@router.post("/router/select")
async def router_select(payload: dict):
    from core.router2 import router2

    return router2.select(payload.get("text", ""))


@router.get("/screen/status")
async def screen_status():
    from core.integrations import _screen_recorder

    return _screen_recorder().status()


@router.post("/screen/start")
async def screen_start(payload: dict = None):
    from core.computer import recording_policy
    from core.integrations import _screen_recorder

    payload = payload or {}
    fps = float(payload.get("fps", 10.0))
    max_seconds = float(payload.get("max_seconds", 30.0))
    valid = recording_policy.validate_settings(fps, max_seconds)
    if not valid.get("ok"):
        return {"success": False, "error": valid["error"]}
    output = payload.get("output_path") or None
    if output:
        try:
            output = str(recording_policy.output_path(output))
        except (ValueError, PermissionError) as exc:
            return {"success": False, "error": str(exc)}
    return _screen_recorder().start(max_seconds=max_seconds, fps=fps, output_path=output)


@router.post("/screen/stop")
async def screen_stop():
    from core.integrations import _screen_recorder

    return _screen_recorder().stop()


@router.post("/screen/save")
async def screen_save(payload: dict):
    from core.computer import recording_policy
    from core.integrations import _screen_recorder

    try:
        path = recording_policy.output_path(payload.get("path", "recording.mp4"))
    except (ValueError, PermissionError) as exc:
        return {"success": False, "error": str(exc)}
    seconds = float(payload.get("seconds", 0.0))
    return _screen_recorder().save(str(path), seconds=seconds if seconds > 0 else None)


@router.post("/screen/analyze")
async def screen_analyze(payload: dict):
    from core.computer import VideoAnalyzer
    from core.integrations import _screen_recorder

    frames = _screen_recorder().recent(float(payload.get("seconds", 10.0)))
    analyzer = (VideoAnalyzer.with_ollama(payload.get("model", "llava:7b"))
                if payload.get("use_vision", True) else VideoAnalyzer())
    return await asyncio.to_thread(
        analyzer.analyze,
        frames,
        payload.get("task", ""),
        int(payload.get("max_events", 12)),
    )


@router.post("/screen/watch")
async def screen_watch(payload: dict):
    from core.computer import ScreenWatcher, VideoAnalyzer
    from core.integrations import _screen_recorder

    analyzer = VideoAnalyzer.with_ollama(payload.get("model", "llava:7b"))
    watcher = ScreenWatcher(_screen_recorder(), analyzer=analyzer)
    return await asyncio.to_thread(
        watcher.watch,
        payload.get("condition", ""),
        float(payload.get("timeout", 60.0)),
        0.25,
        int(payload.get("stable_matches", 1)),
        False,
    )


@router.post("/screen/action/before")
async def screen_action_before(payload: dict):
    from core.integrations import _screen_action_manager

    return _screen_action_manager().before(
        payload.get("action", ""), payload.get("expected_state", "")
    )


@router.post("/screen/action/after")
async def screen_action_after(payload: dict):
    from core.computer import ScreenVerifier, VideoAnalyzer
    from core.integrations import _screen_action_manager

    analyzer = (VideoAnalyzer.with_ollama(payload.get("model", "llava:7b"))
                if payload.get("use_vision", False) else None)
    verifier = ScreenVerifier(
        vision_model=analyzer.evaluate_condition if analyzer else None,
        transition_model=analyzer.evaluate_transition if analyzer else None,
    )
    return await asyncio.to_thread(
        _screen_action_manager().after,
        payload.get("action_id", ""),
        verifier,
    )


@router.post("/watchdog/handle")
async def watchdog_handle(payload: dict):
    from core.watchdog import watchdog

    return watchdog.handle(payload.get("error", ""), context=payload.get("context", ""))


@router.get("/profiles")
async def profiles_list():
    from core.profiles import profile_manager

    return {"profiles": profile_manager.list()}


@router.post("/profiles/create")
async def profiles_create(payload: dict):
    from core.profiles import profile_manager

    return profile_manager.create(payload.get("name", ""), persona=payload.get("persona"),
                                  model=payload.get("model"))


# ===========================================================================
