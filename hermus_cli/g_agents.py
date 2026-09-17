"""agents commands — the Hermus CLI's agents group.

Part of the grouped CLI: one module per capability group instead of one
module per command. Each command keeps its own ``configure``/``run`` pair,
so the command bodies are unchanged while the import shim, docstring and
``TYPE_CHECKING`` block that every one of the forty-three files carried are
gone.
"""

from __future__ import annotations

import sys
from pathlib import Path

from core.config import config

from ._common import CLIContext, add_computer_task_args, add_screen_start_args
from ._spec import Command, no_action


def _configure_agent(subparsers) -> None:
    agent_parser = subparsers.add_parser("agent", help="Persistent background agents")
    agent_sub = agent_parser.add_subparsers(dest="agent_action")
    agent_create = agent_sub.add_parser("create", help="Create a named agent")
    agent_create.add_argument("name")
    agent_create.add_argument(
        "--role",
        default="generic",
        choices=[
            "researcher",
            "coder",
            "system-monitor",
            "scheduler",
            "memory-manager",
            "watchdog",
            "computer-operator",
            "coordinator",
            "generic",
        ],
    )
    agent_create.add_argument("--model", default=None)
    agent_start = agent_sub.add_parser("start", help="Start a background agent worker")
    agent_start.add_argument("name")
    agent_status = agent_sub.add_parser("status", help="Inspect an agent")
    agent_status.add_argument("name")
    agent_stop = agent_sub.add_parser("stop", help="Stop an agent")
    agent_stop.add_argument("name")
    agent_job = agent_sub.add_parser("job", help="Queue a task for a background agent")
    agent_job.add_argument("name")
    agent_job.add_argument("task", nargs="+")
    agent_job.add_argument("--wait", action="store_true")
    agent_job.add_argument("--timeout", type=float, default=180.0)
    agent_result = agent_sub.add_parser("result", help="Inspect a background job")
    agent_result.add_argument("name")
    agent_result.add_argument("job_id")
    agent_sub.add_parser("list", help="List all agents")


def _run_agent(args, ctx: CLIContext) -> None:
    from core.agent_manager import agent_manager

    if args.agent_action == "create":
        r = agent_manager.create(args.name, role=args.role, model=args.model)
        print(f"{'✅' if r.get('success') else '❌'} {r.get('name') or r.get('error')} (role={r.get('role', '')})")
    elif args.agent_action == "start":
        r = agent_manager.start(args.name)
        print(
            f"{'✅' if r.get('success') else '❌'} {args.name} ready (execution on canonical job queue)"
            if r.get("success")
            else f"❌ {r.get('error')}"
        )
    elif args.agent_action == "status":
        s = agent_manager.status(args.name)
        if not s.get("success"):
            print(f"❌ {s.get('error')}")
        else:
            print(f" {args.name} | role={s.get('role')} status={s.get('status')} queue={s.get('queue')}")
    elif args.agent_action == "stop":
        r = agent_manager.stop(args.name)
        print(f"{'✅' if r.get('success') else '❌'} {args.name} stopped")
    elif args.agent_action == "job":
        r = agent_manager.submit_job(args.name, {"task": " ".join(args.task)})
        if r.get("success") and args.wait:
            r = agent_manager.wait_job(args.name, r["job_id"], timeout=args.timeout)
        print(__import__("json").dumps(r, indent=2, default=str))
    elif args.agent_action == "result":
        print(__import__("json").dumps(agent_manager.job_status(args.name, args.job_id), indent=2, default=str))
    elif args.agent_action == "list":
        agents = agent_manager.list()
        if not agents:
            print("No agents. Create one: hermus agent create researcher --role researcher")
        for a in agents:
            print(f" - {a.get('name')} | role={a.get('role')} status={a.get('status')} alive={a.get('alive')}")
    else:
        no_action(ctx, "agent")


def _configure_run(subparsers) -> None:
    run_parser = subparsers.add_parser("run", help="Autonomous task loop - plan/execute/verify/repair")
    run_parser.add_argument("task", help="Goal to drive through the verify/repair loop")
    run_parser.add_argument("--model", default=None, help="Model to run with (default: config.model)")
    run_parser.add_argument("--max-repairs", type=int, default=2, help="Max diagnose/repair cycles")


def _run_run(args, ctx: CLIContext) -> None:
    from core.agent import HermusAgent

    agent = HermusAgent(model=args.model)
    report = agent.autonomous(args.task, max_repairs=args.max_repairs)
    print(f"Autonomous run: status={report['status']} verified={report['verified']} repairs={report['repairs']}")
    for s in report["steps"]:
        print(f"  [{s['status']}] {s['goal'][:70]} (attempts={s['attempts']})")
    print(f"\nResult:\n{str(report['final_answer'])[:1500]}")


def _configure_plan(subparsers) -> None:
    plan_parser = subparsers.add_parser("plan", help="Plans - DeepThink plan persistence & resume")
    plan_sub = plan_parser.add_subparsers(dest="plan_action")
    plan_sub.add_parser("list", help="List saved plans")
    plan_show = plan_sub.add_parser("show", help="Show a plan")
    plan_show.add_argument("session_id")
    plan_resume = plan_sub.add_parser("resume", help="Resume a plan (runs remaining steps)")
    plan_resume.add_argument("session_id")
    plan_resume.add_argument("--model", default=None)


def _run_plan(args, ctx: CLIContext) -> None:
    from core.reasoning.scaffold import list_plans, resume_plan, show_plan

    if args.plan_action == "list":
        plans = list_plans()
        if not plans:
            print("No plans saved yet — ask a multi-step task (DeepThink on) or run `hermus counsel run`.")
        for p in plans:
            print(f"  {p['session_id'][:38]:38s} steps={p['steps']} done={p['done']} status={p['status']} | {p['goal']}")
    elif args.plan_action == "show":
        plan = show_plan(args.session_id)
        if not plan:
            print(f"No plan found for '{args.session_id}'. Try `hermus plan list`")
        else:
            print(plan.to_prompt())
    elif args.plan_action == "resume":
        res = resume_plan(args.session_id, model=args.model)
        print(f"Resume result: success={res.get('success')} remaining_before={res.get('remaining_before')}")
        print(f"Response: {str(res.get('response'))[:400]}")
        if res.get("plan") and res["plan"].get("steps"):
            print("Plan state:")
            for i, s in enumerate(res["plan"]["steps"], 1):
                print(f"   {i}. [{s.get('status', '?')}] {s.get('goal', '')[:60]}")
    else:
        no_action(ctx, "plan")


def _configure_mission(subparsers) -> None:
    mission_parser = subparsers.add_parser("mission", help="Mission Engine — objective-driven lifecycle with verification")
    mission_sub = mission_parser.add_subparsers(dest="mission_action")
    m_start = mission_sub.add_parser("start", help="Start a new goal-driven mission")
    m_start.add_argument("goal", help="Mission goal")
    m_start.add_argument(
        "--domain", default="auto", help="Domain verifier (python, android, web, git, linux, research, file, auto)"
    )
    m_start.add_argument(
        "--budget", type=int, default=48, help="Step budget for the whole lifecycle (planning/execution/verification/repair)"
    )
    m_start.add_argument("--req", action="append", default=None, help="Specific requirement (can be repeated)")
    m_start.add_argument("--skip-preflight", action="store_true", help="Start without the autonomy pre-flight checklist")
    m_start.add_argument(
        "--allow-planning-blocked",
        action="store_true",
        help="Record NEEDS_APPROVAL/MISSING_CAPABILITY as a blocked planning-mode mission; red-line/emergency blockers still refuse",
    )
    m_resume = mission_sub.add_parser("resume", help="Resume a mission by ID")
    m_resume.add_argument("mission_id")
    m_resume.add_argument(
        "--restart-failed", action="store_true", help="Restart a FAILED mission (failed is terminal by default)"
    )
    m_resume.add_argument("--extra-steps", type=int, default=None, help="Grant this many extra steps before resuming")
    m_extend = mission_sub.add_parser("extend", help="Grant extra step budget to a mission")
    m_extend.add_argument("mission_id")
    m_extend.add_argument("--steps", type=int, default=10, help="Extra steps to grant (default 10)")
    m_extend.add_argument(
        "--emergency", action="store_true", help="Use the emergency reserve (when normal extension slots are used up)"
    )
    m_status = mission_sub.add_parser("status", help="Check status of a mission")
    m_status.add_argument("mission_id")
    mission_sub.add_parser("list", help="List all missions")


def _run_mission(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.mission import mission_engine

    if args.mission_action == "start":
        if not args.skip_preflight:
            try:
                from core.autonomy_preflight import preflight_goal

                pf = preflight_goal(args.goal)
                print(pf.to_markdown())
                print("\n--- mission start ---")
            except Exception as exc:
                print(f"Pre-flight unavailable; mission engine will fail closed: {exc}")
        report = mission_engine.start_mission(
            goal=args.goal,
            requirements=args.req,
            domain=None if args.domain == "auto" else args.domain,
            budget_steps=args.budget,
            preflight=not args.skip_preflight,
            allow_preflight_planning=bool(args.allow_planning_blocked),
        )
        print(json_lib.dumps(report.to_dict(), indent=2))
    elif args.mission_action == "resume":
        try:
            report = mission_engine.resume_mission(
                args.mission_id,
                restart_failed=bool(getattr(args, "restart_failed", False)),
                extra_steps=getattr(args, "extra_steps", None),
            )
            print(json_lib.dumps(report.to_dict(), indent=2))
        except ValueError as e:
            print(f"Error: {e}")
    elif args.mission_action == "extend":
        try:
            report = mission_engine.extend_budget(
                args.mission_id,
                steps=args.steps,
                emergency=bool(getattr(args, "emergency", False)),
            )
            print(
                f"Budget extended: +{args.steps} steps "
                f"(extensions {report.budget.extensions_used}/{report.budget.max_extensions}, "
                f"emergency {report.budget.emergency_extensions}/{report.budget.max_emergency_extensions}, "
                f"step limit now {report.budget.total_steps()})"
            )
            print(json_lib.dumps(report.budget.to_dict(), indent=2))
        except ValueError as e:
            print(f"Error: {e}")
    elif args.mission_action == "status":
        report = mission_engine.get_mission(args.mission_id)
        if report:
            print(json_lib.dumps(report.to_dict(), indent=2))
        else:
            print(f"Mission '{args.mission_id}' not found")
    elif args.mission_action == "list":
        missions = mission_engine.list_missions()
        print(f"Missions ({len(missions)}):")
        for m in missions:
            print(f" - [{m.state.upper()}] {m.mission_id}: {m.goal[:60]} (Progress: {m.progress_pct}%)")
    else:
        no_action(ctx, "mission")


def _configure_delegate(subparsers) -> None:
    deleg_parser = subparsers.add_parser("delegate", help="Fan work out to parallel sub-agents (JSON-RPC workers)")
    deleg_parser.add_argument("goal", nargs="+")
    deleg_parser.add_argument(
        "--task", dest="tasks", action="append", default=None, help="Explicit workstream (repeatable); omit to auto-plan"
    )
    deleg_parser.add_argument("--max-children", type=int, default=4)
    deleg_parser.add_argument("--aggregate", default="synthesize", choices=["synthesize", "concat", "vote", "best"])
    deleg_parser.add_argument("--model", default=None)
    deleg_parser.add_argument("--json", action="store_true", help="Print the full structured tree as JSON")


def _run_delegate(args, ctx: CLIContext) -> None:
    from core.delegation import delegation

    goal = " ".join(args.goal)
    sink = (
        (lambda t, d: print(f"  · {t}: {str(d.get('task') or d.get('tool') or d.get('answer') or '')[:80]}"))
        if not args.json
        else None
    )
    if args.tasks:
        out = delegation.fanout(
            args.tasks,
            goal=goal,
            max_children=args.max_children,
            aggregate=args.aggregate,
            model=args.model or "",
            on_event=sink,
        )
    else:
        out = delegation.decompose_and_run(
            goal, max_children=args.max_children, aggregate=args.aggregate, model=args.model or "", on_event=sink
        )
    if args.json:
        print(__import__("json").dumps(out, indent=2, default=str))
    else:
        agg = out.get("aggregate") or {}
        print(
            f"\ndelegated '{str(out.get('goal'))[:60]}' → {out.get('succeeded')}/{out.get('children')} "
            f"children ok ({out.get('duration_ms')}ms, tree={out.get('tree_id')})"
        )
        for n in out.get("nodes") or []:
            mark = {"done": "✅", "failed": "❌", "cancelled": "⛔", "timeout": "⏱"}.get(n.get("status"), "…")
            print(
                f" {mark} {str(n.get('task'))[:56]:58s} {n.get('status')} "
                f"[{n.get('backend')}] {n.get('duration_ms')}ms tools={len(n.get('tool_calls') or [])}"
            )
        sections = agg.get("sections") or []
        if isinstance(sections, dict):  # older handlers returned a mapping
            sections = [{"child": k, "answer": v} for k, v in sections.items()]
        for sec in sections:
            conf = sec.get("confidence")
            print(f"\n### {sec.get('child', 'section')}" + (f"  (conf {conf})" if conf is not None else ""))
            print(str(sec.get("answer", ""))[:900])
        if not sections and agg.get("answer"):
            print(f"\n{str(agg['answer'])[:3000]}")
        if agg.get("disagreement"):
            print(f"\n(disagreement among children: {agg['disagreement']:.0%})")
    if out.get("errors"):
        print("errors:", "; ".join(str(e)[:120] for e in out["errors"]))


def _configure_subagent(subparsers) -> None:
    subagent_parser = subparsers.add_parser("subagent", help="Subagents - parallel work")
    subagent_parser.add_argument("action", choices=["spawn"], help="spawn")
    subagent_parser.add_argument("task", help="Task for subagent")


def _run_subagent(args, ctx: CLIContext) -> None:
    if args.action == "spawn":
        from subagents.subagent import spawn_subagent

        result = spawn_subagent(args.task)
        print(f"Subagent result: {result}")


def _configure_computer(subparsers) -> None:
    computer_parser = subparsers.add_parser("computer", help="Autonomous computer agent - plan/act/record/verify/repair")
    computer_sub = computer_parser.add_subparsers(dest="computer_action")

    computer_task = computer_sub.add_parser("task", help="Run a desktop task autonomously")
    add_computer_task_args(computer_task)
    computer_run = computer_sub.add_parser("run", help="Run and persist a resumable desktop task")
    add_computer_task_args(computer_run)
    computer_resume = computer_sub.add_parser("resume", help="Resume a persisted desktop task")
    computer_resume.add_argument("task_id")
    computer_resume.add_argument("--model", default=None)
    computer_resume.add_argument("--retries", type=int, default=2)
    computer_resume.add_argument("--dry-run", action="store_true")
    computer_sub.add_parser("tasks", help="List persisted desktop tasks")
    computer_show = computer_sub.add_parser("show", help="Show a persisted desktop task checkpoint")
    computer_show.add_argument("task_id")
    computer_delegate = computer_sub.add_parser("delegate", help="Delegate a task across persistent agents")
    computer_delegate.add_argument("task", nargs="+")
    computer_delegate.add_argument("--no-wait", action="store_true")
    computer_delegate.add_argument("--timeout", type=float, default=180.0)
    computer_delegate.add_argument("--dry-run", action="store_true")
    computer_sub.add_parser("stop", help="Emergency stop - halt all mouse/keyboard/autonomous control")
    computer_sub.add_parser("status", help="Show the computer control center")
    computer_target = computer_sub.add_parser("target", help="Vision-driven find-on-screen for a UI element")
    computer_target.add_argument("target", nargs="+")
    computer_target.add_argument("--model", default="llava:7b")
    computer_click = computer_sub.add_parser("click", help="Vision-driven click: locate a UI element, then click it")
    computer_click.add_argument("target", nargs="+")
    computer_click.add_argument("--model", default="llava:7b")
    computer_wait = computer_sub.add_parser("wait", help="Wait until a visual condition is true")
    computer_wait.add_argument("condition", nargs="+")
    computer_wait.add_argument("--timeout", type=float, default=60.0)
    computer_wait.add_argument("--model", default="llava:7b")
    computer_sub.add_parser("skills", help="List learned computer skills")


def _run_computer(args, ctx: CLIContext) -> None:
    import json

    from core.computer import (
        ComputerActionController,
        ComputerAgent,
        ControlCenter,
        ScreenRecorder,
        ScreenWatcher,
        TargetDetector,
        TaskStore,
        VideoAnalyzer,
        emergency_stop,
    )

    def build_computer_agent(model=None, retries=2, learn_skills=True):
        analyzer = VideoAnalyzer.with_ollama(model) if model else None
        recorder = ScreenRecorder(fps=2.0, max_seconds=120.0)
        controller = ComputerActionController(
            frame_provider=recorder.latest,
            target_detector=TargetDetector(vision_model=analyzer.vision_model if analyzer else None),
        )
        return ComputerAgent(
            controller=controller,
            recorder=recorder,
            analyzer=analyzer,
            learn_skills=learn_skills,
            max_retries=retries,
        )

    if args.computer_action in ("task", "run"):
        task = " ".join(args.task)
        computer_agent = build_computer_agent(
            model=args.model,
            retries=args.retries,
            learn_skills=not args.no_skill,
        )
        result = computer_agent.run(task, task_id=args.task_id, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
    elif args.computer_action == "resume":
        computer_agent = build_computer_agent(model=args.model, retries=args.retries)
        print(json.dumps(computer_agent.resume(args.task_id, dry_run=args.dry_run), indent=2, default=str))
    elif args.computer_action == "tasks":
        print(json.dumps(TaskStore().list(), indent=2, default=str))
    elif args.computer_action == "show":
        checkpoint = TaskStore().load(args.task_id)
        print(
            json.dumps(
                checkpoint.to_dict() if checkpoint else {"success": False, "error": f"task '{args.task_id}' not found"},
                indent=2,
                default=str,
            )
        )
    elif args.computer_action == "delegate":
        from core.computer import MultiAgentDelegator

        delegator = MultiAgentDelegator()
        delegation_plan = delegator.plan(" ".join(args.task))
        result = delegator.execute(
            delegation_plan,
            wait=not args.no_wait,
            timeout_per_unit=args.timeout,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, default=str))
    elif args.computer_action == "stop":
        emergency_stop.halt()
        print(
            json.dumps(
                {
                    "success": True,
                    "halted": True,
                    "note": "mouse/keyboard/autonomous control halted. Release in-process or restart.",
                },
                indent=2,
            )
        )
    elif args.computer_action == "status":
        print(ControlCenter(ComputerActionController()).render())
    elif args.computer_action == "target":
        target = " ".join(args.target)
        analyzer = VideoAnalyzer.with_ollama(args.model)
        recorder = ScreenRecorder(fps=2.0, max_seconds=10.0)
        recorder.start()
        try:
            detector = TargetDetector(vision_model=analyzer.vision_model)
            controller = ComputerActionController(frame_provider=recorder.latest, target_detector=detector)
            print(json.dumps(controller.find_on_screen(target), indent=2, default=str))
        finally:
            recorder.stop()
    elif args.computer_action == "click":
        target = " ".join(args.target)
        analyzer = VideoAnalyzer.with_ollama(args.model)
        recorder = ScreenRecorder(fps=2.0, max_seconds=10.0)
        recorder.start()
        try:
            detector = TargetDetector(vision_model=analyzer.vision_model)
            controller = ComputerActionController(frame_provider=recorder.latest, target_detector=detector)
            print(json.dumps(controller.click_target(target), indent=2, default=str))
        finally:
            recorder.stop()
    elif args.computer_action == "wait":
        condition = " ".join(args.condition)
        analyzer = VideoAnalyzer.with_ollama(args.model)
        recorder = ScreenRecorder(fps=2.0, max_seconds=max(10.0, args.timeout))
        result = ScreenWatcher(recorder, analyzer=analyzer).watch(condition, timeout=args.timeout, start_if_needed=True)
        print(json.dumps(result, indent=2, default=str))
    elif args.computer_action == "skills":
        from core.computer import ComputerSkillStore

        skills = ComputerSkillStore().list_skills()
        if not skills:
            print("No computer skills learned yet.")
        for skill in skills:
            print(
                f" - {skill['name']}: {skill['task']} "
                f"({skill['steps']} steps, {skill['successes']}/{skill['runs']} successful, "
                f"rate={skill['success_rate']:.1%}, avg={skill['average_duration']:.1f}s, "
                f"repairs={skill['known_repairs']})"
            )
    else:
        no_action(ctx, "computer")


def _configure_screen(subparsers) -> None:
    screen_parser = subparsers.add_parser("screen", help="Screen recording, visual timelines, verification and watching")
    screen_sub = screen_parser.add_subparsers(dest="screen_action")

    # Backward-compatible short form: hermus screen start|stop|status|save
    screen_start = screen_sub.add_parser("start", help="Start the background recorder")
    add_screen_start_args(screen_start)
    screen_sub.add_parser("stop", help="Stop and finalize the background recorder")
    screen_sub.add_parser("status", help="Background recorder status")
    screen_save = screen_sub.add_parser("save", help="Save the last recording as a video or task bundle")
    screen_save.add_argument("target", help="Filename (.mp4/.webm) or task id")

    # Explicit form from the Computer Agent architecture:
    # hermus screen record start|stop|status|save
    screen_record = screen_sub.add_parser("record", help="Conventional MP4/WebM screen recording")
    record_sub = screen_record.add_subparsers(dest="record_action")
    record_start = record_sub.add_parser("start", help="Start recording in a detached local service")
    add_screen_start_args(record_start)
    record_sub.add_parser("stop", help="Stop and finalize recording")
    record_sub.add_parser("status", help="Show recording status")
    record_save = record_sub.add_parser("save", help="Save the latest video or complete task bundle")
    record_save.add_argument("target", help="Filename (.mp4/.webm) or task id")

    screen_analyze = screen_sub.add_parser("analyze", help="Generate a visual event timeline from a recording")
    screen_analyze.add_argument("video", nargs="?", default="", help="MP4/WebM path (default: latest recording)")
    screen_analyze.add_argument("--task", default="Screen recording")
    screen_analyze.add_argument("--sample-fps", type=float, default=2.0)
    screen_analyze.add_argument("--max-seconds", type=float, default=3600.0)
    screen_analyze.add_argument("--max-events", type=int, default=12)
    screen_analyze.add_argument("--model", default="llava:7b")
    screen_analyze.add_argument("--no-vision", action="store_true", help="Detect changes without running a vision model")

    screen_watch = screen_sub.add_parser("watch", help="Wait until a visual condition becomes true")
    screen_watch.add_argument("condition")
    screen_watch.add_argument("--timeout", type=float, default=60.0)
    screen_watch.add_argument("--fps", type=float, default=2.0)
    screen_watch.add_argument("--model", default="llava:7b")


def _run_screen(args, ctx: CLIContext) -> None:
    import json

    from core.computer.service import ScreenRecordingService

    service = ScreenRecordingService()
    # Normalize short and explicit record forms to one action.
    action = args.record_action if args.screen_action == "record" else args.screen_action
    if action == "start":
        result = service.start(
            fps=args.fps,
            max_seconds=args.buffer_seconds,
            container=args.format,
        )
        print(json.dumps(result, indent=2, default=str))
    elif action == "stop":
        print(json.dumps(service.stop(), indent=2, default=str))
    elif action == "status":
        print(json.dumps(service.status(), indent=2, default=str))
    elif action == "save":
        print(json.dumps(service.save(args.target), indent=2, default=str))
    elif action == "analyze":
        from core.computer import VideoAnalyzer

        state = service.status()
        video = args.video or state.get("output_path") or ""
        analyzer = VideoAnalyzer() if args.no_vision else VideoAnalyzer.with_ollama(args.model)
        result = analyzer.analyze_video(
            video,
            task=args.task,
            sample_fps=args.sample_fps,
            max_seconds=args.max_seconds,
            max_events=args.max_events,
        )
        if result.get("success") and video:
            source = Path(video).expanduser().resolve()
            timeline_path = source.parent / "timeline.json"
            events_path = source.parent / "events.json"
            timeline_path.write_text(json.dumps(result.get("timeline", {}), indent=2), encoding="utf-8")
            events_path.write_text(json.dumps(result.get("events", []), indent=2), encoding="utf-8")
            result["timeline_path"] = str(timeline_path)
            result["events_path"] = str(events_path)
        print(json.dumps(result, indent=2, default=str))
    elif action == "watch":
        from core.computer import ScreenRecorder, ScreenWatcher, VideoAnalyzer

        recorder = ScreenRecorder(fps=args.fps, max_seconds=max(10.0, args.timeout))
        analyzer = VideoAnalyzer.with_ollama(args.model)
        result = ScreenWatcher(recorder, analyzer=analyzer).watch(
            args.condition,
            timeout=args.timeout,
            start_if_needed=True,
        )
        print(json.dumps(result, indent=2, default=str))
    else:
        no_action(ctx, "screen")


def _configure_swe(subparsers) -> None:
    swe_parser = subparsers.add_parser("swe", help="Software Engineer Mode — full repo development & test loop")
    swe_sub = swe_parser.add_subparsers(dest="swe_action")
    swe_run = swe_sub.add_parser("run", help="Execute an engineering task")
    swe_run.add_argument("task", help="Task description")
    swe_run.add_argument("--repairs", type=int, default=3, help="Max repair rounds")


def _run_swe(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.swe_mode import swe_mode

    if args.swe_action == "run":
        res = swe_mode.execute(task=args.task, max_repairs=args.repairs)
        print(json_lib.dumps(res.to_dict(), indent=2))
    else:
        no_action(ctx, "swe")


def _configure_counsel(subparsers) -> None:
    counsel_parser = subparsers.add_parser(
        "counsel",
        help="Counsel System - council of AIs plans together, then upgrades itself",
    )
    counsel_sub = counsel_parser.add_subparsers(dest="counsel_action")
    counsel_run = counsel_sub.add_parser("run", help="Convene the council for a task")
    counsel_run.add_argument("goal", help="The task/goal for the council")
    counsel_run.add_argument("--rounds", type=int, default=None, help="Override deliberation rounds")
    counsel_run.add_argument("--difficulty", type=int, default=None, choices=[1, 2, 3, 4, 5], help="Override difficulty 1-5")
    counsel_run.add_argument("--members", type=int, default=None, help="Override max members")
    counsel_run.add_argument("--no-execute", action="store_true", help="Plan only, skip tool execution")
    counsel_run.add_argument("--model", default=None, help="Base model for members (auto-diversified)")
    counsel_sub.add_parser("status", help="Council status: constitution version, roster, upgrades")
    counsel_amend = counsel_sub.add_parser("amend", help="Self-upgrade amendments (Meta-Counsel)")
    counsel_amend_sub = counsel_amend.add_subparsers(dest="counsel_amend_action")
    counsel_amend_sub.add_parser("list", help="List pending amendments + upgrade history")
    counsel_amend_diff = counsel_amend_sub.add_parser("diff", help="View unified diff of a pending amendment")
    counsel_amend_diff.add_argument("amendment_id")
    counsel_amend_approve = counsel_amend_sub.add_parser("approve", help="Approve a pending high-risk amendment")
    counsel_amend_approve.add_argument("amendment_id")
    counsel_amend_reject = counsel_amend_sub.add_parser("reject", help="Reject a pending amendment")
    counsel_amend_reject.add_argument("amendment_id")
    counsel_amend_rollback = counsel_amend_sub.add_parser("rollback", help="Roll back constitution to a previous version")
    counsel_amend_rollback.add_argument("version", type=int)
    counsel_review = counsel_sub.add_parser("review", help="Run Meta-Counsel review on the last council session")
    counsel_review.add_argument("--session-id", default=None, help="Specific session id (default: latest)")


def _run_counsel(args, ctx: CLIContext) -> None:
    import json as _json

    from core.counsel.constitution import constitution
    from core.counsel.council import CouncilSession
    from core.counsel.meta import meta_counsel

    if args.counsel_action == "run":
        result = CouncilSession(
            args.goal,
            model=args.model,
            difficulty=args.difficulty,
            max_members=args.members,
            max_rounds=args.rounds,
            execute=not args.no_execute,
        ).run()
        print(f"\n{'=' * 60}")
        print(f"⚖️ COUNSEL SESSION: {result['session_id']}")
        print(f"   Difficulty: {result['difficulty']} | Members: {', '.join(m['name'] for m in result['members'])}")
        votes = result.get("votes")
        if votes:
            print("   Votes:")
            for k, v in votes.items():
                print(f"     - {k}: {str(v)[:100]}")
        print(f"{'=' * 60}")
        if result.get("plan") and result["plan"].get("steps"):
            print("\n📋 VOTED PLAN:")
            for i, s in enumerate(result["plan"]["steps"], 1):
                print(f"   {i}. [{s.get('status', 'pending')}] {s.get('goal', '')}")
        print(f"\n✅ FINAL ANSWER:\n{result['final_answer']}")
        if result.get("errors"):
            print(f"\n⚠️  Council errors: {result['errors']}")
    elif args.counsel_action == "status":
        st = meta_counsel.status()
        print(f"Council status — constitution v{st['constitution']['version']} ({st['constitution']['name']})")
        print(f"  Members: {', '.join(st['constitution']['members'])}")
        rules = st.get("constitution", {}).get("rules", {})
        if rules:
            print("  Rules:")
            for k, v in rules.items():
                print(f"    - {k}: {str(v)[:120]}")
        budget = st.get("constitution", {}).get("budget", {})
        if budget:
            print(f"  Budget: max_members={budget.get('max_members')}, max_rounds={budget.get('max_rounds')}")
        print(f"  Pending amendments: {st['pending_amendments']}")
        print(f"  Meta reviews logged: {st['reviews_logged']}")
        print(f"  Upgrade events: {st['constitution']['upgrade_events']}")
        for ev in constitution.upgrade_log()[-5:]:
            print(
                f"    - {ev.get('event')} v{ev.get('new_version') or ev.get('version') or ev.get('to_version')} {ev.get('timestamp', '')[:19]}"
            )
    elif args.counsel_action == "amend":
        if args.counsel_amend_action == "list":
            pending = constitution.pending_amendments()
            print(f"Pending amendments ({len(pending)}):")
            for p in pending:
                print(f"  - [{p['id']}] target={p.get('target')} risk=high | {p.get('change', '')[:120]}")
                print(f"      reason: {p.get('reason', '')[:180]}")
            print("\nUpgrade history (last 10):")
            for ev in constitution.upgrade_log()[-10:]:
                print(
                    f"  - {ev.get('event')} | v{ev.get('new_version') or ev.get('version') or ev.get('to_version')} | {ev.get('reason', '')[:100]} | {ev.get('timestamp', '')[:19]}"
                )
            print("\nUse: hermus counsel amend approve <id> | reject <id> | diff <id> | rollback <version>")
        elif args.counsel_amend_action == "diff":
            res = constitution.diff(args.amendment_id)
            if res.get("success"):
                print(f"=== Unified Diff for Amendment {args.amendment_id} ===")
                print(res.get("diff") or "(no textual diff)")
            else:
                print(f"Diff error: {res.get('error')}")
        elif args.counsel_amend_action == "approve":
            res = constitution.approve(args.amendment_id)
            print(f"Approve result: {res}")
        elif args.counsel_amend_action == "reject":
            res = constitution.reject(args.amendment_id)
            print(f"Reject result: {res}")
        elif args.counsel_amend_action == "rollback":
            res = constitution.rollback(args.version)
            print(f"Rollback result: {res}")
        else:
            ctx.parser.parse_args(["counsel", "amend", "--help"])
    elif args.counsel_action == "review":
        session_id = args.session_id
        if not session_id:
            import glob

            files = sorted(glob.glob(str(config.resolve_path("data/counsel/sessions/*.json"))))
            if not files:
                print('No council sessions yet — run `hermus counsel run "task"` first')
                sys.exit(1)
            session_id = Path(files[-1]).stem

        summary = _json.loads(Path(config.resolve_path(f"data/counsel/sessions/{session_id}.json")).read_text())
        res = meta_counsel.review_session(summary)
        print(f"Meta-Counsel review of {session_id}: proposed {res['proposed']} amendment(s)")
        for r in res.get("results", []):
            print(f"  - {r}")
    else:
        no_action(ctx, "counsel")


def _configure_research(subparsers) -> None:
    research_parser = subparsers.add_parser("research", help="Web research - multi-source with citations")
    research_parser.add_argument("query")


def _run_research(args, ctx: CLIContext) -> None:
    from core.research import research_pipeline

    out = research_pipeline.run(args.query)
    print(f"\n=== RESEARCH: {args.query} ===\n{out['answer']}\n")
    print(f"Confidence: {out['confidence']}")
    print(f"Sources ({len(out['sources'])}):")
    for s in out["sources"]:
        print(f" - [{s['rank']}] {s['title']} ({s['url']})")
    if out.get("contradictions"):
        print(f"\n⚠️ Contradictions ({len(out['contradictions'])}):")
        for c in out["contradictions"]:
            print(f"   A: {c['a'][:90]} [{c['source_a']}]")
            print(f"   B: {c['b'][:90]} [{c['source_b']}]")
    if out.get("uncertain"):
        print(f"\nUncertain claims: {out['uncertain'][:3]}")


COMMANDS: tuple[Command, ...] = (
    Command(name="agent", help="Persistent background agents", configure=_configure_agent, run=_run_agent),
    Command(name="run", help="Autonomous task loop - plan/execute/verify/repair", configure=_configure_run, run=_run_run),
    Command(name="plan", help="Plans - DeepThink plan persistence & resume", configure=_configure_plan, run=_run_plan),
    Command(
        name="mission",
        help="Mission Engine — objective-driven lifecycle with verification",
        configure=_configure_mission,
        run=_run_mission,
    ),
    Command(
        name="delegate",
        help="Fan work out to parallel sub-agents (JSON-RPC workers)",
        configure=_configure_delegate,
        run=_run_delegate,
    ),
    Command(name="subagent", help="Subagents - parallel work", configure=_configure_subagent, run=_run_subagent),
    Command(
        name="computer",
        help="Autonomous computer agent - plan/act/record/verify/repair",
        configure=_configure_computer,
        run=_run_computer,
    ),
    Command(
        name="screen",
        help="Screen recording, visual timelines, verification and watching",
        configure=_configure_screen,
        run=_run_screen,
    ),
    Command(
        name="swe", help="Software Engineer Mode — full repo development & test loop", configure=_configure_swe, run=_run_swe
    ),
    Command(
        name="counsel",
        help="Counsel System - council of AIs plans together, then upgrades itself",
        configure=_configure_counsel,
        run=_run_counsel,
    ),
    Command(name="research", help="Web research - multi-source with citations", configure=_configure_research, run=_run_research),
)

__all__ = ["COMMANDS"]
