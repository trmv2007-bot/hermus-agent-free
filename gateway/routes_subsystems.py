"""Architecture-upgrade endpoints: background agents, workspaces, memory2,
permissions, research, model router, screen recording/watching, watchdog,
and profiles."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


def _permission_guard(tool: str, args: dict | None = None):
    """Return a 403 JSONResponse when a route-level action is not permitted."""
    try:
        from core.permissions import Decision, permission_manager

        check = permission_manager.check(tool, args=args or {})
        if check.get("decision") == Decision.ALLOW.value:
            return None
        return JSONResponse({
            "success": False,
            "error": f"Permission {check.get('decision')} for route action '{tool}'",
            "permission": check,
        }, status_code=403)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": f"permission check failed closed: {exc}"}, status_code=403)


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


@router.get("/permissions/approvals")
async def permissions_approvals(include_inactive: bool = False):
    from core.permissions import permission_manager

    return {"approvals": permission_manager.approvals_list(include_inactive=include_inactive)}


@router.post("/permissions/approve")
async def permissions_approve(payload: dict):
    from core.permissions import permission_manager

    return permission_manager.approval_grant(
        payload.get("title") or "User approval grant",
        tool=payload.get("tool", "*"),
        red_lines=[int(x) for x in payload.get("red_lines", [])],
        resources=[str(x) for x in payload.get("resources", [])],
        purpose=payload.get("purpose", ""),
        ttl_minutes=payload.get("ttl_minutes"),
        max_uses=payload.get("max_uses"),
        notes=payload.get("notes", ""),
    )


@router.post("/permissions/revoke")
async def permissions_revoke(payload: dict):
    from core.permissions import permission_manager

    return permission_manager.approval_revoke(payload.get("id", ""))


@router.get("/permissions/pending")
async def permissions_pending(include_resolved: bool = False):
    from core.permissions import permission_manager

    return {"pending": permission_manager.approval_pending(include_resolved=include_resolved)}


@router.get("/permissions/bundles")
async def permissions_bundles(include_resolved: bool = False):
    from core.permissions import permission_manager

    return {"bundles": permission_manager.approval_bundles(include_resolved=include_resolved)}


@router.post("/permissions/bundles/resolve")
async def permissions_bundles_resolve(payload: dict):
    from core.permissions import permission_manager

    result = permission_manager.approval_bundle_resolve(
        payload.get("id", ""),
        payload.get("decision", "deny"),
        ttl_minutes=payload.get("ttl_minutes"),
        max_uses=payload.get("max_uses"),
        notes=payload.get("notes", ""),
    )
    bundle = result.get("bundle") or {}
    if payload.get("resume") and result.get("success") and payload.get("decision") == "approve" and bundle.get("mission_id"):
        try:
            from core.mission import mission_engine
            report = mission_engine.get_mission(bundle["mission_id"])
            if report and str(report.domain) == "local_defense":
                from core.local_defense_workflow import run_local_scan_mission
                resumed = run_local_scan_mission(bundle["mission_id"])
            else:
                resumed = mission_engine.resume_mission(bundle["mission_id"], extra_steps=payload.get("extra_steps") or 8)
            result["resume"] = resumed.to_dict()
        except Exception as exc:  # noqa: BLE001
            result["resume"] = {"success": False, "error": str(exc), "mission_id": bundle.get("mission_id")}
    return result


@router.post("/permissions/pending/resolve")
async def permissions_pending_resolve(payload: dict):
    from core.permissions import permission_manager

    resolved = permission_manager.approval_resolve(
        payload.get("id", ""),
        payload.get("decision", "deny"),
        resources=[str(x) for x in payload.get("resources", [])] if payload.get("resources") is not None else None,
        purpose=payload.get("purpose", ""),
        ttl_minutes=payload.get("ttl_minutes"),
        max_uses=payload.get("max_uses"),
        notes=payload.get("notes", ""),
    )
    if payload.get("retry") and resolved.get("success") and payload.get("decision") == "approve":
        resolved["retry"] = permission_manager.approval_retry(payload.get("id", ""))
    return resolved


@router.post("/permissions/pending/retry")
async def permissions_pending_retry(payload: dict):
    from core.permissions import permission_manager

    return permission_manager.approval_retry(payload.get("id", ""))


@router.get("/red-lines/policy")
async def red_lines_policy():
    from core.safety_policy import load_safety_policy

    policy = load_safety_policy()
    return {
        "version": policy.version,
        "name": policy.name,
        "summary": policy.summary,
        "zones": policy.zones,
        "rules": [rule.__dict__ for rule in policy.rules],
        "protected_paths": list(policy.protected_paths),
    }


@router.get("/safety/events")
async def safety_events(limit: int = 80):
    """Recent autonomy/safety audit events from the canonical EventBus."""
    from core.safety_report import is_safety_event
    from core.events import get_bus

    max_limit = max(1, min(200, int(limit)))
    events = [e.to_dict() for e in get_bus().recent(limit=max_limit * 4)]
    filtered = [e for e in events if is_safety_event(e)][-max_limit:]
    return {"events": filtered, "count": len(filtered)}


@router.get("/safety/report")
async def safety_report(format: str = "json", write: bool = False):
    """Generate an Autonomy Safety Report as JSON or Markdown."""
    from core.safety_report import generate_safety_report, write_safety_report

    report = generate_safety_report()
    if write:
        return write_safety_report(report)
    if format.lower() in {"md", "markdown"}:
        return {"markdown": report.to_markdown(), "report": report.to_dict()}
    return report.to_dict()


@router.post("/safety/preflight")
async def safety_preflight(payload: dict):
    """Pre-flight an autonomy goal without executing tools or creating approvals."""
    from core.autonomy_preflight import preflight_goal

    report = preflight_goal(payload.get("goal", ""), actions=payload.get("actions"))
    if str(payload.get("format", "json")).lower() in {"md", "markdown"}:
        return {"markdown": report.to_markdown(), "preflight": report.to_dict()}
    return report.to_dict()


@router.post("/safety/preflight/approvals")
async def safety_preflight_approvals(payload: dict):
    """Create draft pending approval prompts from pre-flight suggestions."""
    from core.autonomy_preflight import create_preflight_approval_requests

    return create_preflight_approval_requests(payload.get("goal", ""))


@router.get("/capabilities/registry")
async def capabilities_registry():
    from core.capability_registry import get_capability_registry

    return {"capabilities": get_capability_registry().list()}


@router.post("/capabilities/registry/register")
async def capabilities_registry_register(payload: dict):
    from core.capability_registry import get_capability_registry

    return get_capability_registry().register(
        payload.get("name") or payload.get("power") or "",
        category=payload.get("category", "generic"),
        status=payload.get("status", "missing"),
        source=payload.get("source", "dashboard"),
        notes=payload.get("notes", ""),
    )


@router.post("/capabilities/registry/setup")
async def capabilities_registry_setup(payload: dict):
    from core.capability_registry import get_capability_registry

    return get_capability_registry().setup_plan(payload.get("name") or payload.get("power") or "", write_proposal=bool(payload.get("write_proposal", True)))


@router.post("/capabilities/registry/request-activation")
async def capabilities_registry_request_activation(payload: dict):
    from core.capability_registry import get_capability_registry

    return get_capability_registry().request_activation(payload.get("name") or payload.get("id") or "", reason=payload.get("reason", ""))


@router.post("/capabilities/registry/activate")
async def capabilities_registry_activate(payload: dict):
    from core.capability_registry import get_capability_registry

    return get_capability_registry().activate(payload.get("name") or payload.get("id") or "", approval_id=payload.get("approval_id", ""))


@router.get("/capabilities/ledger")
async def capabilities_ledger():
    from core.capability_ledger import get_capability_ledger

    ledger = get_capability_ledger()
    return {"path": "CAPABILITY_LEDGER.md", "markdown": ledger.read(), "discovered": ledger.list_discovered()}


@router.post("/capabilities/ledger/discover")
async def capabilities_ledger_discover(payload: dict):
    from core.capability_ledger import CapabilityEntry, get_capability_ledger

    entry = CapabilityEntry.create(
        power=payload.get("power", ""),
        use=payload.get("use", ""),
        risk=payload.get("risk", ""),
        needed_approval_setup=payload.get("needed_approval_setup") or payload.get("approval", ""),
        status=payload.get("status", "not_granted"),
        source=payload.get("source", "dashboard"),
    )
    return get_capability_ledger().add_discovered(entry)


@router.post("/capabilities/ledger/propose")
async def capabilities_ledger_propose(payload: dict):
    from core.capability_ledger import get_capability_ledger

    return get_capability_ledger().propose(
        payload.get("power", ""),
        write=bool(payload.get("write", False)),
    )


@router.get("/emergency/status")
async def emergency_status():
    from core.emergency_stop import get_emergency_stop

    return {"emergency_stop": get_emergency_stop().state().to_dict()}


@router.post("/emergency/stop")
async def emergency_stop(payload: dict = None):
    from core.emergency_stop import get_emergency_stop

    payload = payload or {}
    return get_emergency_stop().activate(payload.get("reason", "dashboard emergency stop"), set_by=payload.get("set_by", "user"))


@router.post("/emergency/resume")
async def emergency_resume(payload: dict = None):
    from core.emergency_stop import get_emergency_stop

    payload = payload or {}
    return get_emergency_stop().clear(payload.get("reason", "manual resume"), set_by=payload.get("set_by", "user"))


@router.post("/research")
async def research_run(payload: dict):
    from core.research import research_pipeline

    payload = payload or {}
    denied = _permission_guard("web_search", {"query": payload.get("query", ""), "limit": payload.get("limit", 10)})
    if denied is not None:
        return denied
    return research_pipeline.run(payload.get("query", ""), limit=int(payload.get("limit", 10)))


@router.post("/local-defense/scan")
async def local_defense_scan(payload: dict):
    from core.local_defense_scanner import scan_folder

    payload = payload or {}
    args = {
        "path": payload.get("path", ""),
        "max_files": payload.get("max_files", 500),
        "max_bytes": payload.get("max_bytes", 4096),
        "follow_symlinks": bool(payload.get("follow_symlinks", False)),
        "purpose": payload.get("purpose", "defensive_scan"),
        "save_report": bool(payload.get("save_report", False)),
        "mission_id": payload.get("mission_id", ""),
    }
    denied = _permission_guard("local_folder_defensive_scan", args)
    if denied is not None:
        return denied
    return scan_folder(
        args["path"],
        max_files=int(args["max_files"] or 500),
        max_bytes=int(args["max_bytes"] or 4096),
        follow_symlinks=bool(args["follow_symlinks"]),
        save_report=bool(args["save_report"]),
        mission_id=str(args["mission_id"] or ""),
    )


@router.get("/local-defense/reports")
async def local_defense_reports(limit: int = 50):
    from core.local_defense_scanner import list_scan_reports

    reports = list_scan_reports(limit=limit)
    return {"count": len(reports), "reports": reports}


@router.get("/local-defense/reports/{name}")
async def local_defense_report_get(name: str):
    from core.local_defense_scanner import read_scan_report

    report = read_scan_report(name)
    if not report.get("success"):
        return JSONResponse(report, status_code=404)
    return report


@router.post("/local-defense/missions")
async def local_defense_mission_start(payload: dict):
    from core.local_defense_workflow import start_local_scan_mission

    payload = payload or {}
    report = start_local_scan_mission(
        payload.get("path", "~/Downloads"),
        purpose=payload.get("purpose", "malware"),
        max_files=int(payload.get("max_files", 500)),
    )
    return report.to_dict()


@router.post("/local-defense/missions/{mission_id}/run")
async def local_defense_mission_run(mission_id: str):
    from core.local_defense_workflow import run_local_scan_mission

    try:
        return run_local_scan_mission(mission_id).to_dict()
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=404)


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
    denied = _permission_guard("screen_record_start", {"fps": fps, "max_seconds": max_seconds, "output_path": output})
    if denied is not None:
        return denied
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
    denied = _permission_guard("screen_record_save", {"path": str(path), "seconds": seconds})
    if denied is not None:
        return denied
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

    denied = _permission_guard("screen_watch", payload or {})
    if denied is not None:
        return denied
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

    denied = _permission_guard("screen_action_before", payload or {})
    if denied is not None:
        return denied
    return _screen_action_manager().before(
        payload.get("action", ""), payload.get("expected_state", "")
    )


@router.post("/screen/action/after")
async def screen_action_after(payload: dict):
    from core.computer import ScreenVerifier, VideoAnalyzer
    from core.integrations import _screen_action_manager

    denied = _permission_guard("screen_action_after", payload or {})
    if denied is not None:
        return denied
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
