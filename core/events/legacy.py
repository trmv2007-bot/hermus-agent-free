"""Bridge the three legacy event systems onto the canonical EventBus.

During the migration window the legacy publishers (``run_events.RunBus``,
``dashboard_events.DashboardEventBus`` and ``computer.events.ComputerEventBus``)
keep their own APIs so existing callers keep working, but every event they emit is
**also** normalized into a canonical :class:`EventEnvelope` and published to the
one bus. That gives the control room a single authoritative feed.

This adapter is intentionally a *delegate*, not a new parallel event model: it
reads a legacy dict/event and emits one canonical envelope. It never invents a
second event system.
"""

from __future__ import annotations

from typing import Any, Optional

from ..contracts import EventEnvelope, EventType, CommandStatus
from .bus import get_bus


def _coerce_status(raw: Any) -> str:
    if raw is None:
        return CommandStatus.PENDING.value
    s = str(raw).lower()
    if "succeed" in s or "done" in s or "complete" in s:
        return CommandStatus.SUCCEEDED.value
    if "fail" in s or "error" in s:
        return CommandStatus.FAILED.value
    if "cancel" in s:
        return CommandStatus.CANCELLED.value
    if "start" in s or "run" in s or "exec" in s:
        return CommandStatus.RUNNING.value
    return CommandStatus.PENDING.value


def _coerce_type(raw: Any) -> str:
    if raw is None:
        return EventType.STATE_CHANGED.value
    t = str(raw)
    # Map legacy run/state event names onto the canonical type set.
    mapping = {
        "run.started": EventType.COMMAND_STARTED.value,
        "run.finished": EventType.COMMAND_SUCCEEDED.value,
        "run.failed": EventType.COMMAND_FAILED.value,
        "run.progress": EventType.COMMAND_PROGRESS.value,
        "state": EventType.STATE_CHANGED.value,
        "state.changed": EventType.STATE_CHANGED.value,
        "evidence": EventType.EVIDENCE_ADDED.value,
        "console": EventType.COMMAND_PROGRESS.value,
    }
    return mapping.get(t, EventType.STATE_CHANGED.value)


def publish_legacy(event_type: str, data: Optional[dict[str, Any]] = None,
                   *, source: str = "internal", trace_id: Optional[str] = None,
                   mission_id: Optional[str] = None, run_id: Optional[str] = None,
                   session_id: str = "default") -> EventEnvelope:
    """Normalize a legacy ``publish(type, data)`` call into a canonical envelope.

    This is the adapter that legacy event modules call; the canonical bus is the
    only thing that persists and fans out.
    """
    data = data or {}
    payload = data.get("args") or data.get("payload") or data.get("data") or data
    env = EventEnvelope(
        type=_coerce_type(event_type),
        status=_coerce_status(data.get("status") or data.get("phase")),
        command=str(data.get("command") or event_type),
        target=data.get("target"),
        args_redacted=payload if isinstance(payload, dict) else {"value": payload},
        source=source,
        session_id=session_id,
        mission_id=mission_id,
        run_id=run_id,
        trace_id=trace_id,
    )
    return get_bus().publish(env)


class LegacyEventBridge:
    """Thin wrapper that satisfies a ``publish(type, data)`` interface.

    Handed to legacy modules as their publisher so downstream code sees the same
    callable shape while events actually land on the canonical bus.
    """

    def __init__(self, *, source: str = "legacy", trace_id: Optional[str] = None):
        self.source = source
        self.trace_id = trace_id

    def publish(self, event_type: str, data: Optional[dict[str, Any]] = None) -> EventEnvelope:
        return publish_legacy(event_type, data, source=self.source, trace_id=self.trace_id)
