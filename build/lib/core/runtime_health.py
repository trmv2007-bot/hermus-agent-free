"""Runtime health and trace contract for HERMUS execution.

This module is deliberately side-effect-light. It does not execute work or make
policy decisions; it gives the canonical runtime a stable correlation/health
record that gateways and the Control Room can consume.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import monotonic, time
from typing import Any
from uuid import uuid4


@dataclass
class RunHealth:
    run_id: str
    started_at: float
    elapsed_ms: float = 0.0
    state: str = "running"
    event_count: int = 0
    failure_class: str | None = None
    retryable: bool | None = None
    completed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunTracker:
    """Track one execution without becoming a second execution engine."""

    def __init__(self, run_id: str | None = None, **metadata: Any) -> None:
        self.run_id = run_id or f"run_{uuid4().hex[:16]}"
        self._started_mono = monotonic()
        self.health = RunHealth(
            run_id=self.run_id,
            started_at=time(),
            metadata=dict(metadata),
        )

    def event(self) -> None:
        self.health.event_count += 1
        self.health.elapsed_ms = round((monotonic() - self._started_mono) * 1000, 2)

    def finish(
        self,
        *,
        state: str,
        failure_class: str | None = None,
        retryable: bool | None = None,
    ) -> RunHealth:
        self.health.state = str(state or "unknown")
        self.health.failure_class = failure_class
        self.health.retryable = retryable
        self.health.completed = self.health.state in {"completed", "done", "failed", "blocked", "cancelled"}
        self.health.elapsed_ms = round((monotonic() - self._started_mono) * 1000, 2)
        return self.health


def classify_failure(result: Any) -> tuple[str | None, bool | None]:
    """Classify a structured runtime result without parsing user-facing prose."""
    if not isinstance(result, dict):
        return None, None
    failure = result.get("failure")
    if not isinstance(failure, dict):
        return None, None
    error_type = str(failure.get("error_type") or "").lower()
    reason = str(failure.get("reason") or "").lower()
    combined = f"{error_type} {reason}"
    if any(token in combined for token in ("timeout", "tempor", "rate limit", "provider unavailable", "network")):
        return "transient", True
    if any(token in combined for token in ("permission", "approval", "policy", "red-line", "read_only")):
        return "policy", False
    if any(token in combined for token in ("authentication", "invalid model", "capability mismatch")):
        return "configuration", False
    if failure.get("recoverable") is True:
        return "recoverable", True
    if failure.get("recoverable") is False:
        return "terminal", False
    return "unknown", None


__all__ = ["RunHealth", "RunTracker", "classify_failure"]
