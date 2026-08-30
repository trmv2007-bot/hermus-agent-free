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

from typing import Any

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
    def research_deep(query: str, limit: int = 10) -> dict[str, Any]:
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
    def memory2_recall(query: str, limit: int = 10, kinds: str = "") -> dict[str, Any]:
        from .memory import memory, KINDS

        k = [x.strip() for x in (kinds or "").split(",") if x.strip()] or None
        if k:
            k = [x for x in k if x in KINDS]
        project = resolve_active_project()
        res = memory.recall(query, limit=limit, kinds=k, project=project)
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
                         success: str = "none") -> dict[str, Any]:
        from .memory import memory

        s = None if success == "none" else (success == "true")
        r = memory.remember(kind, content, importance=importance, success=s,
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
    def router_choose(text: str) -> dict[str, Any]:
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
    def workspace_list_projects() -> dict[str, Any]:
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
    def screen_record_start(
        max_seconds: float = 30.0,
        fps: float = 10.0,
        output_path: str = "",
    ) -> dict[str, Any]:
        from .computer import recording_policy

        valid = recording_policy.validate_settings(fps, max_seconds)
        if not valid.get("ok"):
            return {"success": False, "error": valid["error"]}
        path = None
        if output_path:
            try:
                path = str(recording_policy.output_path(output_path))
            except (ValueError, PermissionError) as exc:
                return {"success": False, "error": str(exc)}
        return _screen_recorder().start(max_seconds=max_seconds, fps=fps, output_path=path)

    def screen_record_stop() -> dict[str, Any]:
        return _screen_recorder().stop()

    def screen_record_status() -> dict[str, Any]:
        return _screen_recorder().status()

    def screen_record_save(path: str = "recording.mp4", seconds: float = 0.0, task_id: str = "") -> dict[str, Any]:
        from .computer import TaskArtifacts, VideoAnalyzer, recording_policy

        rec = _screen_recorder()
        if task_id:
            safe = recording_policy.task_id(task_id)
            target = recording_policy.output_path(f"{safe}/recording.mp4")
        else:
            try:
                target = recording_policy.output_path(path)
            except (ValueError, PermissionError) as exc:
                return {"success": False, "error": str(exc)}
        saved = rec.save(str(target), seconds=seconds if seconds > 0 else None)
        if not saved.get("success") or not task_id:
            return saved
        frames = rec.recent(seconds) if seconds > 0 else rec.all_frames()
        analysis = VideoAnalyzer().analyze(frames, task=task_id, recording=str(target))
        bundle = TaskArtifacts(task_id, root=str(recording_policy.root)).write(
            timeline=analysis.get("timeline"),
            events=analysis.get("events"),
            actions=rec.markers(),
            result={"recording": saved, "analysis": {"semantic": analysis.get("semantic")}},
            recording_path=str(target),
        )
        return {**saved, "bundle": bundle}

    def screen_get_recent(seconds: float = 10.0) -> dict[str, Any]:
        frames = _screen_recorder().recent(seconds=seconds)
        return {
            "frames": len(frames),
            "sample": [f.get("ts") for f in frames[-5:]],
            "buffer_bytes": sum(int(f.get("bytes") or 0) for f in frames),
        }

    def screen_analyze(
        task: str = "",
        seconds: float = 10.0,
        max_events: int = 12,
        use_vision: bool = True,
        model: str = "llava:7b",
    ) -> dict[str, Any]:
        from .computer import VideoAnalyzer

        frames = _screen_recorder().recent(seconds=seconds)
        analyzer = VideoAnalyzer.with_ollama(model) if use_vision else VideoAnalyzer()
        return analyzer.analyze(frames, task=task, max_events=max_events)

    def screen_understand(description: str, seconds: float = 10.0) -> dict[str, Any]:
        from .computer import FrameSampler, ScreenVerifier

        frames = _screen_recorder().recent(seconds=seconds)
        summary = FrameSampler().summarize(frames)
        verification = ScreenVerifier().verify_sequence(frames, expected_state=description)
        return {"description": description, "summary": summary, "verification": verification}

    def screen_verify(
        expected_state: str,
        seconds: float = 10.0,
        action: str = "",
        use_vision: bool = False,
        remember: bool = False,
    ) -> dict[str, Any]:
        from .computer import ScreenVerifier, VideoAnalyzer

        frames = _screen_recorder().recent(seconds=seconds)
        analyzer = VideoAnalyzer.with_ollama() if use_vision else None
        callback = analyzer.evaluate_condition if analyzer else None
        transition = analyzer.evaluate_transition if analyzer else None
        result = ScreenVerifier(vision_model=callback, transition_model=transition).verify_sequence(
            frames, expected_state=expected_state, action=action
        )
        if remember and action and result.get("ok"):
            try:
                from .memory import memory as mem

                mem.remember(
                    "procedural",
                    f"ACTION: {action}\nVISUAL RESULT: {result.get('visual_result')}\nCONFIDENCE: {result.get('confidence')}",
                    importance=7.0,
                    success=True,
                    project=resolve_active_project(),
                    metadata={"visual_evidence": result.get("evidence", {})},
                )
                result["remembered"] = True
            except Exception as exc:
                result["memory_error"] = str(exc)
        return result

    def screen_watch(
        condition: str,
        timeout: float = 60.0,
        stable_matches: int = 1,
        model: str = "llava:7b",
    ) -> dict[str, Any]:
        from .computer import ScreenWatcher, VideoAnalyzer

        analyzer = VideoAnalyzer.with_ollama(model)
        return ScreenWatcher(_screen_recorder(), analyzer=analyzer).watch(
            condition, timeout=timeout, stable_matches=stable_matches
        )

    def screen_action_before(action: str, expected_state: str = "") -> dict[str, Any]:
        return _screen_action_manager().before(action, expected_state)

    def screen_action_after(
        action_id: str,
        use_vision: bool = False,
        model: str = "llava:7b",
        remember: bool = False,
    ) -> dict[str, Any]:
        from .computer import ScreenVerifier, VideoAnalyzer

        analyzer = VideoAnalyzer.with_ollama(model) if use_vision else None
        verifier = ScreenVerifier(
            vision_model=analyzer.evaluate_condition if analyzer else None,
            transition_model=analyzer.evaluate_transition if analyzer else None,
        )
        result = _screen_action_manager().after(action_id, verifier=verifier)
        verification = result.get("verification") or {}
        memory = verification.get("memory") or {}
        if remember and memory.get("success"):
            try:
                from .memory import memory as mem

                mem.remember(
                    "procedural",
                    f"ACTION: {memory.get('action')}\nVISUAL RESULT: {memory.get('visual_result')}\nCONFIDENCE: {memory.get('confidence')}",
                    importance=7.0,
                    success=True,
                    project=resolve_active_project(),
                    metadata={"visual_evidence": memory.get("evidence", {})},
                )
                result["remembered"] = True
            except Exception as exc:
                result["memory_error"] = str(exc)
        return result

    definitions = [
        ("screen_record_start", screen_record_start, "Start compressed rolling capture; optionally stream the full session to MP4/WebM.", {"max_seconds": {"type": "number", "default": 30.0}, "fps": {"type": "number", "default": 10.0}, "output_path": {"type": "string", "default": ""}}, []),
        ("screen_record_stop", screen_record_stop, "Stop capture and finalize any active video.", {}, []),
        ("screen_record_status", screen_record_status, "Recorder, compressed-RAM, and video-writer status.", {}, []),
        ("screen_record_save", screen_record_save, "Save the rolling buffer as MP4/WebM, optionally as a task artifact bundle.", {"path": {"type": "string", "default": "recording.mp4"}, "seconds": {"type": "number", "default": 0.0}, "task_id": {"type": "string", "default": ""}}, []),
        ("screen_get_recent", screen_get_recent, "Get recent frame timestamps and compressed buffer size.", {"seconds": {"type": "number", "default": 10.0}}, []),
        ("screen_analyze", screen_analyze, "Detect important screen events and generate an agent-readable visual timeline.", {"task": {"type": "string", "default": ""}, "seconds": {"type": "number", "default": 10.0}, "max_events": {"type": "integer", "default": 12}, "use_vision": {"type": "boolean", "default": True}, "model": {"type": "string", "default": "llava:7b"}}, []),
        ("screen_understand", screen_understand, "Summarize recent changes and compare the first/last screen.", {"description": {"type": "string"}, "seconds": {"type": "number", "default": 10.0}}, ["description"]),
        ("screen_verify", screen_verify, "Before/after verification with optional semantic vision and procedural visual memory.", {"expected_state": {"type": "string"}, "seconds": {"type": "number", "default": 10.0}, "action": {"type": "string", "default": ""}, "use_vision": {"type": "boolean", "default": False}, "remember": {"type": "boolean", "default": False}}, ["expected_state"]),
        ("screen_action_before", screen_action_before, "Capture the exact BEFORE frame for an upcoming GUI action.", {"action": {"type": "string"}, "expected_state": {"type": "string", "default": ""}}, ["action"]),
        ("screen_action_after", screen_action_after, "Capture AFTER and verify it against a prior screen_action_before boundary.", {"action_id": {"type": "string"}, "use_vision": {"type": "boolean", "default": False}, "model": {"type": "string", "default": "llava:7b"}, "remember": {"type": "boolean", "default": False}}, ["action_id"]),
        ("screen_watch", screen_watch, "Watch changed frames until a visual condition is true or timeout expires.", {"condition": {"type": "string"}, "timeout": {"type": "number", "default": 60.0}, "stable_matches": {"type": "integer", "default": 1}, "model": {"type": "string", "default": "llava:7b"}}, ["condition"]),
    ]
    for name, fn, desc, props, required in definitions:
        registry.register(
            name,
            fn,
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {"type": "object", "properties": props, "required": required},
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


def _screen_action_manager():
    from .computer import ActionVerificationManager

    recorder = _screen_recorder()
    manager = getattr(_screen_action_manager, "manager", None)
    if manager is None or manager.recorder is not recorder:
        manager = ActionVerificationManager(recorder)
        _screen_action_manager.manager = manager
    return manager


def maybe_self_heal(result: dict[str, Any]) -> dict[str, Any]:
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
