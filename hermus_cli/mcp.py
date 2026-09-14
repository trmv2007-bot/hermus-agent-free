"""mcp — MCP servers - connect external tool servers (stdio)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    mcp_parser = subparsers.add_parser("mcp", help="MCP servers - connect external tool servers (stdio)")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")
    mcp_sub.add_parser("list", help="List MCP servers")
    mcp_add = mcp_sub.add_parser("add", help="Add MCP server")
    mcp_add.add_argument("--name", required=True)
    mcp_add.add_argument("--command", required=True, help="Executable e.g. npx or python3")
    mcp_add.add_argument("--arg", action="append", default=[], help="Repeatable arg")
    mcp_add.add_argument("--disabled", action="store_true", help="Add but leave disabled")
    mcp_remove = mcp_sub.add_parser("remove", help="Remove MCP server")
    mcp_remove.add_argument("name")
    mcp_sub.add_parser("connect", help="Connect enabled MCP servers and register tools")
    mcp_call = mcp_sub.add_parser("call", help="Call MCP tool")
    mcp_call.add_argument("--server", required=True)
    mcp_call.add_argument("--tool", required=True)
    mcp_call.add_argument("--args", default="{}", help="JSON arguments")


def run(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.mcp_client import mcp_manager

    if args.mcp_action == "list":
        servers = mcp_manager.list_servers()
        print(f"MCP servers ({len(servers)}):")
        for s in servers:
            print(
                f" - {s.get('name')}: cmd={s.get('command')} enabled={s.get('enabled')} running={s.get('running')} tools={s.get('tool_count')}"
            )
            if s.get("last_error"):
                print(f"   error: {s['last_error']}")
    elif args.mcp_action == "add":
        result = mcp_manager.add_server(
            args.name,
            args.command,
            args=args.arg or [],
            enabled=not args.disabled,
        )
        print(result)
        print("Tip: hermus mcp connect  # register tools on agent")
    elif args.mcp_action == "remove":
        print(mcp_manager.remove_server(args.name))
    elif args.mcp_action == "connect":
        result = mcp_manager.connect_enabled()
        from core.tool_registry import tool_registry

        tool_registry.load(force=True)
        info = tool_registry.list_tools()
        mcp_tools = [t for t in info.get("tools", []) if t.startswith("mcp_")]
        print(result)
        print(f"Registered MCP tools ({len(mcp_tools)}): {mcp_tools}")
    elif args.mcp_action == "call":
        try:
            call_args = json_lib.loads(args.args)
        except Exception:
            call_args = {}
        result = mcp_manager.call(args.server, args.tool, call_args)
        print(f"MCP call {args.server}/{args.tool} {call_args}:")
        if isinstance(result, dict):
            if result.get("error"):
                print(f"❌ {result['error']}")
            else:
                for k, v in result.items():
                    print(f"   {k}: {str(v)[:300]}")
        else:
            print(str(result)[:2000])
    else:
        ctx.parser.parse_args(["mcp", "--help"])
