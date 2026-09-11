"""computer — Autonomous computer agent - plan/act/record/verify/repair."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._common import add_computer_task_args

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
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


def run(args, ctx: CLIContext) -> None:
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
        ctx.parser.parse_args(["computer", "--help"])
