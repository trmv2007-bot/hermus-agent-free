"""Canonical Hermus contracts (Rebuild spec §5, §8, §9, §10, §11, §14).

This package is the **single source of truth** for the typed contracts that the
consolidated architecture is built on. Every subsystem exposes one public facade
and speaks one state model. Never add a parallel ``v2``/``new``/``final`` contract
here — extend these, do not duplicate them.

Contracts are intentionally kept lightweight (they prefer the stdlib ``dataclasses``
over a heavyweight ORM) so they can be imported anywhere without pulling in
optional dependencies.
"""

from .events import EventEnvelope, Command, CommandStatus, EventType, redact
from .tools import (ToolDescriptor, ToolResult, Evidence, ToolStatus, RiskClass,
                    SideEffectClass, IdempotencyMode)
from .mission import MissionNode, MissionState
from .models import ModelRequirement, ModelSelection, ModelGatewayResult, FailureClass, Capability
from .jobs import Job, JobStatus, WorkerLifecycle

__all__ = [
    # events
    "EventEnvelope",
    "Command",
    "CommandStatus",
    "EventType",
    "redact",
    # tools
    "ToolDescriptor",
    "ToolResult",
    "Evidence",
    "ToolStatus",
    "RiskClass",
    "SideEffectClass",
    "IdempotencyMode",
    # mission
    "MissionNode",
    "MissionState",
    # models
    "ModelRequirement",
    "ModelSelection",
    "ModelGatewayResult",
    "FailureClass",
    "Capability",
    # jobs
    "Job",
    "JobStatus",
    "WorkerLifecycle",
]
