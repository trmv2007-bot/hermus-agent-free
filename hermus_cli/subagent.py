"""subagent — Subagents - parallel work."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    subagent_parser = subparsers.add_parser("subagent", help="Subagents - parallel work")
    subagent_parser.add_argument("action", choices=["spawn"], help="spawn")
    subagent_parser.add_argument("task", help="Task for subagent")


def run(args, ctx: CLIContext) -> None:
    if args.action == "spawn":
        from subagents.subagent import spawn_subagent

        result = spawn_subagent(args.task)
        print(f"Subagent result: {result}")
