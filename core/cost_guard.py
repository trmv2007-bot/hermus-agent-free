"""
Cost Guard - Autonomous Circuit Breaker for Hermus Agent.
While MissionBudget tracks "steps", it doesn't prevent massive token burns.
This module aggregates real-time usage (USD and tokens) and enforces hard limits.
"""

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CostGuard:
    max_cost_usd: float = 2.00  # Default hard limit: $2.00 per mission
    max_tokens: int = 500_000  # Default hard limit: 500k tokens per mission

    total_cost: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_costs: dict[str, float] = field(default_factory=dict)

    last_check: float = field(default_factory=time.time)
    tripped: bool = False
    trip_reason: Optional[str] = None

    def record_usage(self, usage: dict[str, Any] | None, model: str = "unknown"):
        """Ingest the usage dict returned by LLMResponse."""
        if not usage or not isinstance(usage, dict):
            return

        cost = usage.get("total_cost", 0.0)
        p_tokens = usage.get("prompt_tokens", 0)
        c_tokens = usage.get("completion_tokens", 0)
        t_tokens = usage.get("total_tokens", p_tokens + c_tokens)

        if cost > 0 or t_tokens > 0:
            self.total_cost += cost
            self.total_tokens += t_tokens
            self.prompt_tokens += p_tokens
            self.completion_tokens += c_tokens

            self.model_costs[model] = self.model_costs.get(model, 0.0) + cost
            self.last_check = time.time()
            self._check_limits()

    def _check_limits(self):
        if self.tripped:
            return
        if self.total_cost >= self.max_cost_usd:
            self.tripped = True
            self.trip_reason = f"cost_limit_exceeded (${self.total_cost:.4f} >= ${self.max_cost_usd:.2f})"
        elif self.total_tokens >= self.max_tokens:
            self.tripped = True
            self.trip_reason = f"token_limit_exceeded ({self.total_tokens} >= {self.max_tokens})"

    def is_safe(self) -> bool:
        return not self.tripped

    def get_summary(self) -> dict[str, Any]:
        return {
            "tripped": self.tripped,
            "trip_reason": self.trip_reason,
            "total_cost_usd": round(self.total_cost, 6),
            "total_tokens": self.total_tokens,
            "limits": {"max_cost_usd": self.max_cost_usd, "max_tokens": self.max_tokens},
        }
