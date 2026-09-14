"""safety — Autonomy safety reports and audit exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    safety_parser = subparsers.add_parser("safety", help="Autonomy safety reports and audit exports")
    safety_sub = safety_parser.add_subparsers(dest="safety_action")
    safety_report_p = safety_sub.add_parser("report", help="Generate an Autonomy Safety Report")
    safety_report_p.add_argument("--write", action="store_true", help="Write docs/safety_reports/autonomy-safety-report-*.md")
    safety_report_p.add_argument("--output", help="Write report to this markdown path")
    safety_report_p.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    safety_preflight_p = safety_sub.add_parser("preflight", help="Check a mission/action before starting it")
    safety_preflight_p.add_argument("goal", nargs="+", help="Goal/action to pre-flight")
    safety_preflight_p.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    safety_preflight_p.add_argument(
        "--create-approval-prompts", action="store_true", help="Create draft pending approval prompts suggested by pre-flight"
    )
    safety_scan_p = safety_sub.add_parser("scan-folder", help="Run read-only local defensive folder scanner")
    safety_scan_p.add_argument("path")
    safety_scan_p.add_argument("--max-files", type=int, default=500)
    safety_scan_p.add_argument("--save-report", action="store_true", help="Write a Markdown scan report artifact")
    safety_scan_p.add_argument("--mission-id", default="", help="Attach saved scan report to a mission")
    safety_scan_p.add_argument("--json", action="store_true")
    safety_scan_mission_p = safety_sub.add_parser("scan-mission", help="Create a gated local folder scan mission")
    safety_scan_mission_p.add_argument("path")
    safety_scan_mission_p.add_argument("--purpose", default="malware")
    safety_scan_mission_p.add_argument("--max-files", type=int, default=500)
    safety_scan_mission_run_p = safety_sub.add_parser("scan-mission-run", help="Run an approved local folder scan mission")
    safety_scan_mission_run_p.add_argument("mission_id")


def run(args, ctx: CLIContext) -> None:
    import json as _json
    from pathlib import Path as _Path

    from core.safety_report import generate_safety_report, write_safety_report

    if args.safety_action == "report":
        report = generate_safety_report()
        if args.write or args.output:
            result = write_safety_report(report, output=_Path(args.output) if args.output else None)
            print(f"✅ safety report written: {result['path']}")
        elif args.json:
            print(_json.dumps(report.to_dict(), indent=2, default=str))
        else:
            print(report.to_markdown())
    elif args.safety_action == "preflight":
        from core.autonomy_preflight import create_preflight_approval_requests, preflight_goal

        goal = " ".join(args.goal)
        report = preflight_goal(goal)
        if args.json:
            print(_json.dumps(report.to_dict(), indent=2, default=str))
        else:
            print(report.to_markdown())
        if args.create_approval_prompts:
            prompts = create_preflight_approval_requests(goal)
            print("\n--- draft approval prompts ---")
            print(
                _json.dumps(
                    {"success": prompts.get("success"), "created": prompts.get("created"), "errors": prompts.get("errors")},
                    indent=2,
                    default=str,
                )
            )
    elif args.safety_action == "scan-folder":
        try:
            from core.permissions import Decision, permission_manager

            check = permission_manager.check(
                "local_folder_defensive_scan",
                args={
                    "path": args.path,
                    "max_files": args.max_files,
                    "purpose": "defensive_scan",
                    "save_report": args.save_report,
                    "mission_id": args.mission_id,
                },
            )
            if check.get("decision") != Decision.ALLOW.value:
                print(_json.dumps({"success": False, "error": "approval required", "permission": check}, indent=2, default=str))
                return
        except Exception as exc:
            print(_json.dumps({"success": False, "error": f"permission check failed closed: {exc}"}, indent=2, default=str))
            return
        from core.local_defense_scanner import scan_folder

        result = scan_folder(args.path, max_files=args.max_files, save_report=args.save_report, mission_id=args.mission_id)
        if args.json:
            print(_json.dumps(result, indent=2, default=str))
        else:
            print(result.get("markdown") or _json.dumps(result, indent=2, default=str))
    elif args.safety_action == "scan-mission":
        from core.local_defense_workflow import start_local_scan_mission

        report = start_local_scan_mission(args.path, purpose=args.purpose, max_files=args.max_files)
        print(_json.dumps(report.to_dict(), indent=2, default=str))
    elif args.safety_action == "scan-mission-run":
        from core.local_defense_workflow import run_local_scan_mission

        try:
            print(_json.dumps(run_local_scan_mission(args.mission_id).to_dict(), indent=2, default=str))
        except ValueError as exc:
            print(f"Error: {exc}")
    else:
        ctx.parser.parse_args(["safety", "--help"])
