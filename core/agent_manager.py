"""Persistent background agents — jobs keep running after the terminal closes.

    hermus agent create researcher   # define a named agent
    hermus agent start researcher    # spawn a background worker
    hermus agent status researcher   # inspect pid / heartbeat / last job
    hermus agent stop researcher     # graceful shutdown
    hermus agent list                # all agents + states

Each agent gets a directory under ``~/.hermus/agents/<name>/`` with:

    agent.json    # role, model, persona, config
    state.json    # status, pid, heartbeat, last result
    jobs/         # queued job files (one JSON per job)

The worker is a plain function (``worker_loop``) spawned via multiprocessing;
it drains its job queue, records a heartbeat, and reports results. A watchdog
(``watchdog_tick``) detects stale/dead agents and restarts them.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable

from .workspace import workspace
import builtins

ROLES = (
    "researcher", "coder", "system-monitor", "scheduler", "memory-manager",
    "watchdog", "computer-operator", "coordinator", "generic",
)

BASE_DIR = Path(__file__).parent.parent

# role -> handler(job_dict) -> result_dict. Register custom role logic here;
# it must be importable in a fresh interpreter (the detached worker imports it).
ROLE_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_handler(role: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    ROLE_HANDLERS[role] = handler


def _general_agent_handler(config: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def handle(job: dict[str, Any]) -> dict[str, Any]:
        from .agent import HermusAgent
        from .runtime import execute as runtime_execute

        task = str(job.get("task") or job.get("goal") or "")
        if not task:
            return {"ok": False, "error": "job has no task"}
        agent = HermusAgent(
            model=config.get("model"),
            session_id=f"background_{config.get('name', 'agent')}",
        )
        # Background agents use the universal runtime too: goal-like tasks get
        # the full mission lifecycle instead of a single chat turn.
        result = runtime_execute(task, agent=agent, prefer=str(job.get("prefer") or "auto"))
        ok = bool(result.get("success", True)) if isinstance(result, dict) else bool(result)
        if isinstance(result, dict) and result.get("run_kind") == "mission":
            ok = result.get("state") == "completed"
        return {"ok": ok, "task": task, "result": result}
    return handle


def _computer_agent_handler(job: dict[str, Any]) -> dict[str, Any]:
    """Run or resume one persistent desktop task in a detached worker."""
    from core.computer import (
        ComputerActionController,
        ComputerAgent,
        ScreenRecorder,
        TargetDetector,
        VideoAnalyzer,
    )

    model = job.get("model")
    analyzer = VideoAnalyzer.with_ollama(model) if model else None
    recorder = ScreenRecorder(fps=float(job.get("fps", 2.0)), max_seconds=float(job.get("max_seconds", 120.0)))
    controller = ComputerActionController(
        frame_provider=recorder.latest,
        target_detector=TargetDetector(vision_model=analyzer.vision_model if analyzer else None),
        scope=str(job.get("scope") or "background-computer"),
    )
    agent = ComputerAgent(
        controller=controller,
        recorder=recorder,
        analyzer=analyzer,
        learn_skills=not bool(job.get("no_skill")),
        max_retries=int(job.get("retries", 2)),
    )
    if job.get("resume"):
        result = agent.resume(str(job.get("task_id")), dry_run=bool(job.get("dry_run")))
    else:
        result = agent.run(
            str(job.get("task") or ""),
            task_id=job.get("task_id"),
            dry_run=bool(job.get("dry_run")),
        )
    return {"ok": bool(result.get("success")), "computer_task": result}


def worker_entry(name: str) -> None:
    """Detached worker entrypoint with built-in autonomous role handlers."""
    cfg = _read_json(_agent_dir(name) / "agent.json", {})
    role = cfg.get("role", "generic")
    handler = ROLE_HANDLERS.get(role)
    if handler is None:
        handler = _computer_agent_handler if role == "computer-operator" else _general_agent_handler(cfg)
    worker_loop(name, handler=handler)


def _agents_dir() -> Path:
    return workspace.dirs["agents"]


def _agent_dir(name: str) -> Path:
    return _agents_dir() / name


def _state_path(name: str) -> Path:
    return _agent_dir(name) / "state.json"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def worker_loop(name: str, handler=None, heartbeat_interval: float = 2.0,
                max_idle: Optional[float] = None) -> None:
    """Background worker: drain the agent's job queue, update heartbeat.

    ``handler`` maps a job dict to a result dict. Default handler echoes the
    job back (useful for tests and as a template for real role logic).
    """
    handler = handler or (lambda job: {"ok": True, "echo": job})
    adir = _agent_dir(name)
    jobs_dir = adir / "jobs"
    results_dir = adir / "results"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    state = {"status": "running", "pid": os.getpid(), "heartbeat": datetime.now().isoformat(),
             "last_result": None, "jobs_done": 0}
    _write_json(_state_path(name), state)
    idle_started = time.time()
    while True:
        try:
            jobs = sorted(jobs_dir.glob("*.json"))
            if jobs:
                idle_started = time.time()
                job_path = jobs[0]
                job = _read_json(job_path, {})
                try:
                    result = handler(job)
                    result = result if isinstance(result, dict) else {"result": result}
                except Exception as e:  # noqa: BLE001
                    result = {"ok": False, "error": str(e)}
                job_id = str(job.get("_job_id") or job_path.stem)
                result_record = {
                    "job_id": job_id,
                    "agent": name,
                    "task": job.get("task"),
                    "success": bool(result.get("ok", result.get("success", False))),
                    "result": result,
                    "finished": datetime.now().isoformat(),
                }
                _write_json(results_dir / f"{job_id}.json", result_record)
                job_path.unlink(missing_ok=True)
                state = _read_json(_state_path(name), state)
                state.update(
                    {
                        "status": "running",
                        "pid": os.getpid(),
                        "heartbeat": datetime.now().isoformat(),
                        "last_job_id": job_id,
                        "last_result": result_record,
                        "jobs_done": state.get("jobs_done", 0) + 1,
                    }
                )
                _write_json(_state_path(name), state)
                continue
            # heartbeat + idle timeout
            state = _read_json(_state_path(name), state)
            state.update({"status": "running", "pid": os.getpid(), "heartbeat": datetime.now().isoformat()})
            _write_json(_state_path(name), state)
            if max_idle is not None and (time.time() - idle_started) > max_idle:
                state["status"] = "idle-exit"
                _write_json(_state_path(name), state)
                return
            time.sleep(heartbeat_interval)
        except KeyboardInterrupt:
            state["status"] = "stopped"
            _write_json(_state_path(name), state)
            return


class AgentManager:
    def create(self, name: str, role: str = "generic", model: Optional[str] = None,
               persona: Optional[str] = None) -> dict[str, Any]:
        if role not in ROLES:
            return {"success": False, "error": f"unknown role '{role}' (choose {ROLES})"}
        adir = _agent_dir(name)
        if adir.exists():
            return {"success": False, "error": f"agent '{name}' already exists"}
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "jobs").mkdir(exist_ok=True)
        (adir / "results").mkdir(exist_ok=True)
        _write_json(adir / "agent.json", {
            "name": name, "role": role, "model": model, "persona": persona,
            "created": datetime.now().isoformat(),
        })
        _write_json(_state_path(name), {"status": "created", "pid": None, "heartbeat": None,
                                        "last_result": None, "jobs_done": 0})
        return {"success": True, "name": name, "role": role}

    def start(self, name: str, handler=None, daemon: bool = True) -> dict[str, Any]:
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found (create it first)"}
        state = _read_json(_state_path(name), {})
        if state.get("status") == "running" and self.status(name).get("alive"):
            return {"success": False, "error": f"agent '{name}' already running (pid {state.get('pid')})"}

        log_path = _agent_dir(name) / "worker.log"
        log_fh = open(log_path, "a", encoding="utf-8")
        code = "import sys; from core.agent_manager import worker_entry; worker_entry(sys.argv[1])"
        # Detached process: start_new_session=True so it survives the CLI exiting.
        proc = subprocess.Popen(
            [sys.executable, "-c", code, name],
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _write_json(_state_path(name), {
            "status": "starting", "pid": proc.pid, "heartbeat": datetime.now().isoformat(),
            "last_result": None, "jobs_done": 0,
        })
        return {"success": True, "name": name, "pid": proc.pid}

    def stop(self, name: str) -> dict[str, Any]:
        state = _read_json(_state_path(name), {})
        pid = state.get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                pass
        _write_json(_state_path(name), {**state, "status": "stopped", "pid": None})
        return {"success": True, "name": name}

    def status(self, name: str) -> dict[str, Any]:
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found"}
        cfg = _read_json(_agent_dir(name) / "agent.json", {})
        state = _read_json(_state_path(name), {})
        alive = False
        if state.get("status") in ("running", "starting") and state.get("pid"):
            try:
                os.kill(state["pid"], 0)
                alive = True
            except OSError:
                alive = False
        return {"success": True, "name": name, **cfg, **state, "alive": alive}

    def list(self) -> builtins.list[dict[str, Any]]:
        out = []
        if not _agents_dir().exists():
            return out
        for p in sorted(_agents_dir().iterdir()):
            if p.is_dir() and (p / "agent.json").exists():
                out.append(self.status(p.name))
        return out

    def submit_job(self, name: str, job: dict[str, Any]) -> dict[str, Any]:
        """Queue a job for a (running) agent to pick up."""
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found"}
        jobs_dir = _agent_dir(name) / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
        payload = {**job, "_job_id": ts, "_submitted": datetime.now().isoformat()}
        path = jobs_dir / f"{ts}.json"
        _write_json(path, payload)
        return {"success": True, "name": name, "job_id": ts, "queued": True,
                "status_path": str(_agent_dir(name) / "results" / f"{ts}.json")}

    def job_status(self, name: str, job_id: str) -> dict[str, Any]:
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found"}
        result_path = _agent_dir(name) / "results" / f"{job_id}.json"
        if result_path.exists():
            return {"success": True, "status": "finished", **_read_json(result_path, {})}
        queued = (_agent_dir(name) / "jobs" / f"{job_id}.json").exists()
        return {"success": True, "status": "queued" if queued else "unknown",
                "name": name, "job_id": job_id}

    def wait_job(self, name: str, job_id: str, timeout: float = 120.0,
                 interval: float = 0.2) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() <= deadline:
            status = self.job_status(name, job_id)
            if status.get("status") in {"finished", "unknown"} or not status.get("success"):
                return status
            time.sleep(max(0.02, float(interval)))
        return {"success": False, "status": "timeout", "name": name,
                "job_id": job_id, "error": f"job did not finish within {timeout:g}s"}

    def watchdog_tick(self, stale_seconds: float = 30.0, restart: bool = True) -> dict[str, Any]:
        """Detect dead/stale agents and (optionally) restart them."""
        now = datetime.now()
        revived, stale, errors = [], [], []
        for a in self.list():
            name = a.get("name")
            hb = a.get("heartbeat")
            if a.get("alive"):
                continue
            if a.get("status") == "created":
                continue
            stale.append(name)
            if restart:
                try:
                    r = self.start(name)
                    if r.get("success"):
                        revived.append(name)
                    else:
                        errors.append(name)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{name}: {e}")
        return {"stale": stale, "revived": revived, "errors": errors, "tick": now.isoformat()}


agent_manager = AgentManager()
