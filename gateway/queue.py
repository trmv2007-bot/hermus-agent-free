"""Async gateway job queue — the FastAPI layer never runs an agent inline.

Problem: ``POST /command`` used to ``await asyncio.to_thread(agent.chat(...))``,
so one webhook = one blocked request for as long as the tool loop takes (minutes,
for a research-heavy task). Telegram retries, Discord reconnects, the dashboard
spinner hangs, and N concurrent users need N busy threads with no cap.

This module decouples intake from execution:

* ``submit()`` returns a job id **immediately** (p99 intake = microseconds).
* Workers run the actual agent turn in a bounded thread pool.
* **Session lanes**: jobs for the same ``platform:user`` run in submission order
  (an agent's memory must not interleave), while different users run in
  parallel — bounded by ``HERMUS_QUEUE_WORKERS`` so the box stays responsive.
* Every state change is published to the run bus (SSE/WebSocket) and appended to
  a durable JSONL log, so a restart can show what it lost instead of silently
  dropping work.
* Cooperative cancellation + per-job timeout: a cancelled/timed-out job tells the
  agent loop to stop at its next step boundary (``should_cancel``) rather than
  being abandoned in a thread that outlives the request.
* Optional Redis Streams transport (``gateway_queue_backend=redis``) for
  multi-process workers; falls back to in-process lanes when redis is absent.

Handlers are registered by kind: ``runtime.turn`` (canonical, auto-classified
chat-or-mission), ``agent.chat``, ``agent.autonomous``, ``mission.start``,
``swe.develop``, ``research.deep``, ``subagent.delegate``, ``channel.reply``,
``memory.sweep`` … so new async work never needs a new endpoint. All
work kinds execute through the universal mission runtime (``core.runtime``).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable

from core.config import config
from core.contracts.jobs import Job as _ContractJob
from core.run_events import RunBus, run_bus

JobHandler = Callable[["JobContext"], Any]

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_INTERRUPTED = "interrupted"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Job(_ContractJob):
    """One job = one canonical ``core.contracts.jobs.Job`` plus queue runtime state.

    The durable/lease/recovery fields come from the single §14 Job contract
    (``type``, ``attempt``, ``idempotency_key``, ``lease_owner``, ``heartbeat_at``,
    ``next_run_at``, ``mission_id``, ``result_ref`` …). The queue only adds the
    operational fields it needs to schedule and report. The legacy queue attribute
    names (``kind``, ``attempts``, ``dedupe_key``, ``error``) are kept as aliases
    so the live realtime/SSE consumers and the internal executor stay unchanged.
    """

    session_key: str = ""
    run_id: str = ""
    max_attempts: int = 1
    timeout: float = 0.0
    # wall-clock epoch used for timing; the contract's ``created_at`` stays the
    # canonical durable ISO timestamp.
    created_ts: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    created: str = field(default_factory=_now)

    # -- legacy queue attribute aliases (map onto the canonical contract) -----
    @property
    def kind(self) -> str:
        return self.type

    @kind.setter
    def kind(self, value: str) -> None:
        self.type = value

    @property
    def attempts(self) -> int:
        return self.attempt

    @attempts.setter
    def attempts(self, value: int) -> None:
        self.attempt = value

    @property
    def error(self) -> str:
        return self.error_message or ""

    @error.setter
    def error(self, value: str) -> None:
        self.error_message = value

    @property
    def dedupe_key(self) -> str:
        return self.idempotency_key or ""

    @dedupe_key.setter
    def dedupe_key(self, value: str) -> None:
        self.idempotency_key = value

    def brief(self) -> dict[str, Any]:
        # Explicit dict, NOT asdict(): this preserves the exact external shape the
        # realtime/SSE + route consumers already read, regardless of subclassing.
        d: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "status": self.status,
            "session_key": self.session_key,
            "run_id": self.run_id,
            "priority": self.priority,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "timeout": self.timeout,
            "created_at": self.created_ts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "dedupe_key": self.dedupe_key,
            "created": self.created,
        }
        d["has_result"] = self.result is not None
        if self.started_at and self.finished_at:
            d["elapsed_ms"] = int((self.finished_at - self.started_at) * 1000)
        elif self.started_at:
            d["elapsed_ms"] = int((time.time() - self.started_at) * 1000)
        else:
            d["elapsed_ms"] = None
        d["duration_ms"] = int(((self.finished_at or time.time()) - (self.started_at or self.created_ts)) * 1000)
        d["wait_ms"] = int(((self.started_at or time.time()) - self.created_ts) * 1000)
        return d


class JobContext:
    """What a handler gets: the payload, an emitter, and cancellation."""

    def __init__(self, job: Job, bus: RunBus, queue: "JobQueue"):
        self.job = job
        self._bus = bus
        self._queue = queue
        self.id = job.id
        self.kind = job.kind
        self.payload = job.payload
        self.run_id = job.run_id

    @property
    def emit(self) -> Callable[[str, dict[str, Any]], None]:
        return self._queue.emit_for(self.job)

    def should_cancel(self) -> bool:
        return self._bus.is_cancelled(self.run_id) or self.job.status == STATUS_CANCELLED

    def pending_steers(self) -> list[str]:
        """Drain mid-run steering instructions queued via POST /run/steer."""
        return self._bus.pending_steers(self.run_id)

    def log(self, message: str, level: str = "info") -> None:
        self.emit("log", {"level": level, "message": str(message)[:1000]})


class Lane:
    """One serialized FIFO of jobs (normally one per platform:user)."""

    def __init__(self, key: str):
        self.key = key
        self.pending: deque[Job] = deque()
        self.running = False
        self.task: Optional[asyncio.Task] = None


class JobQueue:
    def __init__(
        self,
        *,
        workers: Optional[int] = None,
        maxsize: Optional[int] = None,
        default_timeout: Optional[float] = None,
        bus: Optional[RunBus] = None,
        persist: Optional[str] = None,
        backend: Optional[str] = None,
    ):
        self.workers = int(workers if workers is not None else getattr(config, "gateway_queue_workers", 4))
        self.maxsize = int(maxsize if maxsize is not None else getattr(config, "gateway_queue_maxsize", 500))
        self.default_timeout = float(
            default_timeout if default_timeout is not None else getattr(config, "gateway_queue_timeout", 300)
        )
        self.bus = bus or run_bus
        self.enabled = bool(getattr(config, "gateway_queue_enabled", True))
        self.backend = str(backend or getattr(config, "gateway_queue_backend", "inprocess")).lower()
        self.handlers: dict[str, JobHandler] = {}
        self.jobs: dict[str, Job] = {}
        self._order: deque[str] = deque(maxlen=self.maxsize)
        self._lanes: dict[str, Lane] = {}
        self._recent_keys: dict[str, str] = {}      # dedupe key -> job id
        self._sem: Optional[asyncio.Semaphore] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = False
        self._stopped = False
        self.retry_backoff = float(getattr(config, "gateway_queue_retry_backoff", 1.5) or 1.5)
        self.cancel_grace = float(getattr(config, "gateway_queue_cancel_grace", 15) or 15)
        self.persist_path = Path(
            persist or config.resolve_path(str(getattr(config, "gateway_jobs_log", "data/jobs/jobs.jsonl")))
        )
        self.results_dir = self.persist_path.parent / "results"
        self.stats_counts = {"submitted": 0, "succeeded": 0, "failed": 0, "cancelled": 0, "retried": 0,
                             "rejected": 0}
        self._redis = None

    # ------------------------------------------------------------------ wiring
    def register(self, kind: str, handler: JobHandler, *, overwrite: bool = True) -> None:
        if kind in self.handlers and not overwrite:
            return
        self.handlers[kind] = handler

    def handler(self, kind: str) -> Optional[JobHandler]:
        return self.handlers.get(kind)

    async def start(self) -> dict[str, Any]:
        """Bind to the running loop; recover the durable log; spin up lanes on demand."""
        if not self.enabled:
            info = self.status()
            info["note"] = "queue disabled by config — /command and webhooks run inline"
            print("[Queue] disabled (gateway_queue_enabled=0) — inline execution")
            return info
        self._loop = asyncio.get_running_loop()
        self._sem = asyncio.Semaphore(max(1, self.workers))
        self._started = True
        self._stopped = False
        recovered = self._load_durable_log()
        if self.backend == "redis":
            try:
                from gateway.redis_backend import RedisStreamsConsumer, redis_available

                ok, detail = redis_available()
                if not ok:
                    print(f"[Queue] redis backend unavailable ({detail}) → in-process lanes")
                    self._redis = None
                else:
                    self._redis = RedisStreamsConsumer(self)
                    info = await self._redis.start()
                    print(f"[Queue] redis streams attached ({detail}) {info or ''}")
            except Exception as e:
                print(f"[Queue] redis backend failed ({e}) → in-process lanes")
                self._redis = None
        info = self.status()
        info["recovered"] = recovered
        print(
            f"[Queue] workers={self.workers} timeout={self.default_timeout}s "
            f"backend={self.backend} enabled={self.enabled}"
        )
        return info

    async def stop(self, drain_timeout: float = 5.0) -> None:
        self._stopped = True
        if self._redis is not None:
            try:
                await self._redis.stop()
            except Exception:
                pass
        pending = [t for lane in self._lanes.values() if lane.task for t in [lane.task] if not t.done()]
        if pending and drain_timeout > 0:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=drain_timeout)
            except asyncio.TimeoutError:
                for t in pending:
                    t.cancel()
        self._started = False

    # ------------------------------------------------------------------ intake
    def emit_for(self, job: Job) -> Callable[[str, dict[str, Any]], None]:
        def emit(event_type: str, data: Optional[dict[str, Any]] = None) -> None:
            try:
                payload = dict(data or {})
                payload.setdefault("job_id", job.id)
                self.bus.publish(job.run_id, event_type, payload)
                self._record({"job_id": job.id, "event": event_type, "data": _trim(payload)})
            except Exception:
                pass

        return emit

    def submit(
        self,
        kind: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        session_key: str = "",
        priority: int = 0,
        timeout: Optional[float] = None,
        max_attempts: Optional[int] = None,
        dedupe_key: str = "",
        job_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Job:
        """Enqueue a job. Synchronous on purpose: FastAPI handlers, channel
        callbacks and the CLI all share one entry point, and nothing here blocks."""
        payload = dict(payload or {})
        if kind not in self.handlers:
            raise KeyError(f"no handler registered for job kind '{kind}' "
                           f"(known: {sorted(self.handlers)})")
        if len(self.jobs) >= self.maxsize * 2:
            # keep memory bounded; oldest finished jobs are dropped first
            self._evict_old()
        session_key = session_key or str(payload.get("session_key") or "default")
        job = Job(
            id=job_id or f"job_{uuid.uuid4().hex[:10]}",
            type=kind,
            payload=payload,
            session_key=session_key,
            run_id=run_id or f"run_{uuid.uuid4().hex[:8]}",
            priority=int(priority),
            timeout=float(timeout if timeout is not None else self.default_timeout),
            max_attempts=max(1, int(max_attempts if max_attempts is not None else 1)),
            idempotency_key=dedupe_key,
        )
        if dedupe_key and dedupe_key in self._recent_keys:
            existing_id = self._recent_keys[dedupe_key]
            existing = self.jobs.get(existing_id)
            if existing and existing.status in (STATUS_QUEUED, STATUS_RUNNING):
                self._record({"job_id": job.id, "event": "deduped", "into": existing_id})
                return existing
        self.jobs[job.id] = job
        self._order.append(job.id)
        if dedupe_key:
            self._recent_keys[dedupe_key] = job.id
        self.stats_counts["submitted"] += 1
        self.bus.start(job.run_id, label=f"{kind}:{job.id}")
        self._record({"job_id": job.id, "event": "queued", "kind": kind,
                      "session_key": session_key, "run_id": job.run_id, "data": _trim(payload)})
        self.bus.publish(job.run_id, "job_queued", {"job_id": job.id, "kind": kind,
                                                     "session_key": session_key})
        self._kick(job)
        return job

    def _kick(self, job: Job) -> None:
        lane = self._lanes.get(job.session_key)
        if lane is None:
            lane = Lane(job.session_key)
            self._lanes[job.session_key] = lane
        if job.priority:
            # priority > 0 → insert ahead of the FIFO tail but after running work
            insert_at = len(lane.pending)
            while insert_at > 0 and lane.pending[insert_at - 1].priority < job.priority:
                insert_at -= 1
            lane.pending.insert(insert_at, job)
        else:
            lane.pending.append(job)
        if not lane.running and self._started and self._loop is not None:
            lane.running = True
            lane.task = self._loop.create_task(self._drain_lane(lane))

    def _lane_ready(self) -> bool:
        return self._started and self._loop is not None and not self._stopped

    async def _drain_lane(self, lane: Lane) -> None:
        try:
            while lane.pending:
                if self._stopped:
                    for job in list(lane.pending):
                        lane.pending.remove(job)
                        self._finalize(job, STATUS_CANCELLED, error="gateway shutting down")
                    break
                job = lane.pending.popleft()
                if self._sem is not None:
                    await self._sem.acquire()
                try:
                    await self._execute(job)
                finally:
                    if self._sem is not None:
                        self._sem.release()
        finally:
            lane.running = False
            if not lane.pending:
                self._lanes.pop(lane.key, None)

    # --------------------------------------------------------------- execution
    async def _execute(self, job: Job) -> None:
        handler = self.handlers.get(job.kind)
        job.status = STATUS_RUNNING
        job.attempts += 1                      # attempts == times executed (1-based)
        job.started_at = time.time()
        ctx = JobContext(job, self.bus, self)
        self.bus.publish(job.run_id, "job_started", {"job_id": job.id, "kind": job.kind,
                                                      "attempt": job.attempts,
                                                      "max_attempts": job.max_attempts})
        self._record({"job_id": job.id, "event": "started", "attempt": job.attempts})
        try:
            if handler is None:
                raise KeyError(f"handler for '{job.kind}' disappeared")
            # Exactly one invocation per attempt — a tool loop must never run twice.
            # Async handlers run on the loop; blocking handlers run in the pool so
            # the gateway stays responsive while a job is mid-tool-call.
            if inspect.iscoroutinefunction(handler):
                pending = asyncio.ensure_future(_call_with_context(handler, ctx))
                result = await (asyncio.wait_for(pending, timeout=job.timeout)
                                if job.timeout else pending)
            else:
                fut = self._loop.run_in_executor(None, _call_with_context, handler, ctx)
                if job.timeout:
                    try:
                        result = await asyncio.wait_for(asyncio.shield(fut), timeout=job.timeout)
                    except asyncio.TimeoutError:
                        # Ask politely first: the handler polls ctx.should_cancel()
                        # (and the agent loop polls the run bus) and unwinds itself.
                        ctx.emit("cancel_requested", {"reason": f"job timeout after {job.timeout}s"})
                        self.bus.cancel(job.run_id)
                        try:
                            result = await asyncio.wait_for(fut, timeout=self.cancel_grace)
                        except asyncio.CancelledError:
                            raise
                        except Exception as inner:
                            raise TimeoutError(
                                f"job exceeded {job.timeout}s and stopped with: {type(inner).__name__}: {inner}"
                            ) from inner
                else:
                    result = await fut
            if not isinstance(result, dict):
                result = {"result": result}
            job.result = result
            self._finalize(job, STATUS_DONE)
        except asyncio.CancelledError:
            self._finalize(job, STATUS_CANCELLED, error="cancelled")
            raise
        except Exception as e:
            retriable = (job.attempts < max(1, job.max_attempts)
                         and not isinstance(e, _NO_RETRY_ERRORS))
            if retriable:
                self.stats_counts["retried"] += 1
                delay = min(30.0, self.retry_backoff ** job.attempts)
                self.bus.publish(job.run_id, "job_retry", {"job_id": job.id, "error": str(e)[:400],
                                                            "attempt": job.attempts, "in": delay})
                self._record({"job_id": job.id, "event": "retry", "error": str(e)[:400]})
                job.status = STATUS_QUEUED
                if self._loop is not None:
                    self._loop.call_later(delay, self._requeue, job)
                return
            self._finalize(job, STATUS_FAILED, error=f"{type(e).__name__}: {e}")
        except BaseException as e:  # noqa: BLE001 - never let a lane die silently
            self._finalize(job, STATUS_FAILED, error=f"{type(e).__name__}: {e}")

    def _requeue(self, job: Job) -> None:
        if self._stopped:
            self._finalize(job, STATUS_INTERRUPTED, error="shutdown before retry")
            return
        self._kick(job)

    def _finalize(self, job: Job, status: str, result: Optional[dict[str, Any]] = None,
                  error: str = "") -> None:
        job.status = status
        job.finished_at = time.time()
        if result is not None:
            job.result = result
        if error:
            job.error = str(error)[:2000]
        if status == STATUS_DONE:
            self.stats_counts["succeeded"] += 1
        elif status == STATUS_FAILED:
            self.stats_counts["failed"] += 1
        elif status == STATUS_CANCELLED:
            self.stats_counts["cancelled"] += 1
        self._persist_result(job)
        self._record({"job_id": job.id, "event": "finished", "status": status,
                      "error": job.error[:500], "duration_ms": job.brief()["duration_ms"]})
        self.bus.publish(
            job.run_id, "job_finished",
            {"job_id": job.id, "status": status, "error": job.error[:500],
             "duration_ms": job.brief()["duration_ms"],
             "result_preview": _trim(job.result or {})},
        )
        try:
            self.bus.finish(job.run_id, "finished" if status == STATUS_DONE else status,
                            result=job.result, error=job.error if status == STATUS_FAILED else "")
        except Exception:
            pass

    # ------------------------------------------------------------------ control
    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            return {"cancelled": False, "error": f"unknown job '{job_id}'"}
        if job.status in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED):
            return {"cancelled": False, "job_id": job_id, "status": job.status,
                    "reason": "already terminal"}
        if job.status == STATUS_QUEUED and job in (self._lanes.get(job.session_key).pending
                                                   if self._lanes.get(job.session_key) else []):
            lane = self._lanes[job.session_key]
            try:
                lane.pending.remove(job)
            except ValueError:
                pass
            self._finalize(job, STATUS_CANCELLED, error="cancelled before start")
            return {"cancelled": True, "job_id": job_id, "stage": "queued"}
        self.bus.cancel(job.run_id)
        self.bus.publish(job.run_id, "cancel_requested", {"job_id": job_id})
        return {"cancelled": True, "job_id": job_id, "stage": "cooperative",
                "note": "agent will stop at its next step boundary"}

    def status(self, job_id: Optional[str] = None) -> dict[str, Any]:
        if job_id:
            job = self.jobs.get(job_id)
            if not job:
                recovered = self._lookup_logged_job(job_id)
                if recovered is not None:
                    recovered["found"] = True
                    return recovered
                # NOTE: ``found`` is explicit on purpose. Every Job.brief()
                # carries an ``error`` field (empty string on success), so a
                # caller cannot use `"error" in payload` to detect an unknown
                # job — that mistake made /jobs/{id} answer 404 for jobs that
                # had already succeeded, breaking the dashboard's poll loop.
                return {"error": f"unknown job '{job_id}'", "job_id": job_id,
                        "found": False}
            out = job.brief()
            out["found"] = True
            out["result"] = _trim(job.result or {}, limit=6000) if job.status == STATUS_DONE else None
            out["result_path"] = str(self.results_dir / f"{job.id}.json") if job.status == STATUS_DONE else None
            out["events"] = self.bus.history(job.run_id, limit=20)
            out["run"] = self.bus.snapshot(job.run_id)
            return out
        by_status: dict[str, int] = {}
        for j in self.jobs.values():
            by_status[j.status] = by_status.get(j.status, 0) + 1
        return {
            "found": True,
            "enabled": self.enabled,
            "started": self._started,
            "backend": self.backend,
            "workers": self.workers,
            "maxsize": self.maxsize,
            "default_timeout": self.default_timeout,
            "registered_kinds": sorted(self.handlers),
            "by_status": by_status,
            "queues": {k: len(v.pending) for k, v in self._lanes.items() if v.pending},
            "stats": self.stats_counts,
            "log": str(self.persist_path),
        }

    def list_jobs(self, *, limit: int = 50, status: Optional[str] = None,
                  session_key: Optional[str] = None) -> list[dict[str, Any]]:
        rows = [self.jobs[i].brief() for i in reversed(list(self._order)) if i in self.jobs]
        if status:
            rows = [r for r in rows if r["status"] == status]
        if session_key:
            rows = [r for r in rows if r.get("session_key") == session_key]
        return rows[: max(1, int(limit))]

    def events(self, job_id: str, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        job = self.jobs.get(job_id)
        if not job:
            return []
        return self.bus.history(job.run_id, after=after, limit=limit)

    def _lookup_logged_job(self, job_id: str) -> Optional[dict[str, Any]]:
        """Answer for a job from a previous process (read from the durable log)."""
        try:
            if not self.persist_path.exists():
                return None
            found: Optional[dict[str, Any]] = None
            for line in reversed(self.persist_path.read_text(errors="ignore").splitlines()[-4000:]):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("job_id") != job_id:
                    continue
                if found is None:
                    found = dict(rec)
                    found.setdefault("status", "running")
                if rec.get("event") == "finished":
                    found["status"] = rec.get("status") or "succeeded"
                    found["error"] = rec.get("error") or ""
                    found["duration_ms"] = rec.get("duration_ms")
                    break
                if rec.get("event") == "started":
                    found["status"] = "interrupted"
            if found is None:
                return None
            result_file = self.results_dir / f"{job_id}.json"
            found["job_id"] = job_id
            found["recovered"] = True
            if result_file.exists():
                try:
                    found["result"] = _trim(json.loads(result_file.read_text()), limit=4000)
                except Exception:
                    pass
            found.setdefault("payload", {})
            found["result_path"] = str(result_file) if result_file.exists() else None
            return found
        except Exception:
            return None

    def result(self, job_id: str) -> Optional[dict[str, Any]]:
        job = self.jobs.get(job_id)
        if job and job.result is not None:
            return job.result
        path = self.results_dir / f"{job_id}.json"
        if path.exists():
            try:
                stored = json.loads(path.read_text())
            except Exception:
                return None
            # the on-disk record is a small envelope; callers want the payload
            if isinstance(stored, dict) and "result" in stored and stored.get("job_id") == job_id:
                return stored["result"]
            return stored
        return None

    # ---------------------------------------------------------------- durability
    def _record(self, line: dict[str, Any]) -> None:
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": _now(), **line}, default=str) + "\n")
        except Exception:
            pass

    def _persist_result(self, job: Job) -> None:
        if job.result is None:
            return
        try:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            (self.results_dir / f"{job.id}.json").write_text(
                json.dumps({"job_id": job.id, "kind": job.kind, "status": job.status,
                            "finished": _now(), "result": job.result}, default=str, indent=2)
            )
        except Exception:
            pass

    def set_log_path(self, path) -> Path:
        """Relocate the durable log + results dir (tests, or moving state off a small disk)."""
        self.persist_path = Path(path)
        self.results_dir = self.persist_path.parent / "results"
        try:
            self.results_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return self.persist_path

    @staticmethod
    def read_records(path, tail: int = 4000) -> tuple[dict[str, dict[str, Any]], list[str], int]:
        """Fold the append-only log into ``({job_id: latest record}, submission order, lines seen)``.

        Shared by restart recovery and the CLI, so both agree on what a job's
        state was — including jobs that finished in another process.
        """
        latest: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        path = Path(path)
        if not path.exists():
            return latest, order, 0
        try:
            lines = path.read_text(errors="ignore").splitlines()[-max(1, int(tail)):]
        except Exception:
            return latest, order, 0
        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            jid = rec.get("job_id")
            if not jid:
                continue
            if jid not in latest:
                order.append(jid)
            prev = latest.get(jid) or {}
            event = rec.get("event")
            merged = {**prev, **rec}
            if event == "queued":
                merged["status"] = STATUS_QUEUED
            elif event == "started":
                merged["status"] = STATUS_RUNNING
            elif event == "retry":
                merged["status"] = STATUS_QUEUED
            elif event == "deduped":
                merged["status"] = prev.get("status") or STATUS_QUEUED
                merged["deduped_into"] = rec.get("into")
            elif event == "finished":
                merged["status"] = rec.get("status") or STATUS_DONE
                merged["duration_ms"] = rec.get("duration_ms")
            latest[jid] = merged
        return latest, order, len(lines)

    def read_log(self, path=None, limit: int = 50, *, include_live: bool = True) -> list[dict[str, Any]]:
        """Log-shaped rows, newest first, merging disk history with live state."""
        latest, _order = self.read_records(Path(path) if path else self.persist_path)[:2]
        rows: dict[str, dict[str, Any]] = dict(latest)
        if include_live:
            for jid, job in self.jobs.items():
                rows[jid] = {**rows.get(jid, {}), **job.brief(),
                             "finished": job.finished_at or 0.0,
                             "result_brief": _brief_result(job.result)}
        out = sorted(rows.values(), key=_row_time, reverse=True)
        return out[:max(1, int(limit))]

    def _load_durable_log(self, *, replay_limit: int = 50) -> dict[str, Any]:
        """Rehydrate recent jobs from the append-only log after a restart.

        Two things this buys: a job that was running when the process died is
        reported as ``interrupted`` instead of pretending to still be in flight,
        and ``hermus jobs list`` / ``GET /jobs`` still show history after a restart.
        """
        latest, order, seen = self.read_records(self.persist_path)
        if not latest:
            return {"seen": 0, "interrupted": 0, "recovered": 0, "jobs": 0}

        interrupted = 0
        recovered = 0
        for jid in order[-replay_limit:]:
            rec = latest[jid]
            status = rec.get("status") or STATUS_INTERRUPTED
            if status in (STATUS_QUEUED, STATUS_RUNNING):
                status = STATUS_INTERRUPTED
                interrupted += 1
            if jid in self.jobs:
                continue
            job = Job(id=jid, type=str(rec.get("kind") or "unknown"),
                      session_key=str(rec.get("session_key") or "default"),
                      run_id=str(rec.get("run_id") or jid), status=status,
                      error_message=str(rec.get("error") or "") or
                            ("interrupted by gateway restart" if status == STATUS_INTERRUPTED else ""))
            self.jobs[job.id] = job
            self._order.append(job.id)
            recovered += 1
        return {"seen": seen, "interrupted": interrupted, "recovered": recovered,
                "jobs": len(latest)}

    def _evict_old(self) -> None:
        keep_running = True
        for _ in range(200):
            victim = None
            for jid in list(self._order)[:]:
                job = self.jobs.get(jid)
                if job and job.status in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED):
                    victim = jid
                    break
            if victim is None:
                break
            self._order.popleft() if self._order and self._order[0] == victim else self._order.remove(victim)
            job = self.jobs.pop(victim, None)
            if job and job.dedupe_key:
                self._recent_keys.pop(job.dedupe_key, None)
            _ = keep_running
        self.stats_counts["rejected"] += 1

    def recent_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Most recent jobs (any status), log-shaped for the CLI and ``GET /jobs``.

        Live jobs first, then whatever the durable log remembers — a restart must
        not make the queue look empty.
        """
        out: list[dict[str, Any]] = []
        seen: set = set()
        for jid in reversed(list(self._order)):
            job = self.jobs.get(jid)
            if job is None:
                continue
            d = job.brief()
            d["finished"] = job.finished_at or 0.0
            d["result_brief"] = _brief_result(job.result)
            out.append(d)
            seen.add(jid)
            if len(out) >= max(1, int(limit)):
                return out
        for row in self.read_log(limit=max(1, int(limit)) * 2):
            if row.get("job_id") in seen:
                continue
            out.append(row)
            if len(out) >= max(1, int(limit)):
                break
        return out[:max(1, int(limit))]

    def recent_completed(self, limit: int = 10) -> list[dict[str, Any]]:
        return [row for row in self.recent_jobs(limit * 3) if row.get("status") == STATUS_DONE][:limit]


class CancelledError_(Exception):
    """Marker handlers can raise to stop a job without triggering a retry."""


try:  # the agent loop raises this when a run is cancelled mid-step
    from core.run_hooks import CancelledRun as _CancelledRun
except Exception:  # pragma: no cover
    _CancelledRun = None

_NO_RETRY_ERRORS: tuple = tuple(
    e for e in (asyncio.CancelledError, CancelledError_, _CancelledRun) if e is not None
)


def _row_time(row: dict[str, Any]) -> float:
    """Sortable timestamp for a log row: epoch floats and ISO strings both occur.

    Live jobs carry ``time.time()`` floats, the JSONL log carries ISO-8601 — a
    restart merges the two, so sorting must not assume one shape.
    """
    from datetime import datetime

    for key in ("finished", "finished_at", "created", "created_at", "ts", "at"):
        value = row.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except Exception:
            try:
                return float(value)
            except Exception:
                continue
    return 0.0


def _call_with_context(handler: JobHandler, ctx: JobContext) -> Any:
    """Run a sync handler in a worker thread; supports ``emit=`` kwarg handlers."""
    try:
        sig = inspect.signature(handler)
        params = sig.parameters
    except (TypeError, ValueError):
        params = {}
    if "emit" in params and "ctx" not in params:
        return handler(ctx.payload, emit=ctx.emit)
    if "ctx" in params:
        return handler(ctx)
    if params and next(iter(params.values())).kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY
    ):
        first = next(iter(params.values()))
        if first.name in ("payload", "job"):
            return handler(ctx.payload)
    return handler(ctx)


def _trim(data: Any, limit: int = 1500) -> Any:
    """Shrink big payloads for logs/events without losing the shape."""
    if isinstance(data, str):
        return data[:limit] + ("…[truncated]" if len(data) > limit else "")
    if isinstance(data, dict):
        out = {}
        for k, v in list(data.items())[:24]:
            if isinstance(v, (dict, list)):
                txt = json.dumps(v, default=str)
                out[k] = _trim(v, limit // 2) if len(txt) > limit else v
            elif isinstance(v, str) and len(v) > limit:
                out[k] = _trim(v, limit)
            else:
                out[k] = v
        return out
    if isinstance(data, list):
        head = [_trim(x, limit // 2) for x in data[:8]]
        if len(data) > 8:
            head.append(f"…{len(data) - 8} more")
        return head
    return data


def _brief_result(result: Optional[dict[str, Any]], limit: int = 160) -> str:
    """One-line summary of a job result for `hermus jobs list` / listings."""
    if not isinstance(result, dict):
        return str(result or "")[:limit]
    for key in ("response", "answer", "final_answer", "summary", "text", "error"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return " ".join(val.split())[:limit]
    bits = []
    if result.get("steps") is not None:
        bits.append(f"steps={result.get('steps')}")
    if result.get("tool_calls") is not None:
        bits.append(f"tools={len(result.get('tool_calls') or [])}")
    if result.get("children") is not None:
        bits.append(f"children={result.get('succeeded')}/{result.get('children')}")
    keys = [k for k in result if k not in {"steps", "tool_calls", "children", "succeeded"}][:4]
    if keys:
        bits.append("keys=" + ",".join(keys))
    return " ".join(bits)[:limit]


job_queue = JobQueue()
