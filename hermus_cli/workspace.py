"""workspace — Workspace - per-project isolation (agent OS)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    ws_parser = subparsers.add_parser("workspace", help="Workspace - per-project isolation (agent OS)")
    ws_sub = ws_parser.add_subparsers(dest="ws_action")
    ws_sub.add_parser("layout", help="Show workspace layout + paths")
    ws_create = ws_sub.add_parser("create", help="Create a project")
    ws_create.add_argument("name")
    ws_create.add_argument("--description", default="")
    ws_sub.add_parser("list", help="List projects")
    ws_use = ws_sub.add_parser("use", help="Set the current project")
    ws_use.add_argument("name")


def run(args, ctx: CLIContext) -> None:
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
        ctx.parser.parse_args(["workspace", "--help"])
