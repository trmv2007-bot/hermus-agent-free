"""Who holds the gateway's port, and whether this start may take it.

When the port was already bound, ``hermus gateway start`` used to get as far as
uvicorn, fail to bind, and exit with its threads still alive and no listener — so a
launcher clicked twice, or a second start for a test, left a process nobody could
see or account for. The port is now tested before anything is imported or bound, so
a collision is one printed line and a non-zero exit.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

#: How long a status probe waits for the gateway to answer before calling it absent.
PROBE_TIMEOUT_S = 2.0

#: How long a connect probe waits. Loopback answers or refuses at once.
CONNECT_TIMEOUT_S = 0.5

#: The address a start binds when nothing names one, as `gateway start` resolves it.
ANY_INTERFACE = "0.0.0.0"


def _accepts(port: int) -> bool:
    """True when something on loopback is accepting connections on ``port``."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT_S):
            return True
    except OSError:
        return False


def _binds(host: str, port: int) -> bool:
    """Would a listener on ``host:port`` get its socket, with uvicorn's options?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name != "nt":
            # uvicorn sets this too; without it a socket in TIME_WAIT refuses a bind.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def listener_pid(port: int) -> int | None:
    """The process listening on ``port``, when the OS will name it."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and getattr(conn.laddr, "port", None) == port and conn.pid:
                return int(conn.pid)
    except (OSError, psutil.Error):
        return None
    return None


def port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    """Is something already serving or holding this port?

    Three questions, because one is not enough: a connect catches a gateway serving
    somewhere this check can reach, the listening table catches one bound anywhere
    at all, and the bind answers for a holder that refuses connections. Windows in
    particular lets a socket bind the wildcard address while another already holds a
    loopback one, so a bind probe alone would call a busy port free.
    """
    if _accepts(port):
        return True
    if listener_pid(port) is not None:
        return True
    return not _binds(host, port)


def require_free(port: int, host: str | None = None) -> None:
    """Refuse to start when someone else holds the port: one line, then exit 1."""
    host = host or os.environ.get("HERMUS_GATEWAY_HOST") or ANY_INTERFACE
    if not port_in_use(port, host):
        return
    pid = listener_pid(port)
    owner = f" by pid {pid}" if pid else ""
    print(f"Hermus gateway did not start: {host}:{port} is already in use{owner}.", flush=True)
    raise SystemExit(1)


def live_gateway(port: int) -> dict | None:
    """The gateway's own liveness answer, or None when nothing answers on ``port``."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=PROBE_TIMEOUT_S) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return body if isinstance(body, dict) and body.get("status") == "ok" else None


def gateway_status(port: int) -> tuple[bool, str]:
    """``(running, one line)`` for ``hermus gateway status``."""
    health = live_gateway(port)
    if health is not None:
        return True, f"Hermus gateway: running on port {port} (pid {health.get('pid', 'unknown')})"
    pid = listener_pid(port)
    if pid is not None:
        return False, f"Hermus gateway: not running on port {port}; another process holds it (pid {pid})"
    if port_in_use(port):
        return False, f"Hermus gateway: not running on port {port}; the port is in use by a process we cannot name"
    return False, f"Hermus gateway: not running on port {port}"
