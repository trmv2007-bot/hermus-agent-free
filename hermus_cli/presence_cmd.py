"""``hermus presence …`` — the floating character's command surface.

Kept in its own module so the presence overlay can grow without touching the
grouped CLI: :func:`configure_overlay_subcommands` adds the sub-commands to the
existing ``presence`` parser, and :func:`run_overlay` handles them, returning
``False`` for anything owned by the identity/goal commands next door.

Everything here is optional-software friendly. No shell installed, no gateway
running, no Node.js — each case produces one sentence saying exactly what to do
and never a traceback.
"""

from __future__ import annotations

import argparse
import json
import os
import time

from core.config import config
from core.log import get_logger

logger = get_logger(__name__)

OVERLAY_ACTIONS = ("start", "stop", "install", "shell", "demo", "point", "say", "overlay", "hide", "show")


def configure_overlay_subcommands(presence_sub) -> None:
    """Add the overlay sub-commands to the existing ``presence`` parser."""
    start = presence_sub.add_parser("start", help="Show the floating character on your desktop (native shell)")
    start.add_argument("--url", default=None, help="Gateway base URL (default: local gateway)")
    start.add_argument("--gateway", choices=["auto", "spawn", "none"], default="auto", help="Start a gateway if none is running")
    start.add_argument("--state", default="idle", help="Initial character state")
    start.add_argument("--install", action="store_true", help="Install the Electron runtime if it is missing")

    stop = presence_sub.add_parser("stop", help="Close the floating character")
    stop.add_argument("--keep-gateway", action="store_true", help="Leave a gateway this command started running")

    presence_sub.add_parser("install", help="Install the Electron runtime under data/presence_shell")
    presence_sub.add_parser("shell", help="Report native-shell availability and how it would run here")
    presence_sub.add_parser("hide", help="Put the character away without closing the shell")
    presence_sub.add_parser("show", help="Bring the character back")
    presence_sub.add_parser("overlay", help="Print the overlay state as JSON")
    presence_sub.add_parser("demo", help="Point at three screen regions and speak — the documented demo")

    point = presence_sub.add_parser("point", help="Point at a screen rect")
    point.add_argument("x", type=float)
    point.add_argument("y", type=float)
    point.add_argument("w", type=float)
    point.add_argument("h", type=float)
    point.add_argument("--label", default="")
    point.add_argument("--ttl-ms", type=int, default=0)
    point.add_argument("--space", choices=["physical", "dip"], default="physical")

    say = presence_sub.add_parser("say", help="Show a subtitle (two lines max)")
    say.add_argument("text", nargs="+")
    say.add_argument("--ttl-ms", type=int, default=0)


def _base_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env = os.getenv("HERMUS_PRESENCE_URL") or os.getenv("HERMUS_GATEWAY_URL")
    if env:
        return env.rstrip("/")
    return f"http://127.0.0.1:{config.gateway_port}"


def _client(url: str | None = None):
    from presence_app.client import PresenceClient

    token = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    return PresenceClient(_base_url(url), token=token)


def _wait_for_gateway(client, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.reachable():
            return True
        time.sleep(0.35)
    return False


def _cmd_start(args) -> int:
    from presence_app import shell

    client = _client(getattr(args, "url", None))
    if not client.reachable():
        if getattr(args, "gateway", "auto") == "none":
            print(f"No gateway at {client.base_url}. Start it with: hermus gateway start")
            return 1
        print(f"No gateway at {client.base_url}; starting one…")
        spawned = shell.spawn_gateway(config.gateway_port)
        if not spawned.get("ok"):
            print(f"Could not start the gateway: {spawned.get('error')}")
            return 1
        if not _wait_for_gateway(client):
            shell.stop_gateway()
            print("The gateway did not answer in 20s. Run `hermus gateway start` in a terminal to see why.")
            return 1

    status = shell.shell_status()
    if not status.electron and getattr(args, "install", False):
        print(f"Installing the Electron runtime ({shell.ELECTRON_VERSION}) under data/presence_shell …")
        installed = shell.install_runtime()
        if not installed.get("ok"):
            print(f"Install failed: {installed.get('error')}")
            return 1
        status = shell.shell_status()

    if not status.electron:
        print(status.reason or "The native shell is unavailable on this machine.")
        print("Install it with: hermus presence install")
        print(f"Until then the renderer still works in a browser: {client.base_url}/presence/overlay")
        return 1

    from presence_app.client import page_url

    token = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    launched = shell.launch_shell(page_url(client.base_url, token), token=token)
    if not launched.get("ok"):
        print(f"Could not launch the shell: {launched.get('error')}")
        return 1
    client.set_state(getattr(args, "state", "idle"), rationale="hermus presence start")
    print(f"Presence shell running (pid {launched['pid']}). It draws nothing until something asks it to.")
    print(f"Renderer: {client.base_url}/presence/overlay")
    print("Try it: hermus presence demo    ·    Stop it: hermus presence stop")
    return 0


def _cmd_stop(args) -> int:
    from presence_app import shell

    client = _client()
    if client.reachable():
        client.hide()
    stopped = shell.stop_shell()
    if stopped.get("stopped"):
        print(f"Presence shell stopped (pid {stopped['pid']}).")
    else:
        print(f"Presence shell: {stopped.get('detail') or 'not running'}.")
    if not getattr(args, "keep_gateway", False):
        gateway = shell.stop_gateway()
        if gateway.get("stopped"):
            print(f"Gateway that presence started has been stopped (pid {gateway['pid']}).")
    return 0


def _cmd_install(_args) -> int:
    from presence_app import shell

    installed = shell.install_runtime()
    if installed.get("ok"):
        print(f"Electron runtime ready: {installed.get('electron') or installed.get('reason', '')}")
        return 0
    print(f"Install failed: {installed.get('error')}")
    return 1


def _cmd_shell(_args) -> int:
    from presence_app import shell

    status = shell.shell_status()
    print(json.dumps(status.as_dict(), indent=2))
    if not status.electron:
        print(f"\nNext: {status.reason or 'install the runtime'}")
    return 0 if status.electron else 1


def _cmd_demo(_args) -> int:
    from presence_app.client import PresenceUnavailableError
    from presence_app.demo import run_demo

    client = _client()
    if not client.reachable():
        print(f"No gateway at {client.base_url}. Start it with: hermus gateway start")
        return 1
    try:
        sent = run_demo(client)
    except PresenceUnavailableError as exc:
        print(str(exc))
        return 1
    for step in sent:
        print(f"pointed at {step['label']:<20} {step['w']:.0f}×{step['h']:.0f} at ({step['x']:.0f}, {step['y']:.0f})")
    print("Demo finished.")
    return 0


def _cmd_point(args) -> int:
    client = _client()
    result = client.point(args.x, args.y, args.w, args.h, label=args.label, ttl_ms=args.ttl_ms or None, space=args.space)
    if not result.get("ok"):
        print(f"Refused: {result.get('error') or result}")
        return 1
    print(json.dumps(result.get("point"), indent=2))
    return 0


def _cmd_say(args) -> int:
    client = _client()
    result = client.say(" ".join(args.text), ttl_ms=args.ttl_ms or None)
    if not result.get("ok"):
        print(f"Refused: {result.get('error') or result}")
        return 1
    print(result["say"]["text"])
    return 0


def _cmd_overlay(_args) -> int:
    client = _client()
    print(json.dumps(client.snapshot(), indent=2))
    return 0


def _cmd_hide(_args) -> int:
    print(json.dumps(_client().hide().get("visible")))
    return 0


def _cmd_show(_args) -> int:
    print(json.dumps(_client().show().get("visible")))
    return 0


_HANDLERS = {
    "start": _cmd_start,
    "stop": _cmd_stop,
    "install": _cmd_install,
    "shell": _cmd_shell,
    "demo": _cmd_demo,
    "point": _cmd_point,
    "say": _cmd_say,
    "overlay": _cmd_overlay,
    "hide": _cmd_hide,
    "show": _cmd_show,
}


def run_overlay(args: argparse.Namespace) -> bool:
    """Run an overlay sub-command. Returns False when the action is not ours."""
    action = getattr(args, "presence_action", None)
    handler = _HANDLERS.get(action)
    if handler is None:
        return False
    raise SystemExit(handler(args))


__all__ = ["OVERLAY_ACTIONS", "configure_overlay_subcommands", "run_overlay"]
