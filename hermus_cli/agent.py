"""agent — Persistent background agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
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


def run(args, ctx: CLIContext) -> None:
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
        ctx.parser.parse_args(["agent", "--help"])
