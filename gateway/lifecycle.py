"""Gateway lifecycle state: uptime, readiness and graceful drain.

Health probes used to be guesswork: ``/api/status`` always answers 200, so a
supervisor (systemd, Docker, k8s) cannot tell "up but still starting" from
"serving". This module owns the two answers a supervisor actually needs:

``/healthz``  liveness  — is the process able to serve HTTP at all?
``/readyz``   readiness — may traffic be sent *right now*? False during
                          startup and during a graceful drain, so a load
                          balancer can take the instance out of rotation
                          before the process exits and in-flight jobs finish.

Readiness is derived from real runtime state (the job queue and the drain
flag), never from a hardcoded ``ok``.
"""

from __future__ import annotations

import os
import time
from typing import Any

_START = time.monotonic()


class LifecycleState:
    """Mutable process lifecycle flags (single instance, see :data:`state`)."""

    def __init__(self) -> None:
        self.started = False
        self.draining = False
        self.drain_started_at: float | None = None
        self.shutdown_reason: str = ""

    def mark_started(self) -> None:
        """Startup completed — and a restart means we are no longer draining.

        Clearing the drain flag here matters beyond bookkeeping: a process can
        run the lifespan more than once (tests, an in-process restart), and a
        stale ``draining`` would make readiness report "not ready" forever
        after the first shutdown.
        """
        self.started = True
        self.draining = False
        self.drain_started_at = None
        self.shutdown_reason = ""

    def begin_drain(self, reason: str = "shutdown") -> None:
        """Stop advertising readiness; in-flight work is still finishing."""
        if not self.draining:
            self.draining = True
            self.drain_started_at = time.monotonic()
            self.shutdown_reason = reason

    @property
    def drain_seconds(self) -> float:
        if self.drain_started_at is None:
            return 0.0
        return round(time.monotonic() - self.drain_started_at, 3)


state = LifecycleState()


def uptime_seconds() -> float:
    return round(time.monotonic() - _START, 3)


def drain_timeout_seconds(default: float = 15.0) -> float:
    """How long a graceful shutdown waits for in-flight jobs (``0`` = skip)."""
    raw = os.environ.get("HERMUS_DRAIN_TIMEOUT", "").strip()
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(getattr(_config(), "gateway_queue_cancel_grace", default) or default)


def _config() -> Any:  # local import keeps this module import-cycle free
    from core.config import config

    return config


def readiness() -> tuple[bool, dict[str, Any]]:
    """Return ``(ready, detail)`` from real runtime state.

    A gateway is ready once its lifespan has started the realtime layer and it
    is not draining. The queue is only a gate when it is enabled *and* it
    failed to start — an intentionally disabled queue (inline mode) is ready.
    """
    checks: dict[str, Any] = {
        "started": state.started,
        "draining": state.draining,
    }
    queue_state = "disabled"
    try:
        from gateway.queue import job_queue

        if job_queue.enabled:
            queue_state = "running" if getattr(job_queue, "_started", False) else "not_started"
            if getattr(job_queue, "_draining", False):
                queue_state = "draining"
        checks["queue"] = queue_state
    except Exception as exc:  # noqa: BLE001 - readiness must still answer
        queue_state = f"error: {type(exc).__name__}"
        checks["queue"] = queue_state

    reasons: list[str] = []
    if not state.started:
        reasons.append("gateway lifespan has not completed startup")
    if state.draining:
        reasons.append(f"draining ({state.shutdown_reason or 'shutdown'})")
    if queue_state == "not_started":
        reasons.append("job queue enabled but not started")
    elif queue_state == "draining":
        reasons.append("job queue is draining (no new work accepted)")
    elif queue_state.startswith("error:"):
        reasons.append(f"job queue probe failed: {queue_state}")

    checks["ready"] = not reasons
    checks["reasons"] = reasons
    checks["uptime"] = uptime_seconds()
    return (not reasons), checks
