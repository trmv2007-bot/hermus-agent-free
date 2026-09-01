"""Model gateway contracts (Rebuild spec §11).

``ModelRequirement`` describes what a task needs; ``ModelGatewayResult`` is the
structured outcome (provider/model/token/rate/latency + typed error category).
Model name keywords are only one score feature, never proof of capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields as _fields
from enum import Enum
from typing import Any, Optional


class Capability(str, Enum):
    CHAT = "chat"
    CODE = "code"
    REASONING = "reasoning"
    VISION = "vision"
    RESEARCH = "research"
    TOOLING = "tooling"


class FailureClass(str, Enum):
    """Separate failure classes with different recovery policies.

    These map to the canonical structured error codes the ModelGateway exposes so
    a mission/agent can decide on recovery without parsing free-form text:
    ``provider_unavailable``, ``rate_limited``, ``authentication_failed``,
    ``model_unavailable``, ``timeout``, ``capability_mismatch``.
    """

    RATE_LIMIT = "rate_limit"            # 429
    AUTH = "auth"                        # 401/403
    TIMEOUT = "timeout"
    INVALID_MODEL = "invalid_model"
    TOOL_UNSUPPORTED = "tool_unsupported"
    CONTEXT_OVERFLOW = "context_overflow"
    POLICY_DENIED = "policy_denied"
    NETWORK = "network"
    PROVIDER_UNAVAILABLE = "provider_unavailable"   # provider down/refused
    MODEL_UNAVAILABLE = "model_unavailable"          # model not deployed/known
    CAPABILITY_MISMATCH = "capability_mismatch"      # model can't satisfy a requirement
    UNKNOWN = "unknown"


@dataclass
class ModelRequirement:
    """Task requirements for model selection."""

    task: str = "chat"
    capabilities: list[str] = field(default_factory=list)
    context_window: int = 0
    tools: bool = False
    vision: bool = False
    streaming: bool = False
    priority: str = "cost"  # cost | reliability | quality
    preferred_providers: list[str] = field(default_factory=list)
    max_tokens: int = 0

    def requires_tools(self) -> bool:
        return self.tools or Capability.TOOLING.value in self.capabilities


@dataclass
class ModelSelection:
    """A ranked candidate returned by select()."""

    provider: str
    model: str
    score: float
    capabilities: list[str] = field(default_factory=list)
    context_window: int = 0
    reason: str = ""
    tool_capable: bool = False
    vision_capable: bool = False
    base_url: Optional[str] = None
    api_key_ref: Optional[str] = None
    free: bool = False


@dataclass
class ModelGatewayResult:
    """Structured completion outcome (Rebuild spec §11)."""

    provider: str
    model: str
    ok: bool
    failure_class: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    rate_state: Optional[dict[str, Any]] = None
    used_fallback: bool = False
    fallback_reason: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    retryable: bool = False
    trace_id: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
