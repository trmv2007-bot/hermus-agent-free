"""The keeper is what the logon task runs, so it must never fight for the port.

The room's gateway is started by a scheduled task with no session behind it. That
task runs the keeper, which owns port 8000 and restarts the gateway whenever it
exits. Its refusals are the whole safety contract: never start a second gateway
beside one that already answers, and never take a port another process holds. The
one holder it may replace is a gateway of its own install that has stopped
answering, because that orphan leaves the room dark behind a port nobody can
free. All of it is proven here with a fake spawn and a fake port, so no test opens
a socket.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gateway import keeper

ROOT = Path(__file__).resolve().parents[1]


def foreign(pid: int = 9911, argv: list[str] | None = None) -> dict:
    """A process on the port that is not a gateway of this install."""
    return {"pid": pid, "name": "python.exe", "cmdline": argv or [sys.executable, "-m", "http.server", "8000"]}


def orphan(pid: int = 9912) -> dict:
    """A gateway of this install — the keeper's own, left running and frozen."""
    return {
        "pid": pid,
        "name": "python.exe",
        "cmdline": [sys.executable, str(ROOT / "hermus.py"), "gateway", "start", "--port", "8000"],
    }


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


class Child:
    """A spawned gateway: the pid the log names, and the code it exits with."""

    def __init__(self, code: int = 0, pid: int = 4242):
        self.code = code
        self.pid = pid
        self.reaped = False

    def wait(self) -> int:
        self.reaped = True
        return self.code


@pytest.fixture()
def no_gateway(monkeypatch):
    """Nothing answers on the port and nothing holds it."""

    def answer(port):
        return None

    def nothing_holds(port):
        return None

    def bindable(port, host=keeper.LOOPBACK):
        return None

    monkeypatch.setattr(keeper.ports, "live_gateway", answer)
    monkeypatch.setattr(keeper.ports, "holder", nothing_holds)
    monkeypatch.setattr(keeper.ports, "bind_blocker", bindable)


def test_a_gateway_already_serving_the_port_is_left_alone(monkeypatch):
    """At logon the port may already be served by the person's own launch."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: {"status": "ok", "pid": 1})
    started: list[list[str]] = []
    stop = Stopper(after=2)

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or Child(), stop=stop)

    assert started == []


def test_a_port_another_process_holds_is_not_taken(monkeypatch, capsys):
    """A listener that is not this gateway is not a gateway to restart over."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    monkeypatch.setattr(keeper.ports, "holder", lambda port: foreign())
    started: list[list[str]] = []

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or Child(), stop=Stopper(after=2))

    assert started == []
    out = capsys.readouterr().out
    assert "127.0.0.1:8000 is held by pid 9911 (python.exe); leaving it alone" in out
    assert "pid 9911" in out


def test_a_stranger_on_the_port_is_named_once_not_once_poll(monkeypatch, capsys):
    """A standing condition is one line: forty of them before breakfast is not a log."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    monkeypatch.setattr(keeper.ports, "holder", lambda port: foreign())

    keeper.serve(8000, spawn=lambda argv: Child(), stop=Stopper(after=4))

    assert capsys.readouterr().out.count("leaving it alone") == 1


def test_a_holder_too_dead_to_name_is_still_not_taken(monkeypatch, capsys):
    """Something answers that this account may not name: it is not ours to remove."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    monkeypatch.setattr(keeper.ports, "holder", lambda port: {"pid": None, "name": None, "cmdline": []})
    started: list[list[str]] = []

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or Child(), stop=Stopper(after=1))

    assert started == []
    assert "a process this account cannot name" in capsys.readouterr().out


def test_a_gateway_of_this_install_that_stopped_answering_is_replaced(monkeypatch, capsys):
    """The reported defect: our frozen gateway holds the port and the room stays dark.

    A wedged gateway keeps its socket, so the port reads as taken by a process the
    keeper must not fight — except that this one is its own install's gateway, and
    the room comes back only if it is replaced.
    """
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    answers = [orphan()]
    monkeypatch.setattr(keeper.ports, "holder", lambda port: answers.pop() if answers else None)
    monkeypatch.setattr(keeper.ports, "bind_blocker", lambda port, host=keeper.LOOPBACK: None)
    stopped: list[int] = []
    monkeypatch.setattr(keeper, "stop_process", lambda pid: stopped.append(pid) or True)
    started: list[list[str]] = []

    keeper.serve(
        8000,
        spawn=lambda argv: started.append(argv) or Child(),
        stop=Stopper(after=2),
        wedged_seconds=0.0,
    )

    assert stopped == [9912]
    assert started, "the keeper replaced the wedge and then started the room"
    out = capsys.readouterr().out
    assert "has not answered" in out and "pid 9912" in out
    assert "gateway started" in out


def test_a_gateway_that_is_still_booting_is_given_its_time(monkeypatch, capsys):
    """A cold boot imports the whole app: silence for a poll is not a wedge."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    monkeypatch.setattr(keeper.ports, "holder", lambda port: orphan())
    stopped: list[int] = []
    monkeypatch.setattr(keeper, "stop_process", lambda pid: stopped.append(pid) or True)
    started: list[list[str]] = []

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or Child(), stop=Stopper(after=2))

    assert stopped == [] and started == []
    out = capsys.readouterr().out
    assert "is not answering yet (pid 9912); waiting for it" in out


def test_a_replacement_gateway_gets_its_own_grace_period(monkeypatch):
    """The silence timer belongs to the pid: a new holder must not inherit the old one's."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    monkeypatch.setattr(keeper.ports, "bind_blocker", lambda port, host=keeper.LOOPBACK: None)
    pids = [11, 12, 12]
    monkeypatch.setattr(keeper.ports, "holder", lambda port: orphan(pids.pop(0)) if pids else orphan(12))
    clock = iter([0.0, 30.0, 70.0])
    monkeypatch.setattr(keeper.time, "time", lambda: next(clock, 70.0))
    stopped: list[int] = []
    monkeypatch.setattr(keeper, "stop_process", lambda pid: stopped.append(pid) or True)
    started: list[list[str]] = []

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or Child(), stop=Stopper(after=3), wedged_seconds=60.0)

    # pid 11 has been silent since 0 s and is gone by 30 s; pid 12 appeared at 30 s, so at
    # 70 s it has only been silent for 40 s and its boot must not be cut short.
    assert stopped == []
    assert started == []


def test_a_wedge_that_will_not_die_is_not_started_over(monkeypatch, capsys):
    """If the orphan cannot be stopped, a second gateway would only fight for the port."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    monkeypatch.setattr(keeper.ports, "holder", lambda port: orphan())
    monkeypatch.setattr(keeper, "stop_process", lambda pid: False)
    started: list[list[str]] = []

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or Child(), stop=Stopper(after=1), wedged_seconds=0.0)

    assert started == []
    assert "did not stop" in capsys.readouterr().out


def test_a_probe_that_lies_once_does_not_darken_the_room(monkeypatch):
    """One poll that reports a holder where there is none must cost one poll, not the room."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    answers = [foreign(pid=4321)]
    monkeypatch.setattr(keeper.ports, "holder", lambda port: answers.pop() if answers else None)
    monkeypatch.setattr(keeper.ports, "bind_blocker", lambda port, host=keeper.LOOPBACK: None)
    started: list[list[str]] = []

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or Child(), stop=Stopper(after=2))

    assert started, "one false positive left the room down"


def test_a_port_this_machine_would_refuse_is_reported_as_that(monkeypatch, capsys):
    """A reserved or forbidden port is not another process holding it."""
    monkeypatch.setattr(keeper.ports, "live_gateway", lambda port: None)
    monkeypatch.setattr(keeper.ports, "holder", lambda port: None)
    monkeypatch.setattr(keeper.ports, "bind_blocker", lambda port, host=keeper.LOOPBACK: "reserved")
    started: list[list[str]] = []

    keeper.serve(8000, spawn=lambda argv: started.append(argv) or Child(), stop=Stopper(after=1))

    assert started == []
    out = capsys.readouterr().out
    assert "cannot listen on 127.0.0.1:8000" in out
    assert "reserves this port" in out
    assert "another process" not in out


def test_stopping_a_wedged_gateway_stops_what_it_started():
    """The replacement path is a real kill, not a request: prove it on a real tree."""
    psutil = pytest.importorskip("psutil")
    marker = f"keeper-stop-test-{os.getpid()}"
    script = chr(10).join(
        [
            "import subprocess, sys, time",
            "subprocess.run([sys.executable, '-c', 'import time; time.sleep(30)'])",
            f"time.sleep(30)  # {marker}",
        ]
    )
    worker = threading.Thread(target=subprocess.run, args=([sys.executable, "-c", script],), daemon=True)
    worker.start()
    parent = None
    children: list = []
    try:
        deadline = time.time() + 30
        while time.time() < deadline and parent is None:
            for proc in psutil.process_iter(["pid", "cmdline"]):
                if any(marker in part for part in (proc.info.get("cmdline") or [])):
                    parent = psutil.Process(proc.pid)
                    break
            if parent is None:
                time.sleep(0.1)
        assert parent is not None, "the test tree never appeared"
        while time.time() < deadline and not children:
            children = parent.children(recursive=True)
            time.sleep(0.1)
        assert children, "the test tree never started anything to stop"

        assert keeper.stop_process(parent.pid) is True
        assert all(not child.is_running() for child in children)
    finally:
        for victim in children + ([parent] if parent is not None else []):
            try:
                victim.kill()
            except psutil.Error:
                pass
