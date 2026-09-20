"""The persistent Fleet (SPEC_PERSISTENT_FLEET) — canonical package.

Canonical owners, one per concern (consolidation rule, spec §2):

* :mod:`core.fleet.bus` — the event-sourced **Fleet Bus** (§6): single writer,
  monotonic ``seq``, per-agent cursors, idempotency keys, snapshot + replay,
  rotation/retention. The legacy ``core/harness/bus.py`` and
  ``core/agents/messaging.py`` are merged onto this contract in roadmap step 3
  (§10) — never re-implemented next to it.
* ``core.fleet.registry`` (roadmap step 1) — the **Fleet Registry** (§5):
  durable spawn/list/update/dismiss/checkpoint, boot = snapshot + replay.
* ``core.fleet.migrate`` (roadmap step 7) — explicit N→N+1 migrations (§13).

Import from this package rather than the module paths so future moves stay
internal, e.g. ``from core.fleet import FleetBus, STATE_CHANGED``.
"""

from __future__ import annotations

from .bus import (
    ALERT,
    BROADCAST,
    BROADCAST_KINDS,
    BUDGET_WARNING,
    CLAIM,
    DEFAULT_PRIORITY,
    DM,
    GATE_PENDING,
    GATE_RESOLVED,
    KINDS,
    MISSION_OPENED,
    MISSION_TERMINATED,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
    PROPOSE,
    RESULT,
    RETENTION_GRACE_DAYS,
    REVIEW,
    SCHEMA_VERSION,
    SNAPSHOT_EVERY_EVENTS,
    SNAPSHOT_INTERVAL_S,
    STATE_CHANGED,
    SUBTASK,
    SYNTHESIS,
    SYSTEM,
    USER_TASK,
    FleetBus,
    FleetEvent,
    get_bus,
    is_addressed_to,
    reset_bus,
)

__all__ = [
    "ALERT",
    "BROADCAST",
    "BROADCAST_KINDS",
    "BUDGET_WARNING",
    "CLAIM",
    "DEFAULT_PRIORITY",
    "DM",
    "FleetBus",
    "FleetEvent",
    "GATE_PENDING",
    "GATE_RESOLVED",
    "KINDS",
    "MISSION_OPENED",
    "MISSION_TERMINATED",
    "PRIORITY_HIGH",
    "PRIORITY_LOW",
    "PRIORITY_NORMAL",
    "PRIORITY_URGENT",
    "PROPOSE",
    "RESULT",
    "RETENTION_GRACE_DAYS",
    "REVIEW",
    "SCHEMA_VERSION",
    "SNAPSHOT_EVERY_EVENTS",
    "SNAPSHOT_INTERVAL_S",
    "STATE_CHANGED",
    "SUBTASK",
    "SYNTHESIS",
    "SYSTEM",
    "USER_TASK",
    "get_bus",
    "is_addressed_to",
    "reset_bus",
]
