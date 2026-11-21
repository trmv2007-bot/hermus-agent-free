"""Stable product-level contracts used by the HERMUS Nexus interface.

These are deliberately small and serialization-friendly. They describe what
HERMUS is doing, not how an individual subsystem happens to implement it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

MissionState = Literal[
    "queued",
    "planning",
    "awaiting_approval",
    "running",
    "paused",
    "succeeded",
    "failed",
    "cancelled",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class NexusCommand:
    """A user intent entering HERMUS through any channel."""

    text: str
    user_id: str = "default"
    session_id: str | None = None
    channel: str = "nexus"
    mode: str = "chat"
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> NexusCommand:
        return NexusCommand(
            text=self.text.strip(),
            user_id=self.user_id.strip() or "default",
            session_id=self.session_id,
            channel=self.channel.strip() or "nexus",
            mode=self.mode.strip() or "chat",
            priority=int(self.priority),
            metadata=dict(self.metadata),
        )


@dataclass
class Mission:
    """Product-level execution identity shared by UI and backend services."""

    id: str
    intent: str
    user_id: str = "default"
    state: MissionState = "queued"
    run_id: str | None = None
    job_id: str | None = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    steps: list[dict[str, Any]] = field(default_factory=list)
    active_step: str | None = None
    capabilities: list[str] = field(default_factory=list)
    approval: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def transition(self, state: MissionState, **changes: Any) -> None:
        self.state = state
        self.updated_at = now_iso()
        for key, value in changes.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NexusEvent:
    """Normalized event envelope consumed by the Nexus UI."""

    type: str
    timestamp: str = field(default_factory=now_iso)
    mission_id: str | None = None
    run_id: str | None = None
    source: str = "hermus"
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
