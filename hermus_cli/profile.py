"""profile — Agent profiles - personas with independent memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    profile_parser = subparsers.add_parser("profile", help="Agent profiles - personas with independent memory")
    profile_sub = profile_parser.add_subparsers(dest="profile_action")
    profile_create = profile_sub.add_parser("create", help="Create a profile")
    profile_create.add_argument("name")
    profile_create.add_argument("--persona", default=None)
    profile_sub.add_parser("list", help="List profiles")
    profile_use = profile_sub.add_parser("use", help="Show a profile's system prompt")
    profile_use.add_argument("name")


def run(args, ctx: CLIContext) -> None:
    from core.profiles import profile_manager

    if args.profile_action == "create":
        r = profile_manager.create(args.name, persona=args.persona)
        print(f"{'✅' if r.get('success') else '❌'} {r.get('name') or r.get('error')}")
        if r.get("success"):
            print(f"   persona: {r['persona']}")
    elif args.profile_action == "list":
        profiles = profile_manager.list()
        if not profiles:
            print("No profiles. Create one: hermus profile create coder")
        for p in profiles:
            print(f" - {p.get('name')} | {p.get('persona', '')[:70]}")
    elif args.profile_action == "use":
        print(profile_manager.system_prompt(args.name))
    else:
        ctx.parser.parse_args(["profile", "--help"])
