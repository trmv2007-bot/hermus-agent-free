"""Tool contracts (Rebuild spec §9).

``ToolDescriptor`` is the one descriptor format; ``ToolResult`` is the one result
envelope. Every tool (shell, filesystem, browser, computer, MCP, connectors,
memory, skills) is an adapter behind the Tool Gateway and speaks these contracts.

No caller directly invokes ``subprocess``, ``pyautogui``, Playwright, HTTP provider
code or raw connector callbacks — those are implementation details behind the
gate. Advertised tools must have a descriptor, a callable implementation, a
verifier domain and an integration test.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields as _fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SideEffectClass(str, Enum):
    READ = "read"
    DRY = "dry"
    LOCAL = "local"
    NETWORK = "network"
    FILE = "file"
    EXEC = "exec"
    GUI = "gui"
    ADMIN = "admin"


class RiskClass(str, Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class IdempotencyMode(str, Enum):
    NONE = "none"
    IDEMPOTENT = "idempotent"
    KEYED = "keyed"
    VERIFY = "verify"


@dataclass
class ToolDescriptor:
    """One tool descriptor. Mirrors spec §9 exactly."""

    name: str
    version: str = "1.0"
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    capability_tags: list[str] = field(default_factory=list)
    risk_class: str = RiskClass.LOW.value
    side_effect_class: str = SideEffectClass.READ.value
    supports_dry_run: bool = False
    timeout_s: float = 30.0
    idempotency_mode: str = IdempotencyMode.NONE.value
    verifier_domains: list[str] = field(default_factory=list)
    fallback_policy: str = "none"
    needs_auth: bool = False
    needs_permission: bool = False
    admin_level: str = "user"
    source: str = "builtin"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolDescriptor":
        known = {f.name for f in _fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Evidence:
    """A typed evidence reference produced by a tool call or verifier."""

    kind: str  # change | execution | analysis | external | state
    uri: str  # file path, command, artifact, resource id
    description: str = ""
    source: str = "tool"
    timestamp: str = field(default_factory=_now_iso)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResult:
    """The one tool result envelope. Mirrors spec §9 exactly."""

    ok: bool
    status: str = ToolStatus.OK.value
    output: Any = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    evidence_refs: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    changed_resources: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=_now_iso)
    finished_at: Optional[str] = None
    trace_id: Optional[str] = None
    retryable: bool = False
    next_action: Optional[str] = None
    sandbox: Optional[str] = None
    duration_ms: Optional[int] = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def error(cls, code: str, message: str, *, retryable: bool = False,
              trace_id: Optional[str] = None, status: str = ToolStatus.ERROR.value,
              **kw) -> "ToolResult":
        return cls(ok=False, status=status, error_code=code, error_message=message,
                   retryable=retryable, trace_id=trace_id, **kw)

    @classmethod
    def ok_result(cls, output: Any = None, *, evidence_refs: Optional[list[str]] = None,
                  changed_resources: Optional[list[str]] = None,
                  trace_id: Optional[str] = None, **kw) -> "ToolResult":
        return cls(ok=True, output=output, evidence_refs=evidence_refs or [],
                   changed_resources=changed_resources or [], trace_id=trace_id, **kw)
