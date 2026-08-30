"""Helpers for building canonical Command envelopes."""

from __future__ import annotations

from typing import Any, Optional

from ..contracts import Command, EventEnvelope, EventType, CommandStatus


def make_command_envelope(command: Command, *,
                          type: str = EventType.COMMAND_STARTED.value,
                          status: str = CommandStatus.RUNNING.value,
                          parent_event_id: Optional[str] = None) -> EventEnvelope:
    return command.to_envelope(type=type, status=status).with_trace(
        trace_id=command.trace_id,
        session_id=command.session_id,
    )
