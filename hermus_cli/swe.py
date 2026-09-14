"""swe — Software Engineer Mode — full repo development & test loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    swe_parser = subparsers.add_parser("swe", help="Software Engineer Mode — full repo development & test loop")
    swe_sub = swe_parser.add_subparsers(dest="swe_action")
    swe_run = swe_sub.add_parser("run", help="Execute an engineering task")
    swe_run.add_argument("task", help="Task description")
    swe_run.add_argument("--repairs", type=int, default=3, help="Max repair rounds")


def run(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.swe_mode import swe_mode

    if args.swe_action == "run":
        res = swe_mode.execute(task=args.task, max_repairs=args.repairs)
        print(json_lib.dumps(res.to_dict(), indent=2))
    else:
        ctx.parser.parse_args(["swe", "--help"])
