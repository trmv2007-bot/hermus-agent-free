"""MCP management tools exposed to the agent."""
from typing import Dict, List

from core.mcp_client import mcp_manager


def mcp_list_servers() -> Dict:
    """List configured MCP servers and status."""
    servers = mcp_manager.list_servers()
    return {"servers": servers, "count": len(servers)}


def mcp_connect_all() -> Dict:
    """Connect to all enabled MCP servers and refresh tools."""
    result = mcp_manager.connect_enabled()
    # Reload tool registry so new MCP tools appear
    try:
        from core.tool_registry import tool_registry

        tool_registry.load(force=True)
        listed = tool_registry.list_tools()
        mcp_tools = [t for t in listed.get("tools", []) if t.startswith("mcp_")]
        result["registered_mcp_tools"] = mcp_tools
        result["registered_count"] = len(mcp_tools)
    except Exception as e:
        result["registry_error"] = str(e)
    return result


def mcp_call(server: str, tool: str, arguments: Dict = None) -> Dict:
    """Call a tool on an MCP server by server name + tool name."""
    return mcp_manager.call(server, tool, arguments or {})


def mcp_add_server(name: str, command: str, args: str = "", enabled: bool = True) -> Dict:
    """Add an MCP server. args is space-separated CLI args string."""
    arg_list = [a for a in (args or "").split(" ") if a]
    result = mcp_manager.add_server(name, command, args=arg_list, enabled=enabled)
    try:
        from core.tool_registry import tool_registry

        tool_registry.load(force=True)
    except Exception:
        pass
    return result


def mcp_remove_server(name: str) -> Dict:
    result = mcp_manager.remove_server(name)
    try:
        from core.tool_registry import tool_registry

        tool_registry.load(force=True)
    except Exception:
        pass
    return result


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "mcp_list_servers",
            "description": "List MCP (Model Context Protocol) servers configured for Hermus - free extensibility",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_connect_all",
            "description": "Connect all enabled MCP servers and register their tools on the agent tool bus",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_call",
            "description": "Call a tool on a specific MCP server (server name + tool name + arguments)",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["server", "tool"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_add_server",
            "description": "Add an MCP stdio server (command + args). Example: command=npx args='-y @modelcontextprotocol/server-filesystem /tmp'",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "command": {"type": "string"},
                    "args": {"type": "string", "description": "Space-separated args"},
                    "enabled": {"type": "boolean", "default": True},
                },
                "required": ["name", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_remove_server",
            "description": "Remove an MCP server by name",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
]

TOOL_MAP = {
    "mcp_list_servers": mcp_list_servers,
    "mcp_connect_all": mcp_connect_all,
    "mcp_call": mcp_call,
    "mcp_add_server": mcp_add_server,
    "mcp_remove_server": mcp_remove_server,
}
