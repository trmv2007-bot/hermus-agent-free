"""LearningFacade — evidence-gated skill promotion.

Wraps the real ``SkillForge`` and enforces the spec §16 gate at one place:
a candidate is only promoted after ``min_successes`` independent verified
successful repetitions. It records exactly one successful run as *observed* but
not *promoted*, and it can quarantine a skill after repeated failures.
"""

from __future__ import annotations

import threading
from typing import Any, Optional, Sequence

DEFAULT_MIN_SUCCESSES = 3


class LearningFacade:
    """One canonical learning API backed by SkillForge."""

    def __init__(self, forge: Any = None, *, min_successes: int = DEFAULT_MIN_SUCCESSES,
                 skills_dir: Optional[str] = None):
        if forge is None:
            from ..skill_forge import SkillForge  # type: ignore
            forge = SkillForge(skills_dir=skills_dir)
        self._forge = forge
        self._min_successes = min_successes
        self._lock = threading.RLock()

    @property
    def forge(self) -> Any:
        return self._forge

    def observed_successes(self, goal: str, tool_names: Sequence[str]) -> int:
        """Number of independently verified successes observed for this procedure."""
        try:
            return int(self._forge.observed_successes(goal, list(tool_names)))
        except Exception:
            return 0

    def can_promote(self, goal: str, tool_names: Sequence[str]) -> dict[str, Any]:
        """Return whether the promotion gate is satisfied (evidence-based)."""
        count = self.observed_successes(goal, tool_names)
        return {
            "goal": goal,
            "observed_successes": count,
            "required": self._min_successes,
            "promotable": count >= self._min_successes,
            "reason": "verified_successes_met" if count >= self._min_successes
                      else "need_more_verified_successes",
        }

    def promote(self, goal: str, tool_names: Sequence[str], *, candidate: Any = None,
                validate: bool = True) -> dict[str, Any]:
        """Promote a skill only if the gate is satisfied.

        A single run never satisfies the gate; it is reported as observed, not
        promoted. This makes the correctness rule (do not learn from one plausible
        run) structurally enforced rather than advisory.
        """
        gate = self.can_promote(goal, tool_names)
        if not gate["promotable"]:
            return {"success": False, "promoted": False,
                    "reason": gate["reason"], "required": self._min_successes,
                    "observed": gate["observed_successes"]}
        if candidate is None:
            return {"success": False, "promoted": False, "reason": "no_candidate",
                    "observed": gate["observed_successes"]}
        try:
            result = self._forge.install(candidate, validate=validate)
            return {"success": bool(result.get("success", result.get("installed", False))),
                    "promoted": bool(result.get("success", result.get("installed", False))),
                    "observed": gate["observed_successes"], "gate": gate,
                    "result": result}
        except Exception as exc:
            return {"success": False, "promoted": False, "reason": "install_failed",
                    "error": str(exc), "observed": gate["observed_successes"]}

    def quarantine(self, name: str, *, reason: str = "repeated_failure") -> dict[str, Any]:
        """Quarantine a skill after repeated failures (versioned, reversible)."""
        try:
            # SkillForge keeps a registry; mark the skill as quarantined if possible.
            reg = self._forge._registry()  # noqa: SLF001
            if name in reg and isinstance(reg[name], dict):
                reg[name]["quarantined"] = True
                reg[name]["quarantine_reason"] = reason
                self._forge._save_registry(reg)  # noqa: SLF001
                return {"success": True, "quarantined": True, "name": name, "reason": reason}
            return {"success": False, "quarantined": False, "name": name,
                    "reason": "not_installed"}
        except Exception:
            return {"success": False, "quarantined": False, "name": name}

    def ledger(self) -> dict[str, Any]:
        try:
            return self._forge.success_ledger()
        except Exception:
            return {}


_learning: Optional[LearningFacade] = None
_learning_lock = threading.Lock()


def get_learning(min_successes: int = DEFAULT_MIN_SUCCESSES) -> LearningFacade:
    global _learning
    with _learning_lock:
        if _learning is None or _learning._min_successes != min_successes:
            _learning = LearningFacade(min_successes=min_successes)
        return _learning
