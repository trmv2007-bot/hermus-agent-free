"""Canonical end-to-end autonomy facade for HERMUS.

This is intentionally a thin orchestration boundary: ExecutiveLoop owns the
lifecycle while this facade gives gateways one stable entry point and enforces
a small, explicit result contract for observability and recovery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .executive_loop import ExecutiveLoop, executive_loop
from .runtime_health import RunTracker, classify_failure


@dataclass(frozen=True)
class AutonomyResult:
    ok: bool
    state: str
    mission_id: str | None
    verified: bool | None
    result: dict[str, Any]


class AutonomyFacade:
    def __init__(self, loop: ExecutiveLoop | None = None) -> None:
        self.loop = loop or executive_loop

    def run(
        self,
        text: str,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> AutonomyResult:
        tracker = RunTracker(
            run_id=run_id,
            surface=kwargs.get("platform", "api"),
            user_id=kwargs.get("user_id", "anonymous"),
        )

        def emit(event_type: str, payload: dict[str, Any] | None = None) -> None:
            tracker.event()
            enriched = dict(payload or {})
            enriched.setdefault("run_id", tracker.run_id)
            if on_event is not None:
                try:
                    on_event(event_type, enriched)
                except Exception:
                    # Observability is never allowed to interrupt execution.
                    pass

        result = self.loop.execute(text, on_event=emit, **kwargs)
        if not isinstance(result, dict):
            result = {"response": str(result or "")}

        state = str(result.get("state") or result.get("status") or "unknown")
        verified = result.get("verified")
        failure_class, retryable = classify_failure(result)
        health = tracker.finish(
            state=state,
            failure_class=failure_class,
            retryable=retryable,
        )

        # Stable top-level correlation fields make the result usable by API,
        # voice, Control Room, queue and CLI surfaces without parsing events.
        result = dict(result)
        result.setdefault("run_id", tracker.run_id)
        result["run_health"] = health.as_dict()
        ok = state in {"completed", "done"} and verified is not False
        return AutonomyResult(
            ok=ok,
            state=state,
            mission_id=result.get("mission_id"),
            verified=verified,
            result=result,
        )


autonomy = AutonomyFacade()

__all__ = ["AutonomyFacade", "AutonomyResult", "autonomy"]
