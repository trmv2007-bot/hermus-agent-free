"""Who holds the gateway's port, and whether this start may take it.

When the port was already bound, ``hermus gateway start`` used to get as far as
uvicorn, fail to bind, and exit with its threads still alive and no listener — so a
launcher clicked twice, or a second start for a test, left a process nobody could
see or account for. The port is now tested before anything is imported or bound, so
a collision is one printed line and a non-zero exit.
"""

from __future__ import annotations

import errno
import http.client
import json
import os
import socket
import urllib.error
import urllib.request

#: How long a health probe waits for the gateway to answer before trying elsewhere.
PROBE_TIMEOUT_S = 1.0

#: How long a connect probe waits. A local answer or refusal is immediate.
CONNECT_TIMEOUT_S = 0.2

#: The address a start binds when nothing names one, as `gateway start` resolves it.
ANY_INTERFACE = "0.0.0.0"

#: "Someone already holds this port": POSIX EADDRINUSE and WIN32's WSAEADDRINUSE.
ADDRESS_IN_USE = {errno.EADDRINUSE, 10048}

#: "This account may not listen here": POSIX EACCES/EPERM and WIN32's WSAEACCES.
PERMISSION_DENIED = {errno.EACCES, errno.EPERM, 10013}

#: The addresses a probe asks, in order: this machine, whichever interface it bound.
LOOPBACK = ("127.0.0.1", "::1")


def _accepts(port: int) -> bool:
    """True when something on this machine is accepting connections on ``port``.

    Loopback and every interface address, so a listener bound only to a LAN address
    is found here rather than only in the OS table — psutil refuses that table to a
    normal user on macOS, and a gateway must not read as absent because of it.
    """
    for host in _probe_hosts():
        try:
            with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S):
                return True
        except OSError:
            continue
    return False


def _problem_from(exc: OSError) -> str:
    """Which of the three bind failures ``exc`` is, in a word the caller can print."""
    codes = {exc.errno, getattr(exc, "winerror", None)}
    if codes & ADDRESS_IN_USE:
        return "taken"
    if codes & PERMISSION_DENIED:
        return "denied"
    return "unavailable"


def _bind_problem(host: str, port: int) -> str | None:
    """None when this machine would grant the bind, else why it would not.

    ``taken`` is another socket holding it; ``denied`` is a port this account may not
    listen on; ``unavailable`` is an address or family this machine does not have —
    a stale ``HERMUS_GATEWAY_HOST`` after a DHCP change, or IPv6 switched off. None of
    the three is a free port, and each has its own sentence.
    """
    try:
        family, _, _, _, sockaddr = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
    except socket.gaierror:
        return "unavailable"
    try:
        probe = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return "unavailable"
    with probe:
        if os.name != "nt":
            # uvicorn sets this too; without it a socket in TIME_WAIT refuses a bind.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(sockaddr)
        except OSError as exc:
            return _problem_from(exc)
    return None


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


def port_in_use(port: int, host: str = ANY_INTERFACE) -> bool:
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
    # A bind this machine would refuse is not a free port: a start that proceeded
    # would meet uvicorn's own error instead of our one line.
    return _bind_problem(host, port) is not None


def require_free(port: int, host: str | None = None) -> None:
    """Refuse to start when this machine cannot take the port: one line, then exit 1."""
    host = host or os.environ.get("HERMUS_GATEWAY_HOST") or ANY_INTERFACE
    if not port_in_use(port, host):
        return
    pid = listener_pid(port)
    problem = _bind_problem(host, port)
    if pid is not None:
        print(f"Hermus gateway did not start: {host}:{port} is already in use by pid {pid}.", flush=True)
    elif problem == "denied":
        print(
            f"Hermus gateway did not start: listening on {host}:{port} needs a permission this account does not have.",
            flush=True,
        )
    elif problem == "unavailable":
        print(
            f"Hermus gateway did not start: this machine cannot listen on {host}:{port} (the address is not available here).",
            flush=True,
        )
    else:
        print(f"Hermus gateway did not start: {host}:{port} is already in use.", flush=True)
    raise SystemExit(1)


def _probe_hosts() -> list[str]:
    """Loopback, then this machine's own interface addresses.

    A gateway bound to a LAN address answers on that address and not on loopback, so
    status must ask where it might actually be listening instead of assuming.
    """
    hosts = list(LOOPBACK)
    try:
        import psutil
    except ImportError:
        return hosts
    try:
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family in (socket.AF_INET, socket.AF_INET6) and addr.address and "%" not in addr.address:
                    hosts.append(addr.address)
    except (OSError, psutil.Error):
        pass
    return list(dict.fromkeys(hosts))


def _health_url(host: str, port: int) -> str:
    return f"http://[{host}]:{port}/healthz" if ":" in host else f"http://{host}:{port}/healthz"


def live_gateway(port: int) -> dict | None:
    """The gateway's own liveness answer, or None when nothing answers on ``port``."""
    for host in _probe_hosts():
        try:
            with urllib.request.urlopen(_health_url(host, port), timeout=PROBE_TIMEOUT_S) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
            # Not HTTP at all is still an answer: it is not this gateway.
            continue
        if isinstance(body, dict) and body.get("status") == "ok":
            return body
    return None


def gateway_status(port: int) -> tuple[bool, str]:
    """``(running, one line)`` for ``hermus gateway status``."""
    health = live_gateway(port)
    if health is not None:
        return True, f"Hermus gateway: running on port {port} (pid {health.get('pid', 'unknown')})"
    pid = listener_pid(port)
    if pid is not None:
        return False, f"Hermus gateway: not answering on port {port}; a listener holds it (pid {pid})"
    if port_in_use(port):
        return False, f"Hermus gateway: not answering on port {port}; the port is in use by a process we cannot name"
    return False, f"Hermus gateway: not running on port {port}"
