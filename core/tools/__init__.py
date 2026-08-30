"""Canonical Tool subsystem (Rebuild spec §9).

One :class:`ToolGateway` is the only legal way the agent invokes tools. The legacy
:class:`core.tool_registry.ToolRegistry` and the connectors / MCP / computer / shell
adapters sit behind this boundary. There is no other invocation path.

Execution order (spec §9):
  resolve descriptor -> validate args -> attach trace -> permission/risk gate ->
  select backend -> execute (timeout/cancel) -> persist result + evidence ->
  emit outcome event -> run domain verifier -> feed typed outcome to retry/replan.
"""

from .gateway import ToolGateway, get_tool_gateway, tool_response

__all__ = ["ToolGateway", "get_tool_gateway", "tool_response"]
