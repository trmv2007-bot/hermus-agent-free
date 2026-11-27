"""The autostart entries that let the room come up by itself after an install.

A fresh Windows install used to end by *printing* a task command: ``bootstrap.py``
wrote a launcher and then handed the last step to the person, and
``hermus sentinel install`` printed ``schtasks /create`` and returned. So a new
machine got a companion that died with the terminal it was started from, and the
one task that existed here pointed at a launcher the install folder no longer
held — every logon failed and the room stayed dark. This module performs the
step instead.

Three parts, each of them testable without a task being created:

* **the launcher** — one ``.vbs`` per entry at a stable path inside the user
  profile, so a re-installed or moved checkout is re-pointed by rewriting this
  file and never by editing a task;
* **the logon task** — registered through PowerShell's ScheduledTasks module,
  which the signed-in account may do without elevation. ``schtasks /create
  /sc onlogon`` refuses that same account with "Access is denied" (measured on
  Windows 11 here), which is why the task is registered this way;
* **the probe** — the room has to answer ``/readyz`` on loopback, or the entry is
  undone and the failure is printed. A logon task that fails at every logon is
  worse than no task at all, and it is exactly the defect that cost a day here.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The port the room serves on when nothing names another one.
DEFAULT_PORT = 8000

#: Loopback, because a companion a person runs for themselves is not a service
#: other machines on the network may reach.
LOOPBACK = "127.0.0.1"

#: How long a freshly started task gets to answer before the entry is undone.
PROBE_SECONDS = 45.0

#: How long one probe request may take.
REQUEST_SECONDS = 2.0

#: Where the launchers live inside the user profile, matching the folder the
#: hand repair on this machine used so an existing install is not duplicated.
LAUNCHER_FOLDER = ("hermes", "gateway-service")


@dataclass(frozen=True)
class Entry:
    """One process a logon is supposed to start, and the task that starts it."""

    key: str
    task_name: str
    command: tuple[str, ...]
    description: str
    marker: str

    @property
    def launcher_name(self) -> str:
        return f"Hermes_{self.key.capitalize()}.vbs"


#: The keeper, which owns the port and starts the gateway again whenever it dies.
GATEWAY = Entry(
    key="gateway",
    task_name="Hermes_Gateway",
    command=("gateway", "service", "--port", "{port}", "--host", LOOPBACK),
    description="Hermes room - keeps the gateway up (hermus gateway service)",
    marker="gateway service",
)

#: The always-alert core, which reacts to events for as long as the person is signed in.
SENTINEL = Entry(
    key="sentinel",
    task_name="Hermes_Sentinel",
    command=("sentinel", "run", "--model", "auto"),
    description="Hermes sentinel - always-alert core (hermus sentinel run)",
    marker="sentinel run",
)

ENTRIES = (GATEWAY, SENTINEL)


@dataclass
class InstallResult:
    """What an install did, in lines meant to be printed as they are."""

    ok: bool
    lines: list[str] = field(default_factory=list)
    task_name: str = ""
    trigger: str = ""
    action: str = ""
    launcher: Path | None = None


def default_port() -> int:
    """The port the room serves on, from the same place the gateway reads it.

    A launcher that starts the room on 8000 while the rest of the product thinks
    the port is ``HERMUS_GATEWAY_PORT`` is a room nobody can find, so this asks
    the gateway's own configuration first and only then the environment.
    """
    try:
        from core.config import config

        return int(config.gateway_port)
    except Exception:  # noqa: BLE001 - a probe must not fail over its own port number
        try:
            return int(os.environ.get("HERMUS_GATEWAY_PORT") or DEFAULT_PORT)
        except ValueError:
            return DEFAULT_PORT


def launcher_dir() -> Path:
    """The stable folder inside the user profile that holds every launcher."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root.joinpath(*LAUNCHER_FOLDER)


def launcher_path(entry: Entry) -> Path:
    return launcher_dir() / entry.launcher_name


def entry_arguments(entry: Entry, port: int = DEFAULT_PORT) -> list[str]:
    return [part.format(port=port) for part in entry.command]


def _vbs_string(value: str) -> str:
    """A VBScript string literal: a quote inside it is doubled, never escaped."""
    return '"' + str(value).replace('"', '""') + '"'


def launcher_script(entry: Entry, root: Path, python: Path, port: int = DEFAULT_PORT) -> str:
    """The launcher text for one entry: what the task's action runs.

    The launcher waits for the process it starts, so the task stays ``Running``
    for as long as that process lives and exits with its code — that is what
    makes Task Scheduler's restart-on-failure apply to the keeper rather than
    only to this launcher.
    """
    arguments = " ".join(entry_arguments(entry, port))
    command = f'"{python}" "{Path(root) / "hermus.py"}" {arguments}'
    lines = [
        f"' HERMUS - launcher for the \\{entry.task_name} logon task.",
        "'",
        "' Written by bootstrap.py and `hermus sentinel install`. Re-running either rewrites",
        "' this file, so change the installer rather than this copy.",
        "'",
        f"' It runs: hermus {arguments}",
        "'",
        "' It starts that with no window and waits for it, so the task stays Running for the",
        "' life of the process and exits with its own code, which is what makes Task",
        "' Scheduler's restart-on-failure apply to the process rather than to this launcher.",
    ]
    if entry is GATEWAY:
        lines += [
            "'",
            f"' Ending this task (schtasks /end /tn \"{entry.task_name}\", or Task Scheduler's End)",
            "' does NOT stop the room: it ends this launcher, and the keeper it started",
            "' keeps serving the port. To actually stop the room, end that python tree.",
        ]
    lines += [
        "Option Explicit",
        "",
        "Dim shell, code",
        "",
        'Set shell = CreateObject("WScript.Shell")',
        f"shell.CurrentDirectory = {_vbs_string(str(root))}",
        f"code = shell.Run({_vbs_string(command)}, 0, True)",
        "WScript.Quit code",
        "",
    ]
    return "\r\n".join(lines)


def write_launcher(entry: Entry, root: Path, python: Path, port: int = DEFAULT_PORT) -> Path:
    """Write the launcher, replacing whatever was there, and return its path."""
    path = launcher_path(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = launcher_script(entry, root, python, port)
    try:
        path.write_bytes(text.encode("ascii"))
    except UnicodeEncodeError:
        # A profile path outside ASCII is still a profile path. Windows Script
        # Host reads a .vbs saved as UTF-16 with a BOM, and reads an ANSI one as
        # the system code page, so only this case changes encoding.
        path.write_text(text, encoding="utf-16")
    return path


def _ps_quote(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def encode_script(script: str) -> str:
    """``-EncodedCommand``: UTF-16LE base64, so no path ever needs shell quoting."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def register_script(entry: Entry, launcher: Path, *, backup: Path | None = None) -> str:
    """PowerShell that registers (or refreshes) the logon task and starts it once.

    ``-Force`` makes it idempotent: a second run replaces the definition instead
    of adding a second task. An existing definition is exported first so a failed
    probe can put it back rather than leave the machine without what it had.
    """
    action = f'//B //Nologo "{launcher}"'
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$name = {_ps_quote(entry.task_name)}",
            f"$backup = {_ps_quote(backup) if backup else '$null'}",
            "try {",
            "  if ($backup -and (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {",
            "    Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath $backup -Encoding UTF8",
            "  }",
            f"  $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(action)}",
            # The current account, spelled out: a logon trigger with no user means
            # every user, and that needs elevation. This one does not, and it is
            # the only trigger a companion the person runs for themselves wants.
            "  $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
            "  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me",
            "  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries"
            " -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew"
            " -ExecutionTimeLimit (New-TimeSpan -Seconds 0)"
            " -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)",
            f"  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger"
            f" -Settings $settings -Description {_ps_quote(entry.description)} -Force | Out-Null",
            "  $task = Get-ScheduledTask -TaskName $name",
            "  Write-Output ('HERMUS_TASK name=' + $task.TaskName)",
            "  Write-Output ('HERMUS_TASK state=' + $task.State)",
            "  Write-Output ('HERMUS_TASK action=' + $task.Actions[0].Execute + ' ' + $task.Actions[0].Arguments)",
            "  Write-Output ('HERMUS_TASK trigger=' + $task.Triggers[0].CimClass.CimClassName)",
            "  try {",
            "    Start-ScheduledTask -TaskName $name -ErrorAction Stop",
            "    Write-Output 'HERMUS_TASK started=yes'",
            "  } catch {",
            "    Write-Output ('HERMUS_TASK started=' + $_.Exception.Message)",
            "  }",
            "} catch {",
            "  Write-Output ('HERMUS_TASK error=' + $_.Exception.Message)",
            "  exit 3",
            "}",
            "",
        ]
    )


def inspect_script(entry: Entry) -> str:
    """PowerShell that prints what the task of this name already does."""
    return "\n".join(
        [
            "$ErrorActionPreference = 'SilentlyContinue'",
            f"$task = Get-ScheduledTask -TaskName {_ps_quote(entry.task_name)}",
            "if ($task) {",
            "  Write-Output ('HERMUS_TASK action=' + $task.Actions[0].Execute + ' ' + $task.Actions[0].Arguments)",
            "  Write-Output ('HERMUS_TASK trigger=' + $task.Triggers[0].CimClass.CimClassName)",
            "} else {",
            "  Write-Output 'HERMUS_TASK action='",
            "}",
            "",
        ]
    )


def start_script(entry: Entry) -> str:
    """PowerShell that starts the task once, as the person's own account may."""
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "try {",
            f"  Start-ScheduledTask -TaskName {_ps_quote(entry.task_name)}",
            "  Write-Output 'HERMUS_TASK started=yes'",
            "} catch {",
            "  Write-Output ('HERMUS_TASK error=' + $_.Exception.Message)",
            "  exit 3",
            "}",
            "",
        ]
    )


def rollback_script(entry: Entry, *, backup: Path | None = None) -> str:
    """PowerShell that puts the task back the way it was, or removes it."""
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$name = {_ps_quote(entry.task_name)}",
            f"$backup = {_ps_quote(backup) if backup else '$null'}",
            "try {",
            "  Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue",
            "  if ($backup -and (Test-Path -LiteralPath $backup)) {",
            "    Register-ScheduledTask -TaskName $name -Xml (Get-Content -LiteralPath $backup -Raw) -Force | Out-Null",
            "    Write-Output ('HERMUS_TASK restored=' + $name)",
            "  } elseif (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {",
            "    Unregister-ScheduledTask -TaskName $name -Confirm:$false",
            "    Write-Output ('HERMUS_TASK removed=' + $name)",
            "  } else {",
            "    Write-Output 'HERMUS_TASK removed=none'",
            "  }",
            "} catch {",
            "  Write-Output ('HERMUS_TASK error=' + $_.Exception.Message)",
            "  exit 3",
            "}",
            "",
        ]
    )


def rerun_command(entry: Entry, root: Path, python: Path) -> str:
    """The command that installs *this* entry again, for an elevated PowerShell.

    ``hermus sentinel install`` owns the sentinel only, so pointing a gateway
    refusal at it would leave the room starting by hand at every logon while
    reading as if autostart were complete. The room's entry is bootstrap's. The
    call operator and double-quoted paths are one PowerShell statement that works
    with a space in the profile and without one; a bare quoted path is a parser
    error there, which is the shell the Windows instructions use.
    """
    if entry is GATEWAY:
        target = f'"{Path(root) / "bootstrap.py"}"'
    else:
        target = f'"{Path(root) / "hermus.py"}" sentinel install'
    return f'& "{python}" {target}'


def elevated_command(entry: Entry, launcher: Path) -> str:
    """The line that registers the task by hand, for an elevated PowerShell."""
    action = f'//B //Nologo "{launcher}"'
    current_user = "$([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"
    return (
        f"Register-ScheduledTask -TaskName {_ps_quote(entry.task_name)} -Action "
        f"(New-ScheduledTaskAction -Execute 'wscript.exe' -Argument {_ps_quote(action)}) "
        f"-Trigger (New-ScheduledTaskTrigger -AtLogOn -User {current_user}) -Force"
    )


_POWERSHELL = ("powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass")


def _powershell(script: str, *, timeout: float = 90.0, run: Any = None) -> subprocess.CompletedProcess[str]:
    runner = run or subprocess.run
    return runner(
        [*_POWERSHELL, "-EncodedCommand", encode_script(script)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _facts(output: str) -> dict[str, str]:
    """The ``HERMUS_TASK key=value`` lines a script prints, as a dict."""
    facts: dict[str, str] = {}
    for line in (output or "").splitlines():
        if line.startswith("HERMUS_TASK "):
            key, _, value = line[len("HERMUS_TASK ") :].partition("=")
            facts[key.strip()] = value.strip()
    return facts


def _get_json(url: str, opener: Any) -> tuple[dict | None, str, int]:
    """``(body, error, http_code)`` for one request; a JSON error answer is still an answer."""
    try:
        with opener(url, timeout=REQUEST_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
            code = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        # /readyz answers 503 while it is warming up, and that is still the room
        # talking: only silence means it is not up.
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
        except (OSError, ValueError):
            return None, f"HTTP {exc.code}", exc.code
        return (body if isinstance(body, dict) else None), f"HTTP {exc.code}", exc.code
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        return None, str(exc) or type(exc).__name__, 0
    return (body if isinstance(body, dict) else None), "", code


def _is_health(body: dict | None) -> bool:
    """Is this Hermes's own ``/healthz``? The identity check, not the readiness one.

    Any JSON on the port is not the room: another service answering there with a
    JSON body would otherwise pass for a gateway, while the keeper waited because
    the port was somebody else's.
    """
    return isinstance(body, dict) and body.get("success") is True and body.get("status") == "ok" and "pid" in body


def probe_ready(
    port: int | None = None,
    *,
    timeout: float = PROBE_SECONDS,
    interval: float = 1.0,
    opener: Any = None,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Wait for the room to answer ``/readyz`` on loopback; report the pid that does."""
    port = port or default_port()
    opener = opener or urllib.request.urlopen
    deadline = time.time() + timeout
    last = "nothing answered"
    while True:
        for host, base in ((LOOPBACK, f"http://{LOOPBACK}:{port}"), ("::1", f"http://[::1]:{port}")):
            body, error, code = _get_json(f"{base}/readyz", opener)
            if code in (200, 503) and isinstance(body, dict):
                # /readyz answers 503 while it is warming up, which is still the
                # room talking — but only Hermes's own /healthz, with a pid, makes
                # it *this* room rather than whatever else holds the port.
                health, _, health_code = _get_json(f"{base}/healthz", opener)
                if health_code == 200 and _is_health(health):
                    status = str(body.get("status") or ("ready" if code == 200 else "not_ready"))
                    return {
                        "ok": True,
                        "status": status,
                        "pid": health.get("pid"),
                        "detail": f"/readyz {status} on {host}:{port}",
                    }
                last = f"{host}:{port} answers /readyz but /healthz is not this room"
            elif error:
                last = error
            elif code:
                last = f"HTTP {code} on {host}:{port} is not this room's /readyz"
        if time.time() >= deadline:
            return {
                "ok": False,
                "status": None,
                "pid": None,
                "detail": f"/readyz did not answer on {LOOPBACK}:{port} within {timeout:.0f}s ({last})",
            }
        sleep(interval)


def _family() -> set[int]:
    """This process and every ancestor of it.

    ``stop_processes`` matches on a command line, and a shell that ran the
    installer carries the same needle in its own: measured here, a stop call
    from a heredoc ended the bash wrapper that was running it. A process must
    never end itself or the chain that is waiting on it.
    """
    try:
        import psutil

        family = {os.getpid()}
        for parent in psutil.Process().parents():
            family.add(parent.pid)
        return family
    except Exception:  # noqa: BLE001 - a stop must not fail over its own guard
        return {os.getpid()}


def find_processes(needle: str) -> list[int]:
    """Pids whose command line contains ``needle``: proof that a task started something."""
    try:
        import psutil
    except ImportError:
        return []
    family = _family()
    found: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if needle in cmdline:
                found.append(int(proc.info["pid"]))
        except (KeyError, TypeError, psutil.Error):
            continue
    return found


def stop_processes(needle: str, *, exclude: Any = (), wait: float = 5.0) -> list[int]:
    """End the process a just-installed task started, and its children.

    Only ever called by an install that is undoing itself, and only for the
    process whose command line names this checkout. ``exclude`` is the set of
    pids that already matched before this install began: a keeper that was
    serving before is not this install's to end, even when the probe failed.
    """
    try:
        import psutil
    except ImportError:
        return []
    family = _family() | {int(pid) for pid in exclude or ()}
    stopped: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if needle not in " ".join(proc.info.get("cmdline") or []):
                continue
            if int(proc.info["pid"]) in family:
                continue
            targets = [t for t in (proc, *proc.children(recursive=True)) if t.pid not in family]
            for target in targets:
                try:
                    target.terminate()
                except psutil.Error:
                    continue
            _, alive = psutil.wait_procs(targets, timeout=wait)
            for target in alive:
                try:
                    target.kill()
                except psutil.Error:
                    continue
            stopped.append(int(proc.info["pid"]))
        except (KeyError, TypeError, psutil.Error):
            continue
    return stopped


def install(
    entry: Entry,
    *,
    root: Path,
    python: Path | None = None,
    port: int | None = None,
    timeout: float = PROBE_SECONDS,
    run: Any = None,
    probe: Any = None,
    find: Any = None,
    stop: Any = None,
    sleep: Any = time.sleep,
    platform: str | None = None,
) -> InstallResult:
    """Create the launcher and the logon task, prove the process comes up, or undo it.

    Idempotent: a second run rewrites the same launcher, replaces the same task by
    name, and leaves one entry behind. Evidence, not assumption, decides success —
    ``/readyz`` for the room, a matching command line for the sentinel.
    """
    root = Path(root).resolve()
    python = Path(python) if python else Path(sys.executable)
    port = port or default_port()
    probe = probe or probe_ready
    find = find or find_processes
    stop = stop or stop_processes
    if (platform or os.name) != "nt":
        return InstallResult(
            ok=False,
            task_name=entry.task_name,
            lines=[f"task \\{entry.task_name}: not created - logon tasks are a Windows entry; this platform has its own"],
        )

    path = launcher_path(entry)
    needle = f"{root / 'hermus.py'} {entry.marker}"
    # What was already running before this install: a keeper that was serving
    # before is not this install's to end, even when its own probe fails.
    preexisting = set(find(needle))
    previous = path.read_bytes() if path.is_file() else None
    write_launcher(entry, root, python, port)
    backup = launcher_dir() / "backups" / f"{entry.task_name}-{time.strftime('%Y%m%dT%H%M%S')}.xml"
    backup.parent.mkdir(parents=True, exist_ok=True)

    done = _powershell(register_script(entry, path, backup=backup), run=run)
    facts = _facts((done.stdout or "") + "\n" + (done.stderr or ""))
    result = InstallResult(
        ok=False,
        task_name=entry.task_name,
        trigger=facts.get("trigger", ""),
        action=facts.get("action", ""),
        launcher=path,
    )
    kept = False
    if done.returncode != 0 or facts.get("error"):
        reason = (
            facts.get("error")
            or (done.stderr or done.stdout or "").strip()[-300:].replace("\n", " ")
            or f"exit {done.returncode}"
        )
        # A definition written by another, higher-privileged account cannot be
        # replaced without elevation. It can still be *used*: its action points at
        # this launcher, which is ours to write, so the entry is repaired by
        # rewriting the launcher and the definition is left alone.
        existing = _facts(_powershell(inspect_script(entry), run=run).stdout or "")
        if str(path) in (existing.get("action") or ""):
            kept = True
            result.trigger = existing.get("trigger", "")
            result.action = existing.get("action", "")
            _powershell(start_script(entry), run=run)
            result.lines = [
                f"task \\{entry.task_name}: kept - it already runs {path}",
                f"  replacing that definition needs elevation ({reason}), so it is unchanged and only the launcher was rewritten",
                f"launcher: {path}",
            ]
        else:
            # The elevated command points at this launcher, so the launcher stays:
            # deleting it would hand the person a command that registers a task
            # running a file that is not there.
            if previous is not None:
                path.write_bytes(previous)
            result.lines = [
                f"task \\{entry.task_name}: NOT created - {reason}",
                "  nothing was scheduled, so nothing fails at every logon; a task that was already there is unchanged",
                f"  the launcher is in place at {path}",
                f"  register it once in an elevated PowerShell: {elevated_command(entry, path)}",
                f"  or re-run the installer in an elevated PowerShell: {rerun_command(entry, root, python)}",
            ]
            return result

    if not kept:
        result.lines = [
            f"task \\{entry.task_name}: registered (trigger: {result.trigger or 'at logon'}, action: {result.action or path})",
            f"launcher: {path}",
        ]

    if entry is GATEWAY:
        answer = probe(port, timeout=timeout)
        up, detail, pid = bool(answer.get("ok")), str(answer.get("detail") or ""), answer.get("pid")
    else:
        deadline = time.time() + timeout
        pids = find(needle)
        while not pids and time.time() < deadline:
            sleep(1.0)
            pids = find(needle)
        up, pid = bool(pids), (pids[0] if pids else None)
        detail = (
            f"pid {pid} is running `hermus {entry.marker}` from {root}"
            if up
            else f"no process running `hermus {entry.marker}` from {root} within {timeout:.0f}s"
        )

    if up:
        result.ok = True
        result.lines.append(f"room: {detail}" + (f", pid {pid}" if pid is not None else ""))
        return result

    result.lines.append(f"room: {detail}")
    if kept:
        # The item is still the task's action, so it must keep pointing at
        # something that starts: restore what was there, and only fall back to
        # leaving this one when there was nothing to restore.
        if previous is not None:
            path.write_bytes(previous)
            result.lines.append(
                f"task \\{entry.task_name}: left exactly as it was, launcher restored - this install did not create or change it"
            )
        else:
            result.lines.append(
                f"task \\{entry.task_name}: left exactly as it was; its launcher now points at {root}, which did not answer"
            )
        return result
    undone = _powershell(rollback_script(entry, backup=backup if backup.is_file() else None), run=run)
    undo = _facts((undone.stdout or "") + "\n" + (undone.stderr or ""))
    if previous is not None:
        path.write_bytes(previous)
    else:
        path.unlink(missing_ok=True)
    ended = stop(needle, exclude=preexisting)
    if undo.get("restored"):
        state = f"task \\{entry.task_name} put back to what it was"
    elif undo.get("removed") not in (None, "none"):
        state = f"task \\{entry.task_name} removed"
    elif undo.get("removed") == "none":
        state = f"task \\{entry.task_name} was never left registered"
    else:
        state = f"task \\{entry.task_name} could not be undone ({undo.get('error') or undone.returncode}); remove it by hand"
    result.lines.append(
        f"undo: {state}, launcher {'restored' if previous is not None else 'removed'}, "
        f"{len(ended or [])} process(es) stopped - nothing is left half-installed"
    )
    return result


def install_all(
    *,
    root: Path,
    python: Path | None = None,
    port: int | None = None,
    timeout: float = PROBE_SECONDS,
    run: Any = None,
    probe: Any = None,
    find: Any = None,
    stop: Any = None,
    sleep: Any = time.sleep,
    platform: str | None = None,
) -> list[InstallResult]:
    """Both entries a fresh install owns: the room, then the sentinel."""
    port = port or default_port()
    return [
        install(
            entry,
            root=root,
            python=python,
            port=port,
            timeout=timeout,
            run=run,
            probe=probe,
            find=find,
            stop=stop,
            sleep=sleep,
            platform=platform,
        )
        for entry in ENTRIES
    ]
