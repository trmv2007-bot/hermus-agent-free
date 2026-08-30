"""Canonical lightweight post-hoc verifiers (Rebuild spec §6, §16).

One owner for the "did the model's answer actually avoid obvious failure markers"
concern. This is the canonical home for the simple marker-based verifier and its
diagnoser that previously lived inside the legacy ``core.autonomous`` module.
The MissionEngine and ``agent._verify_answer`` are the only callers; there is no
second rule-based verifier implementation.
"""

from __future__ import annotations

from typing import Any

ERROR_MARKERS = (
    "error", "exception", "traceback", "failed", "failure", "no such file",
    "command not found", "refused", "timeout", "not running", "no api key",
    "not installed", "permission denied", "denied",
)


class MarkerVerifier:
    """Rule-based verifier: considered ok when non-empty and no error markers."""

    def verify(self, task: str, result: Any) -> dict[str, Any]:
        text = str(result or "").strip()
        low = text.lower()
        problems = [m for m in ERROR_MARKERS if m in low]
        ok = bool(text) and not problems
        return {
            "ok": ok,
            "problems": problems,
            "reason": "" if ok else (f"markers found: {problems}" if problems else "empty result"),
        }


class MarkerDiagnoser:
    """Rule-based diagnoser surfacing the failing marker as a hint."""

    def diagnose(self, task: str, result: Any, verification: dict[str, Any]) -> dict[str, Any]:
        problems = verification.get("problems") or []
        hint = f"Previous attempt failed ({', '.join(problems) or 'unsatisfactory result'})."
        return {"hint": hint, "retry": True}


# Backwards-compatible names used by external callers during migration.
Verifier = MarkerVerifier
Diagnoser = MarkerDiagnoser

__all__ = ["MarkerVerifier", "MarkerDiagnoser", "Verifier", "Diagnoser", "ERROR_MARKERS"]
