"""Hermus agent harness — jcode-inspired runtime primitives.

Session ≠ window. Compaction, file-shift events, a message bus, cascade
memory, and same-repo swarm live here so the ReAct loop can stay small.
"""
from . import bus, files, sessions
from .compaction import compact_messages
from .memory_graph import cascade_recall
from .runtime import HarnessRuntime, harness
from .swarm import run_workers, spawn, status as swarm_status

__all__ = [
    "bus",
    "files",
    "sessions",
    "compact_messages",
    "cascade_recall",
    "HarnessRuntime",
    "harness",
    "spawn",
    "run_workers",
    "swarm_status",
]
