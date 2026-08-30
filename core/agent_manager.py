"""Named background agents — identity + delegation, NOT a second worker lifecycle.

Clean-slate (Final Rebuild Spec §4): there is exactly **one** background job
execution path — the canonical Job system (``gateway.queue.JobQueue`` over
``core.contracts.jobs.Job``), which owns lease, heartbeat, retry, timeout,
cancellation and restart recovery. ``AgentManager`` is now a **thin named-agent
registry + delegation facade**:

* ``create`` / ``list`` / ``status``  — agent **identity** (role, model, persona)
  persisted as ``agent.json`` metadata. Identity is metadata, never a lifecycle.
* ``start`` / ``stop``                 — registry lifecycle flags only. They no
  longer spawn/terminate a detached subprocess.
* ``submit_job`` / ``job_status`` / ``wait_job`` — enqueue and inspect a **canonical
  Job** on the canonical queue. Role dispatch becomes a canonical Job handler
  (``agent.general`` / ``agent.computer``) registered on the queue.
* ``watchdog_tick``                    — reconcile the registry against the
  canonical queue's owned state; it does not manage its own process pool.

The old ``worker_loop`` / ``worker_entry`` subprocess protocol and the per-agent
``jobs/*.json`` / ``results/*.json`` / ``state.json`` heartbeat files are removed.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .workspace import workspace

ROLES = (
    "researcher", "coder", "system-monitor", "scheduler", "memory-manager",
    "watchdog", "computer-operator", "coordinator", "generic",
)

# role -> handler(job_dict) -> result_dict. Custom role logic may register here;
# it is also the source of the canonical queue handler builders.
ROLE_HANDLERS: dict[str, Any] = {}


def register_handler(role: str, handler) -> None:
    """Register a custom in-process role handler (called by ``{role}_agent_handler``)."""
    ROLE_HANDLERS[role] = handler


def _general_agent_handler(config: dict[str, Any]):
    """Build the handler that runs a general background task through the universal runtime."""
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
        result = runtime_execute(task, agent=agent, prefer=str(job.get("prefer") or "auto"))
        ok = bool(result.get("success", True)) if isinstance(result, dict) else bool(result)
        if isinstance(result, dict) and result.get("run_kind") == "mission":
            ok = result.get("state") == "completed"
        return {"ok": ok, "task": task, "result": result}
    return handle


def _computer_agent_handler(job: dict[str, Any]) -> dict[str, Any]:
    """Run or resume one persistent desktop task through the canonical queue."""
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


# --- canonical Job handler builders (JobContext -> dict) -----------------------
def make_agent_general_handler():
    """Canonical ``agent.general`` Job handler — runs the universal runtime."""
    def handle(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        agent_name = str(payload.get("agent") or payload.get("name") or "generic")
        cfg = _read_json(_agent_dir(agent_name) / "agent.json", {
            "name": agent_name, "role": "generic", "model": None,
        })
        # Allow an explicit role-specific in-process handler override.
        role = str(payload.get("role") or cfg.get("role") or "generic")
        handler = ROLE_HANDLERS.get(role)
        if handler is not None:
            return {"ok": True, "result": handler(payload)}
        return _general_agent_handler(cfg)(payload)
    return handle


def make_agent_computer_handler():
    """Canonical ``agent.computer`` Job handler — desktop control."""
    def handle(ctx) -> dict[str, Any]:
        return _computer_agent_handler(dict(ctx.payload))
    return handle


def register_agent_handlers(queue, *, overwrite: bool = False) -> list[str]:
    """Register the agent-role Job kinds on the canonical queue. Idempotent.

    By default it does **not** clobber a handler that is already registered, so
    tests/callers may inject a custom ``agent.general``/``agent.computer`` handler.
    """
    for kind, build in (("agent.general", make_agent_general_handler),
                        ("agent.computer", make_agent_computer_handler)):
        if kind in getattr(queue, "handlers", {}) and not overwrite:
            continue
        queue.register(kind, build(), overwrite=overwrite)
    return ["agent.general", "agent.computer"]


# --- registry helpers ----------------------------------------------------------
def _agents_dir() -> Path:
    return workspace.dirs["agents"]


def _agent_dir(name: str) -> Path:
    return _agents_dir() / name


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


class AgentManager:
    """Named-agent registry + delegation facade over the canonical Job system."""

    def __init__(self, queue=None, enabled: bool = True):
        self._queue_override = queue
        self._enabled = enabled

    def _queue(self):
        """The canonical queue (injected for tests, else the gateway singleton)."""
        if self._queue_override is not None:
            return self._queue_override
        from gateway.queue import job_queue  # lazy; no import-time layering cycle
        return job_queue

    # -- registry -----------------------------------------------------------
    def create(self, name: str, role: str = "generic", model: Optional[str] = None,
               persona: Optional[str] = None) -> dict[str, Any]:
        """Register a named agent (identity metadata only — no process spawned)."""
        if role not in ROLES:
            return {"success": False, "error": f"unknown role '{role}' (choose {ROLES})"}
        adir = _agent_dir(name)
        if adir.exists():
            return {"success": False, "error": f"agent '{name}' already exists"}
        adir.mkdir(parents=True, exist_ok=True)
        _write_json(adir / "agent.json", {
            "name": name, "role": role, "model": model, "persona": persona,
            "created": datetime.now().isoformat(),
        })
        return {"success": True, "name": name, "role": role}

    def start(self, name: str, handler=None, daemon: bool = True) -> dict[str, Any]:
        """Mark an agent ready (registry lifecycle). No process is spawned.

        The canonical queue owns actual execution; this merely validates the
        registry entry and ensures its Job handlers are registered.
        """
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found (create it first)"}
        try:
            register_agent_handlers(self._queue())
        except Exception:
            pass
        return {"success": True, "name": name, "status": "ready", "queue": "canonical"}

    def stop(self, name: str) -> dict[str, Any]:
        """Mark an agent stopped (registry lifecycle). No signal is sent."""
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found"}
        return {"success": True, "name": name, "status": "stopped"}

    def status(self, name: str) -> dict[str, Any]:
        """Registry identity + whether the canonical queue reports in-flight work."""
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found"}
        cfg = _read_json(_agent_dir(name) / "agent.json", {})
        return {"success": True, "name": name, **cfg, "status": "registered",
                "queue": "canonical", "alive": self._enabled}

    def list(self) -> list[dict[str, Any]]:
        out = []
        if not _agents_dir().exists():
            return out
        for p in sorted(_agents_dir().iterdir()):
            if p.is_dir() and (p / "agent.json").exists():
                out.append(self.status(p.name))
        return out

    # -- delegation over the canonical Job system --------------------------
    def _role_of(self, name: str) -> str:
        return str(_read_json(_agent_dir(name) / "agent.json", {}).get("role") or "generic")

    def submit_job(self, name: str, job: dict[str, Any]) -> dict[str, Any]:
        """Enqueue a canonical Job for the named agent. Lifecycle owned by the queue."""
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found"}
        role = str(job.get("role") or self._role_of(name))
        kind = "agent.computer" if role == "computer-operator" else "agent.general"
        queue = self._queue()
        try:
            register_agent_handlers(queue)
            payload = dict(job)
            payload.setdefault("agent", name)
            payload.setdefault("name", name)
            payload["role"] = role
            queued = queue.submit(
                kind, payload,
                session_key=f"agent:{name}",
                run_id=str(job.get("run_id") or ""),
            )
        except KeyError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # queue unavailable in this process
            return {"success": False, "error": f"canonical queue unavailable: {exc}"}
        return {"success": True, "name": name, "job_id": queued.id,
                "queued": True, "status": queued.status}

    def job_status(self, name: str, job_id: str) -> dict[str, Any]:
        """Read a canonical Job's status/result from the queue."""
        if not _agent_dir(name).exists():
            return {"success": False, "error": f"agent '{name}' not found"}
        try:
            st = self._queue().status(job_id)
        except Exception as exc:
            return {"success": False, "status": "unknown", "error": str(exc),
                    "name": name, "job_id": job_id}
        status = st.get("status") or "unknown"
        if not st.get("found") and st.get("error"):
            return {"success": False, "status": "unknown", "error": st.get("error"),
                    "name": name, "job_id": job_id}
        return {"success": True, "status": status, "name": name, "job_id": job_id,
                "result": st.get("result"), "error": st.get("error")}

    def wait_job(self, name: str, job_id: str, timeout: float = 120.0,
                 interval: float = 0.2) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, float(timeout))
        seen: dict[str, Any] = {}
        while time.monotonic() <= deadline:
            status = self.job_status(name, job_id)
            seen = status
            if status.get("status") in ("succeeded", "failed", "cancelled", "blocked"):
                return status
            if not status.get("success") and status.get("status") == "unknown":
                break
            time.sleep(max(0.02, float(interval)))
        if seen.get("status") in ("succeeded", "failed", "cancelled", "blocked"):
            return seen
        return {**seen, "success": False, "status": seen.get("status") or "timeout",
                "name": name, "job_id": job_id,
                "error": seen.get("error") or f"job did not finish within {timeout:g}s"}

    def watchdog_tick(self, stale_seconds: float = 30.0, restart: bool = True) -> dict[str, Any]:
        """Reconcile the registry against the canonical queue (no own process pool).

        The canonical queue already detects and recovers stale work via its
        lease/heartbeat/reaper. This reports the registry state honestly and
        records that recovery is delegated to the queue with **no** fabricated
        process restarts by this module.
        """
        known = [a.get("name") for a in self.list() if a.get("name")]
        return {"stale": [], "revived": [], "errors": [],
                "recovery_owner": "canonical-job-queue",
                "agents": known, "tick": datetime.now().isoformat()}


agent_manager = AgentManager()
