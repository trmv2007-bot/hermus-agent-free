"""mission — Mission Engine — objective-driven lifecycle with verification."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
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


def run(args, ctx: CLIContext) -> None:
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
        ctx.parser.parse_args(["mission", "--help"])
