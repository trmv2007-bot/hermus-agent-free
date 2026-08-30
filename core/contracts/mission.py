"""Mission contracts (Rebuild spec §10).

``MissionNode`` is the required node contract; ``MissionState`` is the canonical
state machine. Verification is a first-class phase, failures are typed before a
retry, and a mission can stop as ``BLOCKED`` (never a silent success).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


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
    result: Optional[str] = None
    evidence_refs: list[str] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    resumable: bool = False
    changed_files: list[str] = field(default_factory=list)


def _fields(cls):
    import dataclasses
    return dataclasses.fields(cls)
