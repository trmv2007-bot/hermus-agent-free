"""The process that keeps the room's gateway up when nobody is watching it.

The gateway is a foreground server: it serves until it dies, and the shell that
launched it is the shell it dies with. That is why a gateway started from a
terminal, or from an agent's session, leaves the room dark the moment that
session ends — and why it had to be restarted by hand.

Windows' own answer is a logon task, but a task whose action *is* the server can
only be restarted when that action process fails, which depends on how the
server happened to die. So the task runs this instead: one resident process that
owns the port, holds the gateway as its child, and starts it again after it
exits. There is never a second gateway: it leaves a port alone when a healthy
gateway already answers there, and it refuses to fight any other process that
holds it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from gateway import ports

#: The address the room answers on. Loopback, because a companion a person runs
#: for themselves is not a service other machines on the network may reach.
LOOPBACK = "127.0.0.1"

#: How often a keeper that is not running the gateway looks at the port again.
POLL_SECONDS = 20.0

#: How long to wait before restarting a gateway that has just exited.
RESTART_SECONDS = 3.0

#: The longest wait between restarts, so a gateway that fails on every start (a
#: missing dependency, say) is still retried across the day without spinning.
MAX_RESTART_SECONDS = 30.0

#: A gateway that stayed up at least this long was not failing to start, so the
#: next restart is prompt again instead of inheriting a grown backoff.
HEALTHY_RUN_SECONDS = 60.0


def gateway_command(port: int, host: str) -> list[str]:
    """The gateway this install starts, on the interpreter that owns its packages."""
    root = Path(__file__).resolve().parents[1]
    return [sys.executable, str(root / "hermus.py"), "gateway", "start", "--port", str(port), "--host", host]


def serve(
    port: int,
    host: str = LOOPBACK,
    *,
    stop: threading.Event | None = None,
    log: Path | None = None,
    poll_seconds: float = POLL_SECONDS,
    restart_seconds: float = RESTART_SECONDS,
    max_restart_seconds: float = MAX_RESTART_SECONDS,
    spawn: object = None,
) -> None:
    """Own the port: run the gateway, and run it again whenever it goes away.

    ``stop`` ends the loop between gateway runs; ``log`` keeps a line per start
    and exit so a room that went dark can say when and why; ``spawn`` is
    ``subprocess.Popen`` unless a caller supplies its own.
    """
    stop = stop or threading.Event()
    spawn = spawn or subprocess.Popen

    # A console that cannot encode a path or an exit code must degrade, not raise:
    # supervision that dies to its own output is not supervision. The log file is
    # UTF-8 and says what the console could not.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    def say(message: str) -> None:
        """One line per event, to the console where it is welcome and to the log."""
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
        try:
            print(line, flush=True)
        except (OSError, UnicodeEncodeError, ValueError):
            pass
        if log is not None:
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(line + os.linesep)
            except OSError:
                pass

    say(f"keeper: watching {host}:{port} - the gateway runs as {sys.executable}")
    wait = restart_seconds
    while not stop.is_set():
        if ports.live_gateway(port) is not None:
            stop.wait(poll_seconds)
            continue
        if ports.port_in_use(port, host):
            # Something that is not this gateway answers there — a launch of the
            # person's own, or a straggler still bound. Taking the port would be a
            # fight with a process we do not own, so wait for it to go instead.
            say(f"keeper: {host}:{port} is held by another process; leaving it alone")
            stop.wait(poll_seconds)
            continue
        started = time.time()
        try:
            child = spawn(gateway_command(port, host))
        except OSError as exc:
            child, code = None, f"could not start: {exc}"
        if child is not None:
            say(f"keeper: gateway started (pid {getattr(child, 'pid', 'unknown')})")
            code = child.wait()
        lived = time.time() - started
        if stop.is_set():
            break
        delay = restart_seconds if lived >= HEALTHY_RUN_SECONDS else wait
        wait = min(delay * 2, max_restart_seconds)
        say(f"keeper: gateway exited ({code}) after {lived:.0f}s; restarting in {delay:.0f}s")
        stop.wait(delay)
