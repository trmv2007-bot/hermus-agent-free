"""workspace commands — the Hermus CLI's workspace group.

Part of the grouped CLI: one module per capability group instead of one
module per command. Each command keeps its own ``configure``/``run`` pair,
so the command bodies are unchanged while the import shim, docstring and
``TYPE_CHECKING`` block that every one of the forty-three files carried are
gone.
"""

from __future__ import annotations

from ._common import CLIContext
from ._spec import Command, no_action


def _configure_workspace(subparsers) -> None:
    ws_parser = subparsers.add_parser("workspace", help="Workspace - per-project isolation (agent OS)")
    ws_sub = ws_parser.add_subparsers(dest="ws_action")
    ws_sub.add_parser("layout", help="Show workspace layout + paths")
    ws_create = ws_sub.add_parser("create", help="Create a project")
    ws_create.add_argument("name")
    ws_create.add_argument("--description", default="")
    ws_sub.add_parser("list", help="List projects")
    ws_use = ws_sub.add_parser("use", help="Set the current project")
    ws_use.add_argument("name")


def _run_workspace(args, ctx: CLIContext) -> None:
    from core.workspace import workspace as ws

    if args.ws_action == "layout":
        print(f"Workspace base: {ws.base_dir}")
        for name, p in ws.dirs.items():
            print(f"  {name:12s} {p}")
    elif args.ws_action == "create":
        r = ws.create_project(args.name, description=args.description)
        print(f"{'✅' if r.get('success') else '❌'} {r.get('name') or r.get('error')} -> {r.get('path', '')}")
    elif args.ws_action == "list":
        projects = ws.list_projects()
        print(f"Projects ({len(projects)}):")
        for p in projects:
            cur = " *" if p.get("name") == ws.current_project() else ""
            print(f" - {p.get('name')}{cur} | {p.get('description', '')}")
    elif args.ws_action == "use":
        r = ws.set_current_project(args.name)
        print(f"{'✅' if r.get('success') else '❌'} current project: {r.get('name') or r.get('error')}")
    else:
        no_action(ctx, "workspace")


def _configure_profile(subparsers) -> None:
    profile_parser = subparsers.add_parser("profile", help="Agent profiles - personas with independent memory")
    profile_sub = profile_parser.add_subparsers(dest="profile_action")
    profile_create = profile_sub.add_parser("create", help="Create a profile")
    profile_create.add_argument("name")
    profile_create.add_argument("--persona", default=None)
    profile_sub.add_parser("list", help="List profiles")
    profile_use = profile_sub.add_parser("use", help="Show a profile's system prompt")
    profile_use.add_argument("name")


def _run_profile(args, ctx: CLIContext) -> None:
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
        no_action(ctx, "profile")


def _configure_presence(subparsers) -> None:
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


def _run_presence(args, ctx: CLIContext) -> None:
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


COMMANDS: tuple[Command, ...] = (
    Command(
        name="workspace", help="Workspace - per-project isolation (agent OS)", configure=_configure_workspace, run=_run_workspace
    ),
    Command(
        name="profile", help="Agent profiles - personas with independent memory", configure=_configure_profile, run=_run_profile
    ),
    Command(
        name="presence",
        help="Hermus identity, live state, ongoing goals and heartbeat",
        configure=_configure_presence,
        run=_run_presence,
    ),
)

__all__ = ["COMMANDS"]
