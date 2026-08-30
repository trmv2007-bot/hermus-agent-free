"""Canonical event subsystem (Rebuild spec §8, §9, §28).

One :class:`EventBus` powers backend audit, live UI updates, replay and telemetry.
The envelope is :class:`core.contracts.EventEnvelope`; consumers subscribe to typed
events or replay from a durable log. Compatibility adapters in :mod:`core.events`
bridge the legacy ``run_events`` / ``dashboard_events`` / ``computer.events``
publish paths into this one bus during the migration window (see
:class:`LegacyEventBridge`).
"""

from .bus import EventBus, get_bus, publish, configure_bus
from .envelope import make_command_envelope
from .legacy import LegacyEventBridge, publish_legacy

__all__ = ["EventBus", "get_bus", "publish", "configure_bus", "make_command_envelope",
           "LegacyEventBridge", "publish_legacy"]
