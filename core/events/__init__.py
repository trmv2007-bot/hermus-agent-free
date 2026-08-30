"""Canonical event subsystem (Rebuild spec §8, §9, §28).

One :class:`EventBus` powers backend audit, live UI updates, replay and telemetry.
The envelope is :class:`core.contracts.EventEnvelope`; consumers subscribe to typed
events or replay from a durable log. Legacy dict-style publishers
(``run_events``/``dashboard_events``/``computer.events``) mirror every event into
this one bus so the canonical EventBus is the single authoritative, replayable
event source; the dict APIs remain as projections for realtime/SSE consumers.
"""

from .bus import EventBus, get_bus, publish, configure_bus
from .envelope import make_command_envelope

__all__ = ["EventBus", "get_bus", "publish", "configure_bus", "make_command_envelope"]
