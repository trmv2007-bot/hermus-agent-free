"""Mission contracts (Rebuild spec §10).

``MissionNode`` is the required node contract; ``MissionState`` is the canonical
state machine. Verification is a first-class phase, failures are typed before a
retry, and a mission can stop as ``BLOCKED`` (never a silent success).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MissionState(str, Enum):
    CREATED = "CREATED"
    REQUIREMENTS = "REQUIREMENTS"
    PLANNING = "PLANNING"
    READY = "READY"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    DIAGNOSING = "DIAGNOSING"
    REPAIRING = "REPAIRING"
    REPLANNING = "REPLANNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OutcomeState(str, Enum):
    """How much of a claimed result the system confirmed for itself.

    Distinct from ``MissionState``, which tracks lifecycle position. A mission
    can be COMPLETED and still only CLAIMED: the workers finished their nodes,
    and nothing independent checked the outcome. The order below is the order
    of increasing confidence, and it is what a workspace or a repair loop
    should switch on rather than a mission's own summary text.
    """

    UNKNOWN = "unknown"  # nothing was observed either way
    CLAIMED = "claimed"  # only the worker's own report
    EXECUTED = "executed"  # actions ran, outcome not looked at
    OBSERVED = "observed"  # something was measured, verdict not reached
    PARTIALLY_VERIFIED = "partially_verified"  # some checks grounded, not all
    VERIFIED = "verified"  # deterministic checks agree with the claim
    FAILED = "failed"  # checked and found not to have happened


@dataclass
class MissionNode:
    """The required node contract (Rebuild spec §10)."""

    id: str
    goal: str
    dependencies: list[str] = field(default_factory=list)
    role: str = "worker"
    expected_output_type: str = "analysis"  # change | execution | analysis | external
    allowed_tools: list[str] = field(default_factory=list)
    model_requirements: dict[str, Any] = field(default_factory=dict)
    verifier_domain: str = "none"
    timeout_s: float = 120.0
    retry_policy: dict[str, Any] = field(default_factory=dict)
    risk_policy: dict[str, Any] = field(default_factory=dict)
    state: str = MissionState.CREATED.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissionReportHeader:
    """Compact mission header for dashboards and resume handles."""

    mission_id: str
    goal: str
    state: str
    result: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    resumable: bool = False
    changed_files: list[str] = field(default_factory=list)


def _fields(cls):
    import dataclasses

    return dataclasses.fields(cls)
