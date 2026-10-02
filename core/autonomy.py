"""Canonical end-to-end autonomy facade for HERMUS.

This is intentionally a thin orchestration boundary: ExecutiveLoop owns the
lifecycle while this facade gives gateways one stable entry point and enforces
a small, explicit result contract for observability and recovery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .executive_loop import ExecutiveLoop, executive_loop


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

    def run(self, text: str, *, on_event: Callable[[str, dict[str, Any]], None] | None = None, **kwargs: Any) -> AutonomyResult:
        result = self.loop.execute(text, on_event=on_event, **kwargs)
        state = str(result.get("state") or result.get("status") or "unknown")
        verified = result.get("verified")
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
