"""Verified computer-action orchestration."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

@dataclass
class ComputerExecutionResult:
    ok: bool
    action: str
    observation_before: Any = None
    action_result: dict[str, Any] = field(default_factory=dict)
    observation_after: Any = None
    verification: dict[str, Any] = field(default_factory=dict)
    recovery: dict[str, Any] | None = None
    error: str | None = None
    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

class VerifiedComputerExecutor:
    """Observe -> gated action -> observe -> verify -> optional recovery."""
    def __init__(self, controller: Any, *, observer: Callable[[], Any] | None = None,
                 verifier: Callable[[Any, Any, dict[str, Any]], dict[str, Any]] | None = None,
                 recoverer: Callable[[ComputerExecutionResult], dict[str, Any] | None] | None = None) -> None:
        self.controller, self.observer, self.verifier, self.recoverer = controller, observer, verifier, recoverer

    def execute(self, action: str, *, args: dict[str, Any] | None = None,
                precondition: Callable[[Any], bool] | None = None) -> dict[str, Any]:
        args = dict(args or {})
        before = self.observer() if self.observer else None
        if precondition is not None and not precondition(before):
            return self._finish(ComputerExecutionResult(False, action, observation_before=before, error="precondition_failed"))
        fn = getattr(self.controller, action, None)
        if not callable(fn):
            return self._finish(ComputerExecutionResult(False, action, observation_before=before, error=f"unsupported computer action: {action}"))
        try:
            action_result = fn(**args)
        except Exception as exc:  # noqa: BLE001
            return self._finish(ComputerExecutionResult(False, action, observation_before=before, error=str(exc)))
        if not action_result.get("ok", False):
            return self._finish(ComputerExecutionResult(False, action, observation_before=before, action_result=action_result,
                                                        error=action_result.get("error") or action_result.get("detail") or "action_failed"))
        after = self.observer() if self.observer else None
        verification = self.verifier(before, after, action_result) if self.verifier else {"verified": False, "reason": "no verifier configured"}
        ok = bool(verification.get("verified"))
        return self._finish(ComputerExecutionResult(ok, action, before, action_result, after, verification,
                                                     error=None if ok else verification.get("reason", "postcondition_not_verified")))

    def _finish(self, result: ComputerExecutionResult) -> dict[str, Any]:
        if not result.ok and self.recoverer is not None:
            result.recovery = self.recoverer(result)
            if isinstance(result.recovery, dict) and result.recovery.get("verified"):
                result.ok = True
        return result.as_dict()

__all__ = ["ComputerExecutionResult", "VerifiedComputerExecutor"]
