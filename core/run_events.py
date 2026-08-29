"""Run event stream — the plumbing behind token + tool-step streaming.

A gateway worker thread runs the (synchronous) agent loop while one or more
async HTTP clients (SSE) or WebSocket connections watch. This module is the
bridge between them:

* ``RunBus.publish(...)`` is callable from **any thread** and never blocks or
  raises — telemetry must not break an agent run.
* Async consumers get an ``asyncio.Queue`` fed via ``loop.call_soon_threadsafe``,
  with replay of the run's history so a client that connects mid-run (or a
  reconnecting SSE client with ``Last-Event-ID``) sees nothing missed.
* Bounded per-run ring buffer keeps memory flat for long runs.

Event kinds used by the agent loop: ``run_started``, ``step_started``,
``llm_delta``, ``llm_finished``, ``tool_call``, ``tool_result``, ``memory``,
``skill``, `subagent``, ``job_status``, ``verification``, ``run_finished``,
``run_error``, ``log``.

Mid-run steering: ``RunBus.steer(run_id, text)`` queues an instruction that the
executing agent loop drains via ``pending_steers()`` at each step boundary —
steering reaches the model, not just the dashboard. Structured, non-fatal
failures are recorded via :func:`record_issue` (component / operation / error /
mission / run / step / retryable / fallback) instead of silent
``except Exception: pass``.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


@dataclass
class Run:
    """One agent execution with its event log."""

    run_id: str
    label: str = ""
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=2000))
    seq: int = 0
    status: str = "running"          # running | finished | error | cancelled
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    error: str = ""
    subscribers: set[Any] = field(default_factory=set)
    cancel: threading.Event = field(default_factory=threading.Event)
    # Mid-run steering: instructions queued by POST /run/steer. The active
    # agent loop drains this inbox at every step boundary and injects whatever
    # is new into the conversation, so steering actually reaches the model
    # instead of only being recorded on the event stream.
    steer_inbox: deque[str] = field(default_factory=lambda: deque(maxlen=100))


    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "label": self.label,
            "status": self.status,
            "events": self.seq,
            "started": self.started,
            "finished": self.finished,
            "duration_ms": int(((self.finished or time.time()) - self.started) * 1000),
            "cancelled": self.cancel.is_set(),
        }
        # the final answer is part of the snapshot so a reconnecting client (or
        # `hermus runs`) can see what happened without replaying every event
        if self.result is not None:
            out["result"] = self.result
        if self.error:
            out["error"] = self.error
        return out


class RunBus:
    """Thread-safe publish/subscribe for agent runs (SSE + WebSocket friendly)."""

    def __init__(self, max_events: int = 2000, max_runs: int = 200):
        self._runs: dict[str, Run] = {}
        self._order: deque[str] = deque(maxlen=max_runs)
        self._max_events = max(50, int(max_events))
        self._lock = threading.RLock()
        self._sinks: list[Callable[[str, dict[str, Any]], None]] = []

    # ------------------------------------------------------------------- runs
    def start(self, run_id: str, label: str = "") -> Run:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                run = Run(run_id=run_id, label=label)
                run.events = deque(maxlen=self._max_events)
                self._runs[run_id] = run
                self._order.append(run_id)
                # keep memory flat: evict the oldest *finished* runs
                while len(self._runs) > self._order.maxlen:
                    victim = next(
                        (r for r in list(self._order)
                         if self._runs.get(r) and self._runs[r].status != "running"),
                        None,
                    )
                    if not victim:
                        break
                    self._order.remove(victim)
                    self._runs.pop(victim, None)
            else:
                run.events = run.events if run.events.maxlen == self._max_events else deque(run.events, maxlen=self._max_events)
                run.seq = 0
                run.result = None
            run.status = "running"
            run.cancel.clear()
            run.started = time.time()
            run.finished = None
        self.publish(run_id, "run_started", {"label": label})
        return run

    def get(self, run_id: str) -> Optional[Run]:
        with self._lock:
            return self._runs.get(run_id)

    def runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in self._runs.values()]

    def finish(self, run_id: str, status: str = "finished", result: Optional[dict[str, Any]] = None,
               error: str = "") -> None:
        run = self.get(run_id)
        payload: dict[str, Any] = {"status": status, "duration_ms": 0}
        if run is not None:
            with self._lock:
                run.status = status
                run.finished = time.time()
                run.result = result
                run.error = str(error or "")[:2000]
                payload["duration_ms"] = int((run.finished - run.started) * 1000)
        if result is not None:
            payload["keys"] = sorted(list(result.keys()))[:20]
            for key in ("model", "steps", "mode", "run_id"):
                if isinstance(result, dict) and key in result:
                    payload[key] = result[key]
        if error:
            payload["error"] = str(error)[:2000]
            self.publish(run_id, "run_error", payload)
        self.publish(run_id, "run_finished", payload)
        if run is not None:
            with self._lock:
                subs = list(run.subscribers)
            for q in subs:
                self._offer(q, {"type": "__closed__", "status": status, "run_id": run_id})

    def cancel(self, run_id: str) -> bool:
        run = self.get(run_id)
        if run is None:
            return False
        run.cancel.set()
        self.publish(run_id, "cancel_requested", {})
        return True

    def is_cancelled(self, run_id: str) -> bool:
        run = self.get(run_id)
        return bool(run and run.cancel.is_set())

    # ------------------------------------------------------------------ steer
    def steer(self, run_id: str, text: str) -> bool:
        """Queue a mid-run instruction for the agent executing ``run_id``.

        Returns True when an active run was found. The instruction is (a)
        published on the run's event stream (dashboards/SSE see it live) and
        (b) appended to the run's steer inbox, which the agent loop drains at
        its next step boundary — so the constraint reaches the model, not just
        the UI.
        """
        text = str(text or "").strip()
        if not text:
            return False
        run = self.get(run_id)
        if run is None:
            return False
        with self._lock:
            run.steer_inbox.append(text)
        self.publish(run_id, "steer", {"text": text, "queued": True, "ts": _now()})
        return True

    def pending_steers(self, run_id: str) -> list[str]:
        """Drain and return steering instructions not yet consumed by the agent."""
        run = self.get(run_id)
        if run is None:
            return []
        with self._lock:
            drained = list(run.steer_inbox)
            run.steer_inbox.clear()
        if drained:
            self.publish(run_id, "steer_consumed", {"count": len(drained)})
        return drained

    # ----------------------------------------------------------------- publish
    def publish(self, run_id: str, event_type: str, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        event = {
            "id": None,
            "run_id": run_id,
            "type": str(event_type or "event"),
            "ts": _now(),
            "data": dict(data or {}),
        }
        run = self.get(run_id)
        if run is None:  # auto-create so late emitters still land somewhere
            run = self.start(run_id)
        with self._lock:
            run.seq += 1
            event["id"] = run.seq
            run.events.append(event)
            subs = list(run.subscribers)
        for sink in list(self._sinks):
            try:
                sink(run_id, event)
            except Exception:
                pass
        for q in subs:
            self._offer(q, event)
        return event

    def _offer(self, q: Any, event: dict[str, Any]) -> None:
        """Deliver to a subscriber queue without ever blocking the publisher."""
        try:
            loop, aq = q if isinstance(q, tuple) else (None, q)
            if loop is not None:
                try:
                    loop.call_soon_threadsafe(_put_nowait, aq, event)
                    return
                except RuntimeError:
                    pass  # loop closed — drop
            _put_nowait(aq, event)
        except Exception:
            pass

    def add_sink(self, fn: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._sinks.append(fn)

        def remove() -> None:
            with self._lock:
                try:
                    self._sinks.remove(fn)
                except ValueError:
                    pass

        return remove

    # ------------------------------------------------------------------ subscribe
    def history(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        run = self.get(run_id)
        if run is None:
            return []
        with self._lock:
            return [e for e in list(run.events) if int(e["id"] or 0) > int(after)][-limit:]

    def subscribe(self, run_id: str, *, loop: Optional[asyncio.AbstractEventLoop] = None,
                  replay: bool = True, after: int = 0, queue_max: int = 2000):
        """Return (queue, unsubscribe). Items are event dicts; ``__closed__`` ends the stream."""
        aq: "asyncio.Queue" = asyncio.Queue(maxsize=queue_max)
        key = (loop, aq) if loop is not None else aq
        run = self.get(run_id) or self.start(run_id)
        if replay:
            for ev in self.history(run_id, after=after):
                _put_nowait(aq, ev)
        with self._lock:
            run.subscribers.add(key)

        def unsubscribe() -> None:
            with self._lock:
                r = self._runs.get(run_id)
                if r is not None:
                    r.subscribers.discard(key)

        return aq, unsubscribe

    def snapshot(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if run is None:
            return {"run_id": run_id, "exists": False}
        out = run.to_dict()
        out["exists"] = True
        out["last_events"] = list(run.events)[-5:]
        return out


def _put_nowait(aq: "asyncio.Queue", event: dict[str, Any]) -> None:
    try:
        if aq.full():
            try:
                aq.get_nowait()  # drop oldest rather than block the producer
            except Exception:
                pass
        aq.put_nowait(event)
    except Exception:
        pass


run_bus = RunBus()


class RunHandle:
    """Convenience emitter object handed to agent code: ``run.emit(...)``."""

    def __init__(self, run_id: str, bus: Optional[RunBus] = None, label: str = ""):
        self.run_id = run_id
        self.bus = bus or run_bus
        self.label = label
        self.started = False

    def start(self, label: str = "") -> "RunHandle":
        self.bus.start(self.run_id, label or self.label)
        self.started = True
        return self

    def emit(self, event_type: str, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self.bus.publish(self.run_id, event_type, data)

    def token(self, text: str) -> None:
        if text:
            self.emit("llm_delta", {"text": text[:4000]})

    def log(self, message: str, level: str = "info") -> None:
        self.emit("log", {"level": level, "message": str(message)[:2000]})

    def finish(self, result: Optional[dict[str, Any]] = None, error: str = "") -> None:
        self.bus.finish(self.run_id, "error" if error else "finished", result, error)

    @property
    def cancelled(self) -> bool:
        return self.bus.is_cancelled(self.run_id)


# --------------------------------------------------------------------- issues
@dataclass
class RuntimeIssue:
    """A structured, non-fatal runtime problem (replaces silent except/pass).

    Every "best-effort" path that used to swallow an exception can now record
    WHAT failed, WHERE, and whether it fell back — with enough context
    (mission/run/step ids) to diagnose an autonomous run after the fact.
    """

    component: str                 # memory | routing | telemetry | executor | ...
    operation: str                 # recall | publish | scan | ...
    error: str
    error_type: str = ""
    mission_id: Optional[str] = None
    run_id: Optional[str] = None
    step: Optional[int] = None
    retryable: Optional[bool] = None
    fallback: Optional[str] = None   # what was done instead ("continued without memory block")
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "operation": self.operation,
            "error": self.error[:500],
            "error_type": self.error_type,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "step": self.step,
            "retryable": self.retryable,
            "fallback": self.fallback,
            "ts": self.ts,
        }


_issue_ring: deque[dict[str, Any]] = deque(maxlen=500)
_issue_lock = threading.Lock()


def _issue_log_path() -> Optional[Path]:
    """Best-effort durable issue log under the hermus workspace logs dir."""
    try:
        from core.workspace import workspace

        path = workspace.dirs["logs"] / "runtime_issues.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return None


def record_issue(
    component: str,
    operation: str,
    error: Any,
    *,
    error_type: str = "",
    mission_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step: Optional[int] = None,
    retryable: Optional[bool] = None,
    fallback: Optional[str] = None,
) -> dict[str, Any]:
    """Record a structured runtime issue: log line + ring buffer + run event.

    Never raises — the whole point is that diagnostics must not break the run
    they are diagnosing.
    """
    try:
        if isinstance(error, BaseException):
            error_type = error_type or type(error).__name__
            error = str(error) or type(error).__name__
        issue = RuntimeIssue(
            component=str(component or "unknown"),
            operation=str(operation or "unknown"),
            error=str(error)[:500],
            error_type=error_type,
            mission_id=mission_id,
            run_id=run_id,
            step=step,
            retryable=retryable,
            fallback=fallback,
        )
        d = issue.to_dict()
        with _issue_lock:
            _issue_ring.append(d)
        print(
            f"[issue] component={d['component']} op={d['operation']} "
            f"error={d['error_type']}: {d['error'][:160]} "
            f"retryable={d['retryable']} fallback={d['fallback'] or 'none'}"
        )
        # durable append-only log (best-effort)
        try:
            path = _issue_log_path()
            if path is not None:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(d, default=str) + "\n")
        except Exception:
            pass
        # surface on the run's event stream when the issue belongs to one
        if run_id:
            try:
                run_bus.publish(run_id, "runtime_issue", d)
            except Exception:
                pass
        return d
    except Exception:
        return {"component": str(component), "operation": str(operation),
                "error": "issue-recorder-failed"}


def recent_issues(limit: int = 100) -> list[dict[str, Any]]:
    """Most recent structured runtime issues (newest last)."""
    with _issue_lock:
        return list(_issue_ring)[-max(1, int(limit)):]


def run_handle_from(run_id: str) -> RunHandle:
    return RunHandle(run_id)


def sse_format(event: dict[str, Any]) -> str:

    """Server-Sent Events framing (id + event + data)."""
    payload = json.dumps(event, default=str)
    return f"id: {event.get('id')}\nevent: {event.get('type')}\ndata: {payload}\n\n"
