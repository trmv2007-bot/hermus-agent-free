"""Hermus agent harness — jcode-inspired runtime primitives.

Session ≠ window. Compaction, file-shift events, a message bus, cascade
memory and same-repo swarm live here so the ReAct loop can stay small.

Ownership: :mod:`core.harness.runtime` is the one facade the agent calls, and
:func:`core.harness.runtime.governed_turn` is the ONE turn entry — every path
that assembles a prompt by hand (counsel, delegation, multi-model panels, the
fleet probe, the swarm pool) goes through it, so no model turn escapes budget,
memory, swarm mail and the session record. :mod:`core.harness.stats` is the one
read-only projection the gateway uses; :mod:`core.harness._store` is the one
durable JSON store; :mod:`core.harness.bus` is the one agent-message bus.
"""

from . import bus, files, sessions, stats
from .compaction import compact_messages
from .memory_graph import cascade_recall
from .runtime import HarnessRuntime, governed_call, governed_turn, harness
from .swarm import run_workers, spawn
from .swarm import status as swarm_status

__all__ = [
    "bus",
    "files",
    "sessions",
    "stats",
    "compact_messages",
    "cascade_recall",
    "HarnessRuntime",
    "harness",
    "governed_turn",
    "governed_call",
    "spawn",
    "run_workers",
    "swarm_status",
]
