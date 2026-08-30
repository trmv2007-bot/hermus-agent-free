"""Event & command contracts (Rebuild spec §8).

``EventEnvelope`` is the one event model. Every UI action that changes
configuration, starts/stops work, sends a command, edits a file, selects a model,
approves a permission or triggers a run is a :class:`Command`; every Command flows
through the event system as an :class:`EventEnvelope`.

Design rules:
* One canonical envelope. UI and integrations subscribe; they do not invent a
  second event model.
* Secret-bearing arguments are **redacted** before they ever reach an envelope.
* Idempotent or carrying an ``idempotency_key`` where duplicate clicks are possible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict, fields as _fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    """Types of events that flow through the event bus."""

    COMMAND_REQUESTED = "command.requested"
    COMMAND_STARTED = "command.started"
    COMMAND_PROGRESS = "command.progress"
    COMMAND_SUCCEEDED = "command.succeeded"
    COMMAND_FAILED = "command.failed"
    STATE_CHANGED = "state.changed"
    EVIDENCE_ADDED = "evidence.added"
    PERMISSION_CHECKED = "permission.checked"


class CommandStatus(str, Enum):
    """Lifecycle of a backend command."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Actor(str, Enum):
    USER = "user"
    AGENT = "agent"
    SCHEDULER = "scheduler"
    SYSTEM = "system"


class CommandSource(str, Enum):
    CLI = "cli"
    DASHBOARD = "dashboard"
    VOICE = "voice"
    CHANNEL = "channel"
    SCHEDULER = "scheduler"
    INTERNAL = "internal"


# Fields that must never be stored inside an event payload.
_SECRET_KEYS = ("api_key", "apikey", "token", "secret", "password", "key", "bearer")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def redact(value: Any, *, enabled: bool = True) -> Any:
    """Recursively redact secret-looking keys from an args payload.

    Redaction is always on for event payloads; callers may disable
    (``enabled=False``) only in controlled, non-persisted in-memory paths.
    """
    if enabled is False:
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SECRET_KEYS:
                out[k] = "<redacted>"
            else:
                out[k] = redact(v, enabled=True)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, enabled=True) for v in value]
    return value


@dataclass
class EventEnvelope:
    """One canonical event envelope.

    Matches the Rebuild spec §8 required envelope shape. Fields that are not
    applicable are ``None``. All timestamps are ISO-8601 UTC.
    """

    event_id: str = field(default_factory=_new_id)
    trace_id: str = field(default_factory=_new_id)
    run_id: Optional[str] = None
    mission_id: Optional[str] = None
    session_id: str = "default"
    actor: str = Actor.AGENT.value
    source: str = CommandSource.INTERNAL.value
    type: str = EventType.STATE_CHANGED.value
    command: str = ""
    target: Optional[str] = None
    args_redacted: dict[str, Any] = field(default_factory=dict)
    command_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    timestamp: str = field(default_factory=_now_iso)
    duration_ms: Optional[int] = None
    status: str = CommandStatus.PENDING.value
    error_code: Optional[str] = None
    evidence_refs: list[str] = field(default_factory=list)
    parent_event_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventEnvelope":
        known = {f.name for f in _fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_trace(self, trace_id: Optional[str] = None,
                   mission_id: Optional[str] = None,
                   run_id: Optional[str] = None,
                   session_id: Optional[str] = None) -> "EventEnvelope":
        if trace_id:
            self.trace_id = trace_id
        if mission_id:
            self.mission_id = mission_id
        if run_id:
            self.run_id = run_id
        if session_id:
            self.session_id = session_id
        return self


@dataclass
class Command:
    """A typed backend command representing any meaningful action.

    One canonical command form used by CLI, dashboard, channels, scheduler,
    queue and computer control. Carries its own correlation ids and idempotency.
    """

    command: str
    target: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    actor: str = Actor.USER.value
    source: str = CommandSource.DASHBOARD.value
    session_id: str = "default"
    trace_id: str = field(default_factory=_new_id)
    idempotency_key: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)

    def redacted_args(self) -> dict[str, Any]:
        return redact(self.args)

    def to_envelope(self, type: str = EventType.COMMAND_REQUESTED.value,
                    status: str = CommandStatus.PENDING.value) -> EventEnvelope:
        return EventEnvelope(
            trace_id=self.trace_id,
            session_id=self.session_id,
            actor=self.actor,
            source=self.source,
            type=type,
            command=self.command,
            target=self.target,
            args_redacted=self.redacted_args(),
            idempotency_key=self.idempotency_key,
            status=status,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
