"""cron — Cron scheduler - natural language."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    cron_parser = subparsers.add_parser("cron", help="Cron scheduler - natural language")
    cron_sub = cron_parser.add_subparsers(dest="cron_action")
    cron_add = cron_sub.add_parser("add", help="Add cron job from natural language")
    cron_add.add_argument("text", help="Natural language schedule: 'daily at 9am send report'")
    cron_add.add_argument("--task", help="Task to execute (defaults to text)")
    cron_add.add_argument("--platform", default="cli")
    cron_add.add_argument("--user-id", default="default")
    cron_sub.add_parser("list", help="List cron jobs")
    cron_remove = cron_sub.add_parser("remove", help="Remove cron job")
    cron_remove.add_argument("job_id")


def run(args, ctx: CLIContext) -> None:
    from scheduler.cron import cron_manager

    if args.cron_action == "add":
        job = cron_manager.add_job(args.text, task=args.task, platform=args.platform, user_id=args.user_id)
        print(f"Cron job added: {job['id']} - {job['cron']} - {job['task']}")
    elif args.cron_action == "list":
        jobs = cron_manager.list_jobs()
        print(f"Cron jobs ({len(jobs)}):")
        for j in jobs:
            print(f" - {j['id']}: {j['cron']} - {j['natural']} -> {j['platform']}:{j['user_id']}")
    elif args.cron_action == "remove":
        ok = cron_manager.remove_job(args.job_id)
        print(f"Removed {args.job_id}: {ok}")
    else:
        ctx.parser.parse_args(["cron", "--help"])
