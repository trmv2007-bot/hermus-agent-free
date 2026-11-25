"""``hermus`` on its own: the one command that gets a person into the product.

``bin/hermus`` installs when the runtime is missing, so by the time this runs the
install half is already done. What is left is the other half of the promise: open
a gateway if one is not answering, then put the room in front of them. Nothing
here downloads, asks a question, or changes a setting.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from core.config import config

if TYPE_CHECKING:
    from ._common import CLIContext

ROOT = Path(__file__).resolve().parents[1]

#: How long a cold gateway may take before the CLI stops promising and says so.
READY_TIMEOUT_S = 180.0

#: The companion launch listens on this machine only. `hermus gateway start` keeps
#: binding every interface for the people who want webhooks or a LAN fleet, but a
#: listener nobody asked for is not something the default command should open to the
#: network, and it must not outlive the terminal without that being the safe case.
LOOPBACK = "127.0.0.1"


def room_url(port: int) -> str:
    return f"http://{LOOPBACK}:{port}/"


def room_link(port: int) -> str:
    """The address to open: the root, with the gateway token when one is configured.

    The root is the address that decides where a person belongs — the welcome flow
    while nothing can answer, the room once something can — and it keeps its query
    through the redirect, so the token still arrives. The token is percent-encoded:
    a key may hold `+`, `&` or `#`, and the browser's query parser would read those
    as a space, a separator or a fragment and send a value that cannot match.
    """
    token = os.environ.get("HERMUS_GATEWAY_TOKEN") or ""
    return room_url(port) + (f"?token={quote(token, safe='')}" if token else "")


def answering(port: int, timeout: float = 1.5) -> bool:
    """True when something on this port is really a Hermus gateway.

    ``/readyz`` is the same answer the boot line prints, so a stale process or a
    different server on the port is not mistaken for the product.
    """
    try:
        with urllib.request.urlopen(room_url(port) + "readyz", timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def spawn_gateway(port: int) -> subprocess.Popen:
    """Start the gateway on its own, so it outlives the terminal that asked."""
    command = [
        sys.executable,
        str(ROOT / "hermus.py"),
        "gateway",
        "start",
        "--port",
        str(port),
        "--host",
        LOOPBACK,
    ]
    kwargs: dict = {"cwd": str(ROOT), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def wait_until_ready(port: int, timeout: float = READY_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if answering(port):
            return True
        time.sleep(0.5)
    return False


def run(args, ctx: CLIContext) -> None:
    port = int(os.environ.get("HERMUS_GATEWAY_PORT") or config.gateway_port)
    url = room_url(port)
    link = room_link(port)

    if answering(port):
        print(f"Hermus is already running at {url}")
    else:
        print("Starting Hermus...")
        spawn_gateway(port)
        if not wait_until_ready(port):
            print(f"Hermus did not come up at {url}. Run it in a terminal to see why: hermus gateway start --port {port}")
            raise SystemExit(1)
        print(f"Hermus is running at {url}")

    print(f"The room is open in your browser, listening on {LOOPBACK} only.")
    print("Close this window whenever you like; Hermus keeps running.")
    try:
        webbrowser.open(link)
    except Exception:  # noqa: BLE001 - a headless machine has no browser to open
        print(f"Open {link} yourself.")
