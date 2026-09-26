"""Two-tier model policy: main (API) brain, local model for the rest.

`config.model` is the main brain - normally a hosted API model. `config.local_model`
is the always-available local model that covers three jobs:

* **doctor** - diagnostics, health checks and short factual probes never touch
  the API, so they cannot be blocked by quota, rate limits or an expired token
* **short turns** - a prompt at or under `local_model_max_chars` is answered
  locally, which spends no API quota at all
* **fallback** - when the main model fails, the local model takes over for
  `local_model_fallback_minutes` before the main model is retried

Routing is a pure function of the inputs plus the recorded health of each tier, so
it is decided in one place and tested without touching the network.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

REASON_MAIN = "main_model"
REASON_NO_LOCAL = "no_local_model_configured"
REASON_SAME = "local_is_main_model"
REASON_SHORT = "short_turn_saves_quota"
REASON_FALLBACK = "main_model_degraded"
REASON_TOOLS = "tools_require_main_model"


@dataclass(frozen=True)
class RouteDecision:
    """Where a call should go, and why."""

    provider: str
    model_name: str
    reason: str
    switched: bool
    using_fallback: bool = False

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.model_name}" if self.provider else self.model_name


def _prompt_chars(messages: list[dict] | None) -> int:
    """Total character count of the user-visible text in the request."""
    if not messages:
        return 0
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            # multimodal: count the text parts only
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += len(part["text"])
    return total


def split_model_ref(model: str | None) -> tuple[str, str]:
    """`nous/stealth/space-bunny-alpha` -> ("nous", "stealth/space-bunny-alpha").

    Splits on the first slash only: plenty of hosted model ids contain their own
    slash and must survive intact.
    """
    if not model:
        return "", ""
    if "/" in model:
        provider, _, name = model.partition("/")
        return provider.lower(), name
    return "", model


class ModelRouter:
    """Chooses a tier per call and remembers which tier is healthy."""

    def __init__(
        self,
        main_model: str | None = None,
        local_model: str | None = None,
        *,
        max_chars: int = 280,
        fallback_minutes: int = 10,
    ) -> None:
        self.main_model = (main_model or "").strip()
        self.local_model = (local_model or "").strip()
        self.max_chars = max(0, int(max_chars or 0))
        self.fallback_seconds = max(0.0, float(fallback_minutes or 0) * 60.0)
        self._fallback_until: float = 0.0
        self._failures: dict[str, int] = {}
        self._lock = threading.Lock()
        self.last_decision: RouteDecision | None = None
        self.history: list[RouteDecision] = []

    # ------------------------------------------------------------- decisions
    def route(
        self,
        messages: list[dict] | None = None,
        *,
        has_tools: bool = False,
        current: tuple[str, str] | None = None,
    ) -> RouteDecision:
        """Decide where this call goes. Never raises, never touches the network."""
        main_provider, main_name = split_model_ref(self.main_model)
        local_provider, local_name = split_model_ref(self.local_model)

        if not self.local_model:
            decision = self._decide(main_provider, main_name, REASON_MAIN, False)
        elif (local_provider, local_name) == (main_provider, main_name):
            decision = self._decide(main_provider, main_name, REASON_SAME, False)
        elif self.fallback_active():
            decision = self._decide(local_provider, local_name, REASON_FALLBACK, True, True)
        elif has_tools and self.max_chars > 0 and self.max_chars < 10_000_000:
            # Tool loops need a model that reliably emits tool calls; do not
            # spend those on the small local model.
            decision = self._decide(main_provider, main_name, REASON_TOOLS, False)
        elif self.max_chars and _prompt_chars(messages) <= self.max_chars:
            decision = self._decide(local_provider, local_name, REASON_SHORT, False)
        else:
            decision = self._decide(main_provider, main_name, REASON_MAIN, False)

        # `switched` means the caller must actually change provider, not merely
        # because the singleton already points at the chosen tier.
        cur = current or (main_provider, main_name)
        switched = (cur[0].lower(), cur[1]) != (decision.provider, decision.model_name)
        decision = RouteDecision(
            provider=decision.provider,
            model_name=decision.model_name,
            reason=decision.reason,
            switched=switched,
            using_fallback=decision.using_fallback,
        )

        with self._lock:
            self.last_decision = decision
            self.history.append(decision)
            if len(self.history) > 50:
                del self.history[:-50]
        return decision

    def _decide(
        self,
        provider: str,
        name: str,
        reason: str,
        switched: bool,
        using_fallback: bool = False,
    ) -> RouteDecision:
        return RouteDecision(
            provider=provider, model_name=name, reason=reason, switched=switched, using_fallback=using_fallback
        )

    # --------------------------------------------------------------- health
    def fallback_active(self, now: float | None = None) -> bool:
        if self.fallback_seconds <= 0:
            return False
        current = time.time() if now is None else now
        return current < self._fallback_until

    def record_failure(self, model: str | None = None) -> None:
        """Trip the fallback window after the main model fails."""
        target = model or self.main_model
        with self._lock:
            self._failures[target] = self._failures.get(target, 0) + 1
            if target == self.main_model and self.fallback_seconds > 0:
                self._fallback_until = time.time() + self.fallback_seconds

    def record_success(self, model: str | None = None) -> None:
        target = model or self.main_model
        with self._lock:
            self._failures[target] = 0
            if target == self.main_model:
                self._fallback_until = 0.0

    def clear_fallback(self) -> None:
        with self._lock:
            self._fallback_until = 0.0
            self._failures.clear()

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        """Doctor-shaped summary. Contains no credentials."""
        with self._lock:
            remaining = max(0.0, self._fallback_until - time.time())
            failures = dict(self._failures)
        return {
            "main_model": self.main_model or None,
            "local_model": self.local_model or None,
            "local_model_max_chars": self.max_chars,
            "fallback_active": remaining > 0,
            "fallback_remaining_s": int(remaining),
            "failures": failures,
            "last_reason": self.last_decision.reason if self.last_decision else None,
            "last_model": self.last_decision.ref if self.last_decision else None,
        }


_router: ModelRouter | None = None
_router_lock = threading.Lock()


def get_router(reload: bool = False) -> ModelRouter:
    """Process-wide router built from current config."""
    global _router
    with _router_lock:
        if _router is not None and not reload:
            return _router
        try:
            from .config import config

            _router = ModelRouter(
                main_model=config.model,
                local_model=config.local_model,
                max_chars=config.local_model_max_chars,
                fallback_minutes=config.local_model_fallback_minutes,
            )
        except Exception:
            # A router that cannot read config still routes everything to main.
            _router = ModelRouter()
        return _router
