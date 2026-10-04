"""Bounded autonomous curiosity for HERMUS.

Personal Space is where HERMUS can spend quiet time when the owner has not
given it work. It may think, research from the context already available to
the runtime, and prepare ideas, but it cannot silently change the system.

The only background execution path is the canonical JobQueue -> runtime.turn
with read_only=True. Anything worth changing is stored as a proposal and
requires an explicit user approval before normal runtime execution is queued.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contracts import EventEnvelope
from .contracts.events import Actor, CommandSource, CommandStatus, EventType
from .events import get_bus


_FINAL_STATUSES = {"succeeded", "failed", "cancelled"}
_ACTIVE_STATUSES = {"queued", "running"}
_USER_SOURCES = {
    CommandSource.CLI.value,
    CommandSource.DASHBOARD.value,
    CommandSource.VOICE.value,
    CommandSource.CHANNEL.value,
}
_BUSY_STATES = {
    "thinking",
    "working",
    "verifying",
    "waiting_approval",
    "cancelling",
    "error",
}


def _now() -> float:
    return time.time()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


class PersonalSpace:
    """One bounded autonomous-curiosity loop with durable proposals."""

    def __init__(self, path: str | Path | None = None) -> None:
        from .config import config

        configured = path or getattr(config, "personal_space_state_path", "data/personal_space.json")
        self.path = Path(configured)
        if not self.path.is_absolute():
            self.path = Path(config.resolve_path(str(self.path)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._running = False
        self._task_job_id: str | None = None
        self._unsubscribe_bus: Any = None
        self._wired_bus: Any = None
        self._last_user_activity = _now()
        self._state: dict[str, Any] = {
            "enabled": True,
            "last_user_activity": self._last_user_activity,
            "current_job_id": None,
            "cycles": [],
            "proposals": [],
        }
        self._load()

    # ------------------------------------------------------------------ config
    @staticmethod
    def _settings() -> tuple[bool, int, int, int]:
        from .config import config

        enabled = bool(getattr(config, "personal_space_enabled", True))
        interval = max(30, int(getattr(config, "personal_space_interval_seconds", 900) or 900))
        idle_minutes = max(1, int(getattr(config, "personal_space_idle_minutes", 10) or 10))
        daily_cap = max(0, min(24, int(getattr(config, "personal_space_daily_cap", 4) or 4)))
        return enabled, interval, idle_minutes, daily_cap

    # ---------------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            return
        with self._lock:
            self._state["last_user_activity"] = float(raw.get("last_user_activity", self._last_user_activity) or self._last_user_activity)
            self._last_user_activity = self._state["last_user_activity"]
            self._state["current_job_id"] = raw.get("current_job_id")
            cycles = raw.get("cycles")
            proposals = raw.get("proposals")
            if isinstance(cycles, list):
                self._state["cycles"] = cycles[-50:]
            if isinstance(proposals, list):
                self._state["proposals"] = proposals[-50:]

    def _save(self) -> None:
        try:
            payload = dict(self._state)
            payload["last_user_activity"] = self._last_user_activity
            payload["current_job_id"] = self._task_job_id
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    # ------------------------------------------------------------------ events
    def _wire_bus(self) -> None:
        bus = get_bus()
        if self._wired_bus is bus and self._unsubscribe_bus is not None:
            return
        self._unwire_bus()

        def on_event(event: EventEnvelope) -> None:
            actor = str(getattr(event, "actor", "") or "")
            source = str(getattr(event, "source", "") or "")
            if actor == Actor.USER.value or source in _USER_SOURCES:
                self.note_user_activity()

        self._unsubscribe_bus = on_event
        self._wired_bus = bus
        bus.subscribe()(on_event)

    def _unwire_bus(self) -> None:
        if self._wired_bus is not None and self._unsubscribe_bus is not None:
            try:
                self._wired_bus.unsubscribe(self._unsubscribe_bus)
            except Exception:
                pass
        self._wired_bus = None
        self._unsubscribe_bus = None

    def note_user_activity(self, timestamp: float | None = None) -> None:
        with self._lock:
            self._last_user_activity = float(timestamp if timestamp is not None else _now())
            self._state["last_user_activity"] = self._last_user_activity
            self._save()

    def _emit(self, command: str, payload: dict[str, Any], *, status: str = CommandStatus.SUCCEEDED.value) -> None:
        try:
            get_bus().publish(
                EventEnvelope(
                    session_id="default",
                    actor=Actor.AGENT.value,
                    source=CommandSource.INTERNAL.value,
                    type=EventType.STATE_CHANGED.value,
                    command=command,
                    status=status,
                    args_redacted=payload,
                )
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ context
    def _context(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            from .focus_os import FocusOS

            result["focus"] = FocusOS().snapshot()
        except Exception as exc:
            result["focus"] = {"error": f"{type(exc).__name__}: {exc}"}
        try:
            from .learning_fabric import LearningFabric

            result["learning"] = LearningFabric().snapshot(limit=6)
        except Exception:
            result["learning"] = {}
        try:
            from .personal_os import personal_os

            snap = personal_os.snapshot()
            result["personal"] = {
                "focus": snap.get("focus"),
                "goals": (snap.get("goals") or [])[:6],
                "projects": (snap.get("projects") or [])[:6],
                "open_task_count": snap.get("open_task_count"),
                "relevant_memories": (snap.get("relevant_memories") or [])[:4],
            }
        except Exception:
            result["personal"] = {}
        # Keep the prompt deliberately bounded. Personal Space must stay cheap.
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        return json.loads(encoded[:10000]) if len(encoded) <= 10000 else {"context_truncated": True, "focus": result.get("focus", {})}

    def _attention_blocks(self) -> tuple[bool, str]:
        try:
            from .presence_kernel import presence_kernel

            snap = presence_kernel.snapshot(user_id="default", include_events=False)
            attention = snap.get("attention") or []
            critical = [x for x in attention if str(x.get("severity", "")).lower() in {"critical", "high"}]
            if critical:
                return True, "important attention is active"
            state = str((snap.get("summary") or {}).get("state") or "").lower()
            if state in _BUSY_STATES:
                return True, f"HERMUS is {state}"
        except Exception:
            # Unknown state is not permission to become active in the background.
            return True, "presence state is unavailable"
        return False, ""

    def _active_personal_job(self) -> dict[str, Any] | None:
        try:
            from gateway.queue import job_queue

            rows = job_queue.list_jobs(limit=50, session_key="personal-space")
        except Exception:
            return None
        for row in rows:
            if str(row.get("id") or "") == str(self._task_job_id or ""):
                return row
            if row.get("status") in _ACTIVE_STATUSES and str(row.get("kind") or "") == "runtime.turn":
                return row
        return None

    def _daily_cycles(self) -> int:
        day = _today()
        return sum(1 for row in self._state.get("cycles", []) if row.get("day") == day and row.get("started_at"))

    def _idle_seconds(self) -> float:
        return max(0.0, _now() - self._last_user_activity)

    def _curiosity_prompt(self) -> str:
        context = self._context()
        return (
            "You are HERMUS in Personal Space. The owner has not given you a new request. "
            "This is a bounded curiosity pass: think of one genuinely useful thing to investigate "
            "or prepare from the supplied context. You have NO tools in this turn. Do not claim to "
            "have researched, tested, changed, installed, measured, or verified anything unless it "
            "is explicitly present in the supplied context. Do not invent a user preference. Do not "
            "propose destructive or security-weakening actions. Prefer an idea that could save time, "
            "improve reliability, clarify a project, or prepare useful work for later. It must remain "
            "a proposal until the owner approves it. Return exactly one of these forms:\n\n"
            "NO_PROPOSAL\n"
            "or\n"
            "TITLE: <short title>\n"
            "CATEGORY: <research|improvement|organization|learning|project>\n"
            "CONFIDENCE: <0..1>\n"
            "SUMMARY: <what you noticed>\n"
            "WHY: <why this is worth the owner's attention>\n"
            "NEXT: <a concrete next action HERMUS could take after approval>\n\n"
            f"CURRENT CONTEXT:\n{json.dumps(context, ensure_ascii=False, default=str)[:10000]}"
        )

    # ------------------------------------------------------------------ parsing
    @staticmethod
    def _parse_proposal(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw or raw.startswith("NO_PROPOSAL"):
            return None
        values: dict[str, str] = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().upper()
            if key in {"TITLE", "CATEGORY", "CONFIDENCE", "SUMMARY", "WHY", "NEXT"}:
                values[key] = value.strip()
        if not values.get("TITLE") or not values.get("NEXT"):
            return None
        confidence = _clamp(values.get("CONFIDENCE"), 0.0, 1.0, 0.0)
        if confidence < 0.55:
            return None
        category = values.get("CATEGORY", "research").lower()
        if category not in {"research", "improvement", "organization", "learning", "project"}:
            category = "research"
        return {
            "title": values["TITLE"][:160],
            "category": category,
            "confidence": round(confidence, 2),
            "summary": values.get("SUMMARY", "")[:1200],
            "why": values.get("WHY", "")[:1200],
            "next_action": values["NEXT"][:1600],
        }

    # ---------------------------------------------------------------- lifecycle
    def harvest(self) -> dict[str, Any]:
        job_id = self._task_job_id
        if not job_id:
            return {"status": "idle", "harvested": False}
        try:
            from gateway.queue import job_queue

            rows = job_queue.list_jobs(limit=100, session_key="personal-space")
            row = next((r for r in rows if str(r.get("id") or "") == str(job_id)), None)
            if row is None:
                return {"status": "waiting", "job_id": job_id}
            status = str(row.get("status") or "")
            if status in _ACTIVE_STATUSES:
                return {"status": status, "job_id": job_id}
            if status not in _FINAL_STATUSES:
                return {"status": status or "unknown", "job_id": job_id}
            result = job_queue.result(job_id) if status == "succeeded" else None
        except Exception as exc:
            return {"status": "error", "job_id": job_id, "error": str(exc)[:300]}

        cycle = {"job_id": job_id, "finished_at": _now(), "status": status}
        proposal = None
        if status == "succeeded":
            response = str((result or {}).get("response") or (result or {}).get("final_answer") or "")
            parsed = self._parse_proposal(response)
            if parsed:
                proposal = {
                    "id": f"proposal_{uuid4().hex[:12]}",
                    **parsed,
                    "status": "pending",
                    "requires_approval": True,
                    "source_job_id": job_id,
                    "source_run_id": (result or {}).get("run_id"),
                    "created_at": _now(),
                }
                with self._lock:
                    self._state.setdefault("proposals", []).append(proposal)
                    self._state["proposals"] = self._state["proposals"][-50:]
                cycle["proposal_id"] = proposal["id"]
                self._emit(
                    "personal_space.proposal_created",
                    {"proposal_id": proposal["id"], "title": proposal["title"], "confidence": proposal["confidence"]},
                )
        if status != "succeeded":
            cycle["error"] = str((result or {}).get("error") or row.get("error") or status)[:300]
        with self._lock:
            self._state.setdefault("cycles", []).append(cycle)
            self._state["cycles"] = self._state["cycles"][-50:]
            self._task_job_id = None
            self._state["current_job_id"] = None
            self._save()
        self._emit("personal_space.cycle_finished", {"job_id": job_id, "status": status, "proposal_id": cycle.get("proposal_id")})
        return {"status": "harvested", "job_id": job_id, "proposal": proposal, "cycle": cycle}

    # ------------------------------------------------------------------ ticking
    def tick(self, *, force: bool = False) -> dict[str, Any]:
        self._wire_bus()
        harvested = self.harvest()
        enabled, _, idle_minutes, daily_cap = self._settings()
        if not enabled:
            return {"status": "disabled", "harvest": harvested}
        active = self._active_personal_job()
        if active:
            with self._lock:
                self._task_job_id = str(active.get("id") or self._task_job_id)
                self._state["current_job_id"] = self._task_job_id
                self._save()
            return {"status": "working", "job": active, "harvest": harvested}

        pending = [p for p in self._state.get("proposals", []) if p.get("status") == "pending"]
        if pending and not force:
            return {"status": "proposal_ready", "harvest": harvested, "proposal": pending[-1]}

        blocked, reason = self._attention_blocks()
        idle_for = self._idle_seconds()
        if not force and idle_for < idle_minutes * 60:
            return {"status": "waiting_for_idle", "idle_for_seconds": round(idle_for, 1), "harvest": harvested}
        if blocked:
            return {"status": "resting", "reason": reason, "harvest": harvested}
        if daily_cap and self._daily_cycles() >= daily_cap:
            return {"status": "daily_cap", "harvest": harvested, "daily_cycles": self._daily_cycles()}
        if not daily_cap:
            return {"status": "daily_cap", "harvest": harvested, "daily_cycles": 0}

        prompt = self._curiosity_prompt()
        try:
            from gateway.queue import job_queue

            job = job_queue.submit(
                "runtime.turn",
                {
                    "text": prompt,
                    "platform": "personal-space",
                    "user_id": "default",
                    "mode": "chat",
                    "prefer": "chat",
                    "read_only": True,
                    "stream": False,
                },
                session_key="personal-space",
                priority=9,
            )
        except Exception as exc:
            return {"status": "queue_error", "error": str(exc)[:300], "harvest": harvested}

        with self._lock:
            self._task_job_id = str(job.id)
            self._state["current_job_id"] = self._task_job_id
            self._state.setdefault("cycles", []).append(
                {
                    "job_id": self._task_job_id,
                    "day": _today(),
                    "started_at": _now(),
                    "status": "queued",
                    "forced": bool(force),
                }
            )
            self._state["cycles"] = self._state["cycles"][-50:]
            self._save()
        self._emit(
            "personal_space.cycle_started",
            {"job_id": job.id, "reason": "manual" if force else "idle", "daily_cycles": self._daily_cycles()},
            status=CommandStatus.RUNNING.value,
        )
        return {"status": "started", "job_id": job.id, "run_id": job.run_id, "forced": force}

    # ---------------------------------------------------------------- proposals
    def _find_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return next((p for p in self._state.get("proposals", []) if str(p.get("id")) == str(proposal_id)), None)

    def list_proposals(self, *, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._state.get("proposals", []))
        return rows[-max(1, min(50, int(limit))):][::-1]

    def approve(self, proposal_id: str) -> dict[str, Any]:
        with self._lock:
            proposal = self._find_proposal(proposal_id)
            if proposal is None:
                return {"success": False, "error": "proposal_not_found"}
            if proposal.get("status") != "pending":
                return {"success": False, "error": "proposal_not_pending", "proposal": dict(proposal)}
            action = str(proposal.get("next_action") or proposal.get("summary") or "").strip()
            if not action:
                return {"success": False, "error": "proposal_has_no_action"}
            proposal["status"] = "approved"
            proposal["approved_at"] = _now()
            self._save()

        try:
            from gateway.queue import job_queue

            job = job_queue.submit(
                "runtime.turn",
                {
                    "text": action,
                    "platform": "personal-space-approved",
                    "user_id": "default",
                    "session_id": "personal-space-approved",
                    "prefer": "auto",
                    "read_only": False,
                    "stream": True,
                    "personal_space_proposal_id": proposal_id,
                },
                session_key="personal-space:approved",
                priority=1,
            )
        except Exception as exc:
            with self._lock:
                proposal = self._find_proposal(proposal_id)
                if proposal is not None:
                    proposal["status"] = "pending"
                    proposal.pop("approved_at", None)
                    self._save()
            return {"success": False, "error": str(exc)[:300]}

        with self._lock:
            proposal = self._find_proposal(proposal_id)
            if proposal is not None:
                proposal["status"] = "queued"
                proposal["approval_job_id"] = job.id
                proposal["approval_run_id"] = job.run_id
                self._save()
        self._emit("personal_space.proposal_approved", {"proposal_id": proposal_id, "job_id": job.id})
        return {"success": True, "proposal": dict(proposal or {}), "job_id": job.id, "run_id": job.run_id}

    def dismiss(self, proposal_id: str) -> dict[str, Any]:
        with self._lock:
            proposal = self._find_proposal(proposal_id)
            if proposal is None:
                return {"success": False, "error": "proposal_not_found"}
            if proposal.get("status") != "pending":
                return {"success": False, "error": "proposal_not_pending", "proposal": dict(proposal)}
            proposal["status"] = "dismissed"
            proposal["dismissed_at"] = _now()
            self._save()
        self._emit("personal_space.proposal_dismissed", {"proposal_id": proposal_id})
        return {"success": True, "proposal": dict(proposal)}

    # ---------------------------------------------------------------- snapshot
    def snapshot(self, *, limit: int = 8) -> dict[str, Any]:
        enabled, interval, idle_minutes, daily_cap = self._settings()
        active = self._active_personal_job()
        pending = [p for p in self._state.get("proposals", []) if p.get("status") == "pending"]
        with self._lock:
            history = list(self._state.get("cycles", []))[-10:]
        blocked, reason = self._attention_blocks()
        idle_for = self._idle_seconds()
        if not enabled:
            status = "disabled"
        elif active:
            status = "curious"
        elif pending:
            status = "proposal_ready"
        elif blocked:
            status = "resting"
        elif idle_for < idle_minutes * 60:
            status = "waiting"
        elif daily_cap and self._daily_cycles() >= daily_cap:
            status = "daily_cap"
        else:
            status = "ready"

        return {
            "enabled": enabled,
            "status": status,
            "current_job_id": self._task_job_id,
            "running": bool(self._running),
            "idle_for_seconds": round(idle_for, 1),
            "idle_threshold_seconds": idle_minutes * 60,
            "daily_cycles": self._daily_cycles(),
            "daily_cap": daily_cap,
            "interval_seconds": interval,
            "attention_blocked": blocked,
            "attention_reason": reason,
            "pending_proposals": len(pending),
            "proposals": self.list_proposals(limit=limit),
            "history": history,
            "last_user_activity": self._last_user_activity,
        }

    # ---------------------------------------------------------------- background
    async def run(self) -> None:
        _, interval, _, _ = self._settings()
        self._wire_bus()
        self._running = True
        try:
            while True:
                try:
                    self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._emit("personal_space.loop_error", {"error": f"{type(exc).__name__}: {exc}"}, status=CommandStatus.FAILED.value)
                await asyncio.sleep(interval)
                _, interval, _, _ = self._settings()
        finally:
            self._running = False
            self._unwire_bus()

    def stop(self) -> None:
        self._running = False
        self._unwire_bus()


personal_space = PersonalSpace()

__all__ = ["PersonalSpace", "personal_space"]
