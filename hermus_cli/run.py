"""run — Autonomous task loop - plan/execute/verify/repair."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    run_parser = subparsers.add_parser("run", help="Autonomous task loop - plan/execute/verify/repair")
    run_parser.add_argument("task", help="Goal to drive through the verify/repair loop")
    run_parser.add_argument("--model", default=None, help="Model to run with (default: config.model)")
    run_parser.add_argument("--max-repairs", type=int, default=2, help="Max diagnose/repair cycles")


def run(args, ctx: CLIContext) -> None:
    from core.agent import HermusAgent

    agent = HermusAgent(model=args.model)
    report = agent.autonomous(args.task, max_repairs=args.max_repairs)
    print(f"Autonomous run: status={report['status']} verified={report['verified']} repairs={report['repairs']}")
    for s in report["steps"]:
        print(f"  [{s['status']}] {s['goal'][:70]} (attempts={s['attempts']})")
    print(f"\nResult:\n{str(report['final_answer'])[:1500]}")
