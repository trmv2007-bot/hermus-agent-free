"""The keeper is what the logon task runs, so it must never fight for the port.

The room's gateway is started by a scheduled task with no session behind it. That
task runs the keeper, which owns port 8000 and restarts the gateway whenever it
exits. Its two refusals are the whole safety contract: never start a second
gateway beside one that already answers, and never take a port another process
holds. Both are proven here with a fake spawn, so no test opens a socket.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gateway import keeper

ROOT = Path(__file__).resolve().parents[1]


class Stopper:
    """A stop event that records the waits it was asked for, then says stop."""

    def __init__(self, after: int = 0):
        self.waits: list[float] = []
        self.limit = after
        self._stopped = False
        self._polls = 0

    def is_set(self) -> bool:
        return self._stopped

    def set(self) -> None:
        self._stopped = True

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self._polls += 1
        if self._polls > self.limit:
            self._stopped = True
        return self._stopped


@pytest.fixture()
def no_gateway(monkeypatch):
    """Nothing answers on the port and nothing holds it."""

    def answer(port):
        return None

    def free(port, host=keeper.LOOPBACK):
        return False

    monkeypatch.setattr(keeper.ports, "live_gateway", answer)
    monkeypatch.setattr(keeper.ports, "port_in_use", free)


def test_a_gateway_already_serving_the_port_is_left_alone(monkeypatch):
    """At logon the port may already be served by the person's own launch."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: {"status": "ok", "pid": 1})
    started: list[list[str]] = []
    stop = Stopper(after=2)

    keeper.serve(8000, spawn=lambda argv: started.append(argv), stop=stop)

    assert started == []


def test_a_port_another_process_holds_is_not_taken(monkeypatch, capsys):
    """A listener that is not this gateway is not a gateway to restart over."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    monkeypatch.setattr(keeper.ports, "port_in_use", lambda port, host=keeper.LOOPBACK: True)
    started: list[list[str]] = []
    stop = Stopper(after=2)

    keeper.serve(8000, spawn=lambda argv: started.append(argv), stop=stop)

    assert started == []
    assert "held by another process" in capsys.readouterr().out


def test_the_gateway_runs_on_loopback_from_this_installs_own_interpreter(no_gateway):
    """The action must bind loopback and use the venv that owns the packages."""
    started: list[list[str]] = []
    stop = Stopper(after=0)

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or 0, stop=stop)

    assert len(started) == 1
    argv = started[0]
    assert argv[0] == sys.executable
    assert Path(argv[1]) == ROOT / "hermus.py"
    assert argv[2:] == ["gateway", "start", "--port", "8000", "--host", "127.0.0.1"]


def test_a_gateway_that_keeps_dying_backs_off_instead_of_spinning(no_gateway):
    """A broken start retries within the minute, then less often, never in a hot loop."""
    stop = Stopper(after=4)

    keeper.serve(8000, spawn=lambda argv: 1, stop=stop)

    delays = [w for w in stop.waits if w >= keeper.RESTART_SECONDS]
    assert delays[:4] == [3.0, 6.0, 12.0, 24.0]
    assert max(delays) <= keeper.MAX_RESTART_SECONDS


def test_a_gateway_that_served_a_healthy_run_is_restarted_promptly(monkeypatch, no_gateway):
    """A healthy run resets the backoff, so the next crash gets seconds, not the cap."""
    monkeypatch.setattr(keeper, "HEALTHY_RUN_SECONDS", 0.0)
    stop = Stopper(after=2)

    keeper.serve(8000, spawn=lambda argv: 1, stop=stop)

    assert [w for w in stop.waits if w >= keeper.RESTART_SECONDS] == [keeper.RESTART_SECONDS] * 3


def test_the_cli_exposes_the_service_action():
    """`hermus gateway service` is the action the logon task names, so it must parse."""
    from hermus_cli import build_parser

    args = build_parser().parse_args(["gateway", "service"])
    assert args.gateway_action == "service"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
