"""skill — Skills."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    skill_parser = subparsers.add_parser("skill", help="Skills")
    skill_parser.add_argument("action", choices=["list", "improve"], help="list or improve")
    skill_parser.add_argument("--name", help="Skill name for improve")


def run(args, ctx: CLIContext) -> None:
    from core.skill_manager import skill_manager

    if args.action == "list":
        skills = skill_manager.list_skills()
        print(f"Skills ({len(skills)}):")
        for s in skills:
            print(f" - {s['name']}: {s['description'][:100]}")
    elif args.action == "improve":
        if not args.name:
            print("Need --name for improve")
        else:
            result = skill_manager.improve_skill(args.name)
            print(f"Improve result: {result}")
