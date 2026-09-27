"""Acting THROUGH a surface, not just arranging it.

`workspace_ops` moves panels around. This is the other half: the agent asks a
panel to do the thing the panel is for, and the result comes back into the room
so you watch it happen.

The important decision here is that every action names the real function it
calls. There is no generic "execute" and no dynamic dispatch by name — an action
is a literal in a table, and the table is the whole surface area. That is what
makes the difference between "the agent can use the terminal" and "the agent can
run anything", which are not the same claim.

Actions are chosen from what actually exists in this build:

    terminal.run     -> core.sandbox.sandbox.run     (the same shell the panel runs)
    memory.remember  -> core.memory.memory.remember  (the same store the panel writes)
    memory.recall    -> core.memory.memory.recall
    computer.frame   -> the live screen, read-only

An unknown action is refused by name, the same way a bad op is, because an agent
that cannot tell "not supported" from "malformed" will keep asking.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

ACTIONS: frozenset[str] = frozenset(
    {
        "terminal.run",
        "memory.remember",
        "memory.recall",
        "computer.frame",
    }
)

# Which surface kind each action needs to be open for the result to be visible.
ACTION_SURFACE: dict[str, str] = {
    "terminal.run": "terminal",
    "memory.remember": "memory",
    "memory.recall": "memory",
    "computer.frame": "computer",
}

MAX_TERMINAL_COMMAND = 2000
MAX_MEMORY_CONTENT = 8000
DEFAULT_MEMORY_KIND = "semantic"


class ActionRefusal(Exception):
    """An action was refused, with a reason a caller can act on."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _terminal_run(args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ActionRefusal("terminal.run needs a 'command'")
    if len(command) > MAX_TERMINAL_COMMAND:
        raise ActionRefusal(f"command longer than {MAX_TERMINAL_COMMAND} characters")

    from core.sandbox import sandbox

    # `sandbox.run` is synchronous and can take seconds. Calling it on the event
    # loop would freeze the whole gateway — every panel, every stream — for the
    # duration of one command, so it goes to a worker thread.
    result = await asyncio.to_thread(
        sandbox.run,
        command,
        timeout=int(args.get("timeout") or 0) or None,
        cwd=args.get("cwd"),
        policy=args.get("policy") or None,
        purpose="api:/workspace/act",
    )
    # The sandbox does not echo the command back — it is a runner, not a shell
    # that keeps history. Without this the terminal panel receives a result it
    # cannot attribute to anything, and the agent's work shows up as an orphaned
    # line of output.
    if isinstance(result, dict):
        result = {**result, "command": command}
    return result


def _memory_remember(args: dict[str, Any]) -> dict[str, Any]:
    content = str(args.get("content") or "").strip()
    if not content:
        raise ActionRefusal("memory.remember needs 'content'")
    if len(content) > MAX_MEMORY_CONTENT:
        raise ActionRefusal(f"content longer than {MAX_MEMORY_CONTENT} characters")

    from core.memory import memory

    return memory.remember(
        str(args.get("kind") or DEFAULT_MEMORY_KIND),
        content,
        importance=float(args.get("importance") or 5.0),
        success=args.get("success"),
    )


def _memory_recall(args: dict[str, Any]) -> dict[str, Any]:
    from core.memory import memory

    hits = memory.recall(
        str(args.get("query") or ""),
        limit=int(args.get("limit") or 8),
    )
    return {"hits": hits, "count": len(hits) if hasattr(hits, "__len__") else None}


def _computer_frame(args: dict[str, Any]) -> dict[str, Any]:
    """Read the live screen. Read-only by design.

    Looking at the screen and driving it are different capabilities with
    different risks. `/computer/run` is the deliberate way to drive it; this
    action only ever looks, so an agent reading the room cannot act on a
    stranger's desktop by accident.
    """
    from core.computer import service as computer_service

    getter = getattr(computer_service, "grab_frame", None) or getattr(computer_service, "latest_frame", None)
    if getter is None:
        raise ActionRefusal("this build has no frame grabber wired; use GET /computer/live-frame")
    frame = getter()
    return {"frame": frame}


def _async_wrap(fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Lift a plain handler into the async shape `HANDLERS` expects.

    Only the genuinely blocking one is async for its own reasons; the rest are
    ordinary functions and wrapping them keeps the table uniform instead of
    making every caller remember which is which.
    """

    async def wrapper(args: dict[str, Any]) -> dict[str, Any]:
        return fn(args)

    return wrapper


HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
    "terminal.run": _terminal_run,
    "memory.remember": _async_wrap(_memory_remember),
    "memory.recall": _async_wrap(_memory_recall),
    "computer.frame": _async_wrap(_computer_frame),
}


async def act(action: Any, args: Any = None) -> dict[str, Any]:
    """Run one action and publish the result so the target panel shows it.

    The result is published rather than only returned, because the point of the
    room is that you watch the agent work. A caller that gets a 200 and a JSON
    body while the panel stays blank has learned nothing about what happened.

    A handler that raises is reported as `ok: false` with the error, not raised
    to the caller: "the command failed" and "the request was malformed" are
    different answers, and only one of them is worth retrying.
    """
    if not isinstance(action, str) or action not in ACTIONS:
        raise ActionRefusal(f"unknown action {action!r}; expected one of {', '.join(sorted(ACTIONS))}")
    if args is not None and not isinstance(args, dict):
        raise ActionRefusal("'args' must be an object")

    from .dashboard_events import publish as publish_dashboard

    surface = ACTION_SURFACE[action]
    try:
        result = await HANDLERS[action](dict(args or {}))
        ok, error = True, None
    except ActionRefusal:
        raise
    except Exception as exc:  # the action's own backend failed, not the request
        ok, result, error = False, {}, f"{type(exc).__name__}: {exc}"

    payload = {"action": action, "surface": surface, "ok": ok, "result": result, "error": error}
    event = publish_dashboard("workspace_action", payload)
    return {"ok": ok, "action": action, "surface": surface, "result": result, "error": error, "event_id": str(event.get("id", ""))}


__all__ = ["ACTIONS", "ACTION_SURFACE", "ActionRefusal", "act"]
