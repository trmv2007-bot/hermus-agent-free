"""Job / background-work contracts (Rebuild spec §14).

One durable job schema and one worker protocol powers the queue, scheduler and
background agents. Jobs are recoverable after process restart; lease expiry makes
stuck work visible and re-claimable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields as _fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    RETRY_WAIT = "retry_wait"


class WorkerLifecycle(str, Enum):
    """Worker lifecycle: queued -> leased -> running -> succeeded."""

    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    """One durable job. Mirrors spec §14 exactly."""

    id: str
    type: str
    payload_ref: Optional[str] = None
    mission_id: Optional[str] = None
    priority: int = 0
    status: str = JobStatus.QUEUED.value
    attempt: int = 0
    lease_owner: Optional[str] = None
    heartbeat_at: Optional[str] = None
    next_run_at: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    result_ref: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        known = {f.name for f in _fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
