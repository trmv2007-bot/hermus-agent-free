"""The install creates autostart entries, proves them, and undoes what does not work.

No test here calls ``schtasks``, and none registers a real task: PowerShell is a
replaced runner that decodes the script it is handed and answers with the lines
the real one prints. What is held is the part that has already cost a day on
this machine — the shape of the entry, that a second install leaves one entry
behind, that a task which does not come up is removed instead of failing at
every logon, and that a refusal is reported as a refusal.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core import autostart

FACTS = "\n".join(
    [
        "HERMUS_TASK name=Hermes_Gateway",
        "HERMUS_TASK state=Ready",
        'HERMUS_TASK action=wscript.exe //B //Nologo "C:\\Users\\someone\\AppData\\Local\\hermes\\gateway-service\\Hermes_Gateway.vbs"',
        "HERMUS_TASK trigger=MSFT_TaskLogonTrigger",
        "HERMUS_TASK started=yes",
        "",
    ]
)


def _script_of(argv: list[str]) -> str:
    encoded = argv[argv.index("-EncodedCommand") + 1]
    return base64.b64decode(encoded).decode("utf-16-le")


class FakePowerShell:
    """Answers as the real runner would, and refuses to be schtasks."""

    def __init__(self, reply=None):
        self.calls: list[str] = []
        self.reply = reply or (lambda script: (0, FACTS, ""))

    def __call__(self, argv, *, capture_output, text, timeout, check):
        joined = " ".join(argv).lower()
        assert "schtasks" not in joined, "an install must not shell out to schtasks"
        script = _script_of(argv)
        self.calls.append(script)
        export = re.search(r"\$backup = '([^']*)'", script)
        if export and "Export-ScheduledTask" in script:
            # The real script exports a definition it means to be able to restore.
            Path(export.group(1)).write_text("<Task/>", encoding="utf-8")
        code, out, err = self.reply(script)
        return subprocess.CompletedProcess(argv, code, out, err)


@pytest.fixture
def profile(monkeypatch, tmp_path):
    """A profile of its own, so no test can touch the person's real launchers."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    return tmp_path / "Local"


@pytest.fixture
def root(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "hermus.py").write_text("# entry point\n", encoding="utf-8")
    return checkout


def test_launcher_lives_at_a_stable_path_in_the_profile(profile):
    path = autostart.launcher_path(autostart.GATEWAY)
    assert path == profile / "hermes" / "gateway-service" / "Hermes_Gateway.vbs"
    assert autostart.launcher_path(autostart.SENTINEL).name == "Hermes_Sentinel.vbs"


def test_launcher_runs_this_checkouts_room_with_no_window(profile, root):
    python = root / "runtime" / "python.exe"
    text = autostart.launcher_script(autostart.GATEWAY, root, python)
    assert str(root / "hermus.py") in text
    assert str(python) in text
    assert "gateway service --port 8000 --host 127.0.0.1" in text
    assert "shell.CurrentDirectory = " in text
    assert ", 0, True)" in text, "a console window at logon would interrupt the person"
    assert "WScript.Quit code" in text
    text.encode("ascii"), "a launcher a console code page cannot hold is not a launcher"

    sentinel = autostart.launcher_script(autostart.SENTINEL, root, python)
    assert "sentinel run --model auto" in sentinel
    # Ending the sentinel task does stop the sentinel; the caveat is about the keeper.
    assert "does NOT stop the room" not in sentinel


def test_launcher_carries_the_end_does_not_stop_the_room_caveat(profile, root):
    text = autostart.launcher_script(autostart.GATEWAY, root, root / "python.exe")
    assert "schtasks /end" in text
    assert "keeps serving the port" in text


def test_install_rewrites_the_launcher_instead_of_appending(profile, root):
    first = autostart.write_launcher(autostart.GATEWAY, root, root / "python.exe")
    stale = first.read_text(encoding="ascii") + "\n' a stale trailing line\n"
    first.write_text(stale, encoding="ascii")
    again = autostart.write_launcher(autostart.GATEWAY, root, root / "python.exe")
    assert again.read_bytes() == autostart.launcher_script(autostart.GATEWAY, root, root / "python.exe").encode("ascii")
    assert len(list(again.parent.iterdir())) == 1, "a launcher per entry, not one per run"


def test_task_registration_asks_for_no_elevation(profile, root):
    launcher = autostart.launcher_path(autostart.GATEWAY)
    script = autostart.register_script(autostart.GATEWAY, launcher)
    assert "Register-ScheduledTask" in script
    assert "-AtLogOn -User $me" in script, "a trigger for every user is the one that needs elevation"
    assert "'Hermes_Gateway'" in script
    assert str(launcher) in script
    assert "-Force" in script, "a second install must replace the entry, not add one"
    assert "highest" not in script.lower()
    assert "runlevel" not in script.lower()
    assert "-RestartCount 3" in script and "-ExecutionTimeLimit (New-TimeSpan -Seconds 0)" in script
    script.encode("ascii"), "a PowerShell script a console code page cannot hold will not run"


def test_install_reports_what_it_created_and_the_pid_serving(profile, root):
    powershell = FakePowerShell()
    result = autostart.install(
        autostart.GATEWAY,
        root=root,
        python=root / "python.exe",
        platform="nt",
        run=powershell,
        probe=lambda port, timeout: {"ok": True, "pid": 9020, "detail": "/readyz ready on 127.0.0.1:8000"},
        stop=lambda needle: [],
    )
    assert result.ok
    assert result.task_name == "Hermes_Gateway"
    assert result.trigger == "MSFT_TaskLogonTrigger"
    text = "\n".join(result.lines)
    assert "task \\Hermes_Gateway: registered" in text
    assert "MSFT_TaskLogonTrigger" in text
    assert "pid 9020" in text
    assert result.launcher is not None and result.launcher.is_file()
    assert len(powershell.calls) == 1, "one registration call, no separate start dance"


def test_second_install_leaves_one_entry(profile, root):
    powershell = FakePowerShell()

    def probe(port, timeout):
        return {"ok": True, "pid": 1, "detail": "/readyz ready"}

    for _ in range(2):
        assert autostart.install(
            autostart.GATEWAY, root=root, python=root / "python.exe", platform="nt", run=powershell, probe=probe
        ).ok
    assert len(powershell.calls) == 2
    assert all("-Force" in call for call in powershell.calls)
    assert all("'Hermes_Gateway'" in call for call in powershell.calls)
    launchers = list(autostart.launcher_dir().glob("*.vbs"))
    assert [p.name for p in launchers] == ["Hermes_Gateway.vbs"]


def test_a_room_that_never_answers_is_reported_and_undone(profile, root):
    def reply(script: str):
        if "Unregister-ScheduledTask" in script:
            return 0, "HERMUS_TASK removed=Hermes_Gateway\n", ""
        return 0, FACTS, ""

    powershell = FakePowerShell(reply)
    stopped: list[str] = []
    result = autostart.install(
        autostart.GATEWAY,
        root=root,
        python=root / "python.exe",
        platform="nt",
        run=powershell,
        probe=lambda port, timeout: {"ok": False, "pid": None, "detail": "/readyz did not answer (refused)"},
        stop=lambda needle: stopped.append(needle) or [],
    )
    text = "\n".join(result.lines)
    assert not result.ok
    assert "did not answer" in text
    assert "undo: task \\Hermes_Gateway removed" in text
    assert "half-installed" in text
    assert any("Unregister-ScheduledTask" in call for call in powershell.calls)
    assert not autostart.launcher_path(autostart.GATEWAY).exists(), "an entry that failed leaves no launcher behind"
    assert stopped == [f"{root.resolve() / 'hermus.py'} gateway service"], "the process it started is ours to end"


def test_a_failed_probe_puts_back_the_task_that_was_there(profile, root):
    previous = autostart.launcher_path(autostart.GATEWAY)
    previous.parent.mkdir(parents=True, exist_ok=True)
    previous.write_text("' the launcher this machine already had\n", encoding="ascii")
    powershell = FakePowerShell(lambda script: (0, "HERMUS_TASK restored=Hermes_Gateway\n" if "-Xml" in script else FACTS, ""))
    result = autostart.install(
        autostart.GATEWAY,
        root=root,
        python=root / "python.exe",
        platform="nt",
        run=powershell,
        probe=lambda port, timeout: {"ok": False, "pid": None, "detail": "/readyz did not answer"},
        stop=lambda needle: [],
    )
    assert not result.ok
    undo = [call for call in powershell.calls if "Register-ScheduledTask -TaskName $name -Xml" in call]
    assert undo, "with a definition to restore, the undo restores rather than deletes"
    backup = re.search(r"\$backup = '([^']*)'", undo[0]).group(1)
    assert Path(backup).is_file(), "the export the undo depends on has to have been written"
    assert "put back to what it was" in "\n".join(result.lines)
    assert previous.read_text(encoding="ascii") == "' the launcher this machine already had\n"


def test_a_refused_registration_says_what_was_not_done(profile, root):
    powershell = FakePowerShell(lambda script: (3, "HERMUS_TASK error=Access is denied\n", ""))
    result = autostart.install(
        autostart.GATEWAY,
        root=root,
        python=root / "python.exe",
        platform="nt",
        run=powershell,
        probe=lambda port, timeout: {"ok": False, "pid": None, "detail": "should never be reached"},
        stop=lambda needle: [],
    )
    text = "\n".join(result.lines)
    assert not result.ok
    assert "NOT created - Access is denied" in text
    assert "elevated" in text and 'powershell -NoProfile -Command "Register-ScheduledTask' in text
    assert "installed" not in text.lower().replace("installed;", "")
    assert len(powershell.calls) == 2, "the register attempt, then a look at what is already there"
    assert "SilentlyContinue" in powershell.calls[1], "a refusal is followed by a look, not by an undo"
    assert not autostart.launcher_path(autostart.GATEWAY).exists()
    assert "room:" not in text, "a refused entry was never started, so it cannot claim to serve"


def test_a_task_it_may_not_replace_is_kept_when_it_already_runs_the_launcher(profile, root):
    launcher = autostart.launcher_path(autostart.GATEWAY)

    def reply(script: str):
        if "Register-ScheduledTask -TaskName $name -Action" in script:
            return 3, "HERMUS_TASK error=Access is denied.\n", ""
        if "SilentlyContinue" in script:
            return 0, f'HERMUS_TASK action=wscript.exe //B //Nologo "{launcher}"\nHERMUS_TASK trigger=MSFT_TaskLogonTrigger\n', ""
        return 0, "HERMUS_TASK started=yes\n", ""

    powershell = FakePowerShell(reply)
    result = autostart.install(
        autostart.GATEWAY,
        root=root,
        python=root / "python.exe",
        platform="nt",
        run=powershell,
        probe=lambda port, timeout: {"ok": True, "pid": 5150, "detail": "/readyz ready on 127.0.0.1:8000"},
        stop=lambda needle: [],
    )
    text = "\n".join(result.lines)
    assert result.ok
    assert "kept - it already runs" in text, "a definition it may not replace is used, not thrown away"
    assert "elevation" in text
    assert "pid 5150" in text
    assert any("Start-ScheduledTask" in call for call in powershell.calls)
    assert not any("Unregister-ScheduledTask" in call for call in powershell.calls)
    assert launcher.is_file(), "the launcher is the part this install can still repair"


def test_a_kept_task_is_not_undone_when_the_room_stays_dark(profile, root):
    launcher = autostart.launcher_path(autostart.GATEWAY)

    def reply(script: str):
        if "Register-ScheduledTask -TaskName $name -Action" in script:
            return 3, "HERMUS_TASK error=Access is denied.\n", ""
        if "SilentlyContinue" in script:
            return 0, f'HERMUS_TASK action=wscript.exe //B //Nologo "{launcher}"\n', ""
        return 0, "HERMUS_TASK started=yes\n", ""

    powershell = FakePowerShell(reply)
    result = autostart.install(
        autostart.GATEWAY,
        root=root,
        python=root / "python.exe",
        platform="nt",
        run=powershell,
        probe=lambda port, timeout: {"ok": False, "pid": None, "detail": "/readyz did not answer"},
        stop=lambda needle: [],
    )
    assert not result.ok
    assert "left exactly as it was" in "\n".join(result.lines)
    assert not any("Unregister-ScheduledTask" in call for call in powershell.calls), (
        "a task this install did not create is not its to delete"
    )


def test_sentinel_install_proves_the_process_is_running(profile, root):
    seen: list[str] = []

    def find(needle: str) -> list[int]:
        seen.append(needle)
        return [4242] if len(seen) > 1 else []

    result = autostart.install(
        autostart.SENTINEL,
        root=root,
        python=root / "python.exe",
        platform="nt",
        run=FakePowerShell(),
        find=find,
        sleep=lambda seconds: None,
        stop=lambda needle: [],
    )
    assert result.ok
    assert "pid 4242" in "\n".join(result.lines)
    assert seen[0] == f"{root.resolve() / 'hermus.py'} sentinel run"


def test_sentinel_that_never_starts_is_reported_and_undone(profile, root):
    called: list[str] = []
    result = autostart.install(
        autostart.SENTINEL,
        root=root,
        python=root / "python.exe",
        platform="nt",
        run=FakePowerShell(lambda script: (0, "HERMUS_TASK removed=Hermes_Sentinel\n", "")),
        find=lambda needle: [],
        sleep=lambda seconds: called.append("slept"),
        stop=lambda needle: [],
        timeout=0,
    )
    assert not result.ok
    assert "no process running" in "\n".join(result.lines)
    assert "undo: task \\Hermes_Sentinel" in "\n".join(result.lines)


def test_elevated_command_is_one_pasteable_line(profile):
    command = autostart.elevated_command(autostart.GATEWAY, autostart.launcher_path(autostart.GATEWAY))
    assert "\n" not in command
    assert command.startswith('powershell -NoProfile -Command "Register-ScheduledTask')
    assert "-TaskName 'Hermes_Gateway'" in command
    assert "-AtLogOn" in command


class _Room(BaseHTTPRequestHandler):
    answered = True

    def do_GET(self):
        if self.path == "/healthz":
            body = {"status": "ok", "pid": 4321}
            self.send_response(200)
        else:
            body = {"status": "ready", "reasons": []}
            self.send_response(200 if self.answered else 503)
        payload = json.dumps(body).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def test_probe_reads_a_real_loopback_answer(profile):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Room)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        answer = autostart.probe_ready(server.server_address[1], timeout=5.0, interval=0.1)
    finally:
        server.shutdown()
        server.server_close()
    assert answer["ok"]
    assert answer["pid"] == 4321
    assert "ready" in answer["detail"]


def test_probe_reports_silence_as_failure(profile):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    answer = autostart.probe_ready(port, timeout=0.2, interval=0.1)
    assert not answer["ok"]
    assert answer["pid"] is None
    assert "did not answer" in answer["detail"]


def test_install_all_owns_the_room_and_the_sentinel(profile, root):
    powershell = FakePowerShell()
    results = autostart.install_all(
        root=root,
        python=root / "python.exe",
        run=powershell,
        probe=lambda port, timeout: {"ok": True, "pid": 7, "detail": "/readyz ready"},
        find=lambda needle: [8],
        sleep=lambda seconds: None,
        platform="nt",
    )
    assert [result.task_name for result in results] == ["Hermes_Gateway", "Hermes_Sentinel"]
    assert all(result.ok for result in results)
    assert sorted(p.name for p in autostart.launcher_dir().glob("*.vbs")) == ["Hermes_Gateway.vbs", "Hermes_Sentinel.vbs"]
