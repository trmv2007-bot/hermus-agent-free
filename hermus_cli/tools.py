"""tools — List registered tools (auto registry)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    subparsers.add_parser("tools", help="List registered tools (auto registry)")

    # ---- architecture upgrades (foundation) ---------------------------------


def run(args, ctx: CLIContext) -> None:
    from core.tool_registry import tool_registry

    info = tool_registry.list_tools()
    print(f"Registered tools: {info['count']} defs={info['definitions']}")
    for t in info.get("tools", []):
        src = info.get("sources", {}).get(t, "")
        print(f" - {t}  [{src}]")
    if info.get("errors"):
        print("Load errors:")
        for e in info["errors"]:
            print(f"   ! {e}")
