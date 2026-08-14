"""Architecture-upgrade integrations — wires the foundation subsystems into the
live tool registry and agent loop.

- `register_architecture_tools(registry)` adds research / screen / memory2 /
  router / workspace tools to the central registry.
- `resolve_active_project()` returns the effective project (config → workspace
  current → default).
- `maybe_self_heal(result)` runs the watchdog on a failed agent result and
  attaches a `self_healing` block.
"""
from __future__ import annotations

from typing import Any, Dict

from .config import config
from .workspace import workspace


def resolve_active_project() -> str:
    try:
        return workspace.active_project()
    except Exception:
        return getattr(config, "project", "default") or "default"


def register_architecture_tools(registry) -> None:
    """Register the upgrade tools. Safe to call multiple times (idempotent)."""
    # ---- Research pipeline -------------------------------------------------
    def research_deep(query: str, limit: int = 10) -> Dict[str, Any]:
        from .research import research_pipeline

        return research_pipeline.run(query, limit=limit)

    registry.register(
        "research_deep",
        research_deep,
        {
            "type": "function",
            "function": {
                "name": "research_deep",
                "description": "Multi-source research: search → rank → extract claims → cross-check → contradictions → synthesis with citations, confidence and uncertain claims.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
            },
        },
        source="core.integrations",
    )

    # ---- Memory 2.0 --------------------------------------------------------
    def memory2_recall(query: str, limit: int = 10, kinds: str = "") -> Dict[str, Any]:
        from .memory2 import memory2, KINDS

        k = [x.strip() for x in (kinds or "").split(",") if x.strip()] or None
        if k:
            k = [x for x in k if x in KINDS]
        project = resolve_active_project()
        res = memory2.recall(query, limit=limit, kinds=k, project=project)
        return {"query": query, "project": project, "results": res, "count": len(res)}

    registry.register(
        "memory2_recall",
        memory2_recall,
        {
            "type": "function",
            "function": {
                "name": "memory2_recall",
                "description": "Ranked recall from typed long-term memory (episodic/semantic/procedural/project), scored by importance/recency/frequency/relevance/success.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                        "kinds": {"type": "string", "description": "comma-separated kinds: working,episodic,semantic,procedural,project"},
                    },
                    "required": ["query"],
                },
            },
        },
        source="core.integrations",
    )

    def memory2_remember(kind: str, content: str, importance: float = 5.0,
                         success: str = "none") -> Dict[str, Any]:
        from .memory2 import memory2

        s = None if success == "none" else (success == "true")
        r = memory2.remember(kind, content, importance=importance, success=s,
                             project=resolve_active_project())
        return r

    registry.register(
        "memory2_remember",
        memory2_remember,
        {
            "type": "function",
            "function": {
                "name": "memory2_remember",
                "description": "Persist a typed long-term memory (working/episodic/semantic/procedural/project).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "content": {"type": "string"},
                        "importance": {"type": "number", "default": 5.0},
                        "success": {"type": "string", "description": "true|false|none"},
                    },
                    "required": ["kind", "content"],
                },
            },
        },
        source="core.integrations",
    )

    # ---- Model router ------------------------------------------------------
    def router_choose(text: str) -> Dict[str, Any]:
        from .router2 import router2

        return router2.select(text)

    registry.register(
        "router_choose",
        router_choose,
        {
            "type": "function",
            "function": {
                "name": "router_choose",
                "description": "Choose the best available model for a single step (task type, difficulty, context, provider health).",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            },
        },
        source="core.integrations",
    )

    # ---- Workspace ---------------------------------------------------------
    def workspace_list_projects() -> Dict[str, Any]:
        return {"projects": workspace.list_projects(), "current": workspace.current_project()}

    registry.register(
        "workspace_list_projects",
        workspace_list_projects,
        {
            "type": "function",
            "function": {
                "name": "workspace_list_projects",
                "description": "List workspace projects and the current active project.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        source="core.integrations",
    )

    # ---- Computer control (screen) ----------------------------------------
    def screen_record_start(max_seconds: float = 30.0, fps: float = 10.0) -> Dict[str, Any]:
        from .computer import ScreenRecorder

        rec = _screen_recorder()
        return rec.start()

    def screen_record_stop() -> Dict[str, Any]:
        return _screen_recorder().stop()

    def screen_record_status() -> Dict[str, Any]:
        return _screen_recorder().status()

    def screen_get_recent(seconds: float = 10.0) -> Dict[str, Any]:
        frames = _screen_recorder().recent(seconds=seconds)
        return {"frames": len(frames), "sample": [f.get("ts") for f in frames[-5:]]}

    def screen_understand(description: str, seconds: float = 10.0) -> Dict[str, Any]:
        from .computer import FrameSampler, ScreenVerifier

        rec = _screen_recorder()
        frames = rec.recent(seconds=seconds)
        sampler = FrameSampler()
        verifier = ScreenVerifier()
        summary = sampler.summarize(frames)
        v = verifier.verify_sequence(frames, expected_state=description)
        return {"description": description, "summary": summary, "verification": v}

    def screen_verify(expected_state: str, seconds: float = 10.0) -> Dict[str, Any]:
        from .computer import ScreenVerifier

        frames = _screen_recorder().recent(seconds=seconds)
        return ScreenVerifier().verify_sequence(frames, expected_state=expected_state)

    for name, fn, desc, props in [
        ("screen_record_start", screen_record_start, "Start the rolling screen recorder (last N seconds buffered).", {"max_seconds": {"type": "number", "default": 30.0}, "fps": {"type": "number", "default": 10.0}}),
        ("screen_record_stop", screen_record_stop, "Stop the screen recorder.", {}),
        ("screen_record_status", screen_record_status, "Recorder status (running, frames buffered).", {}),
        ("screen_get_recent", screen_get_recent, "Get frames from the last N seconds (timestamps).", {"seconds": {"type": "number", "default": 10.0}}),
        ("screen_understand", screen_understand, "Analyze recent screen changes + verify against a description.", {"description": {"type": "string"}, "seconds": {"type": "number", "default": 10.0}}),
        ("screen_verify", screen_verify, "Verify the screen reached an expected state.", {"expected_state": {"type": "string"}, "seconds": {"type": "number", "default": 10.0}}),
    ]:
        registry.register(
            name,
            fn,
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {"type": "object", "properties": props, "required": list(props.keys()) if name in ("screen_understand", "screen_verify") else []},
                },
            },
            source="core.integrations",
        )


def _screen_recorder():
    # module-level singleton, created lazily to avoid a display grab at import time
    from .computer import ScreenRecorder, NullSource, ImageGrabSource

    if not hasattr(_screen_recorder, "rec"):
        try:
            source = ImageGrabSource()
        except Exception:
            source = NullSource()
        _screen_recorder.rec = ScreenRecorder(source=source)
    return _screen_recorder.rec


def maybe_self_heal(result: Dict[str, Any]) -> Dict[str, Any]:
    """Attach a watchdog diagnosis when a task/tool result shows errors."""
    if not getattr(config, "watchdog_enabled", True):
        return result
    try:
        errors = []
        for tr in result.get("tool_results") or []:
            txt = str(tr.get("result", ""))
            low = txt.lower()
            if "error" in low[:300] or "failed" in low[:300]:
                errors.append(txt[:300])
        if errors:
            from .watchdog import watchdog

            healed = [watchdog.handle(e) for e in errors[:3]]
            result["self_healing"] = healed
    except Exception:
        pass
    return result
