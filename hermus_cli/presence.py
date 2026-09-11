"""presence — Hermus identity, live state, ongoing goals and heartbeat."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    presence_parser = subparsers.add_parser("presence", help="Hermus identity, live state, ongoing goals and heartbeat")
    presence_sub = presence_parser.add_subparsers(dest="presence_action")
    presence_sub.add_parser("status", help="Show identity, current state, goals and recent moments")
    presence_identity = presence_sub.add_parser("identity", help="Update Hermus identity")
    presence_identity.add_argument("--name", default=None)
    presence_identity.add_argument("--role", default=None)
    presence_identity.add_argument("--tone", default=None)
    presence_identity.add_argument("--values", default=None, help="Comma-separated values")
    presence_identity.add_argument("--greeting", default=None)
    presence_goal = presence_sub.add_parser("goal", help="Manage ongoing goals")
    presence_goal.add_argument("action", choices=["add", "list", "done"])
    presence_goal.add_argument("value", nargs="?", default="", help="Goal title for add, goal id for done")
    presence_goal.add_argument("--priority", type=int, default=3)
    presence_goal.add_argument("--note", default="")
    presence_sub.add_parser("heartbeat", help="Record one safe presence heartbeat")


def run(args, ctx: CLIContext) -> None:
    import json as _json

    from core.presence import get_presence

    pm = get_presence()
    if args.presence_action in (None, "status"):
        print(_json.dumps(pm.snapshot(), indent=2, default=str))
    elif args.presence_action == "identity":
        values = None
        if args.values is not None:
            values = [item.strip() for item in args.values.split(",") if item.strip()]
        identity = pm.update_identity(
            name=args.name,
            role=args.role,
            tone=args.tone,
            values=values,
            greeting=args.greeting,
        )
        print(_json.dumps({"success": True, "identity": identity}, indent=2))
    elif args.presence_action == "goal":
        if args.action == "add":
            result = pm.add_goal(args.value, priority=args.priority, notes=args.note)
        elif args.action == "done":
            result = pm.complete_goal(args.value, note=args.note)
        else:
            result = {"goals": pm.list_goals()}
        print(_json.dumps(result, indent=2, default=str))
    elif args.presence_action == "heartbeat":
        print(_json.dumps(pm.heartbeat(force_event=True), indent=2, default=str))
