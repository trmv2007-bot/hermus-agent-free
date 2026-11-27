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
holds it. The one process it will replace is a gateway of this install that has
stopped answering — its own orphan, holding the port for a room nobody can see.
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

#: How long a gateway of this install may hold the port without answering before
#: the keeper replaces it. A cold boot imports the whole app and takes tens of
#: seconds, so a short silence is not a wedge; minutes of it is a dead room.
WEDGED_SECONDS = 60.0

#: What a refused bind means, in the one line the keeper writes for it. None of
#: these is another process holding the port, and saying so sent a person looking
#: for a holder that did not exist.
BIND_REFUSALS = {
    "taken": "another socket is bound there",
    "reserved": "Windows reserves this port for itself",
    "denied": "this account may not listen there",
    "unavailable": "this machine does not have that address",
}


def gateway_command(port: int, host: str) -> list[str]:
    """The gateway this install starts, on the interpreter that owns its packages."""
    root = Path(__file__).resolve().parents[1]
    return [sys.executable, str(root / "hermus.py"), "gateway", "start", "--port", str(port), "--host", host]


def own_gateway(held: dict) -> bool:
    """Is the holder a gateway started from this install's own ``hermus.py``?

    The keeper's child can outlive its supervision — a frozen gateway keeps its
    socket bound while answering nothing — and that orphan is the keeper's to
    replace. Another program on the port, or a gateway from a different checkout, is
    never touched. A launch by a relative path reads as foreign: the safe way.
    """
    script = os.path.normcase(str(Path(__file__).resolve().parents[1] / "hermus.py"))
    argv = held.get("cmdline") or []
    return "gateway" in argv and any(os.path.normcase(arg) == script for arg in argv)


def stop_process(pid: int) -> bool:
    """Stop a wedged gateway and everything it started; True when none of it is left."""
    try:
        import psutil
    except ImportError:
        return False
    try:
        proc = psutil.Process(pid)
        victims = proc.children(recursive=True) + [proc]
    except (psutil.Error, OSError):
        return True
    for victim in victims:
        try:
            victim.kill()
        except (psutil.Error, OSError):
            pass
    try:
        _, alive = psutil.wait_procs(victims, timeout=10)
    except (psutil.Error, OSError):
        return False
    return not alive


def _holder_line(host: str, port: int, held: dict) -> str:
    """One sentence naming who holds the port, as far as the OS would say."""
    pid, name = held.get("pid"), held.get("name")
    if pid is None:
        who = "a process this account cannot name"
    elif name:
        who = f"pid {pid} ({name})"
    else:
        who = f"pid {pid}"
    return f"keeper: {host}:{port} is held by {who}; leaving it alone"


def serve(
    port: int,
    host: str = LOOPBACK,
    *,
    stop: threading.Event | None = None,
    log: Path | None = None,
    poll_seconds: float = POLL_SECONDS,
    restart_seconds: float = RESTART_SECONDS,
    max_restart_seconds: float = MAX_RESTART_SECONDS,
    wedged_seconds: float = WEDGED_SECONDS,
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

    # A standing condition — a stranger on the port, a port this machine refuses —
    # is one line, not one per poll: forty identical lines before breakfast is how a
    # log stops being read.
    said: list[str] = []

    def say_once(message: str) -> None:
        if not said or said[-1] != message:
            said.append(message)
            say(message)

    say(f"keeper: watching {host}:{port} - the gateway runs as {sys.executable}")
    wait = restart_seconds
    wedged_since: float | None = None
    while not stop.is_set():
        if ports.live_gateway(port) is not None:
            wedged_since = None
            stop.wait(poll_seconds)
            continue
        held = ports.holder(port)
        if held is not None:
            pid = held.get("pid")
            if pid is not None and own_gateway(held):
                # Ours, and not answering: either it is still booting or it is wedged.
                # Waiting on it forever leaves the room dark behind a port nobody can
                # free, so after a boot's worth of silence the keeper replaces it.
                if wedged_since is None:
                    wedged_since = time.time()
                silent = time.time() - wedged_since
                if silent >= wedged_seconds:
                    say(f"keeper: the gateway on {host}:{port} has not answered for {silent:.0f}s (pid {pid}); replacing it")
                    if not stop_process(pid):
                        say(f"keeper: pid {pid} did not stop; leaving the port to it")
                        stop.wait(poll_seconds)
                        continue
                    wedged_since = None
                    said.clear()
                    stop.wait(1.0)
                    continue
                say_once(f"keeper: {host}:{port} is held by a gateway that is not answering yet (pid {pid}); waiting for it")
                stop.wait(poll_seconds)
                continue
            say_once(_holder_line(host, port, held))
            stop.wait(poll_seconds)
            continue
        refusal = ports.bind_blocker(port, host)
        if refusal is not None:
            reason = BIND_REFUSALS.get(refusal, refusal)
            say_once(f"keeper: cannot listen on {host}:{port}: {reason}; the room stays down until that changes")
            stop.wait(poll_seconds)
            continue
        wedged_since = None
        said.clear()
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
