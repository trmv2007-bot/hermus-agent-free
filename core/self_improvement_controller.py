"""Governed self-improvement control plane for HERMUS.

Turns reflection into bounded lessons, skill candidates and auditable change
proposals. It can prepare and evaluate changes automatically, but protected
control-plane changes require independent review and no code is silently
rewritten by this module.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import config
from .evolution import ChangeDecision, ChangeProposal, EvolutionLedger, EvolutionPolicy


class SelfImprovementController:
    def __init__(self, ledger_path: str | Path | None = None):
        self.policy = EvolutionPolicy()
        self.ledger = EvolutionLedger(Path(ledger_path or config.resolve_path("data/evolution_ledger.jsonl")))
        self._lock = threading.RLock()
        self._history_path = Path(config.resolve_path("data/self_improvement_controller.json"))
        self._history_path.parent.mkdir(parents=True, exist_ok=True)

    def propose(self, *, title: str, description: str, files: list[str],
                tests: list[str] | None = None, evidence: list[str] | None = None) -> dict[str, Any]:
        proposal = ChangeProposal(
            title=str(title)[:200],
            description=str(description)[:2000],
            files=list(files or []),
            tests=list(tests or []),
            evidence=list(evidence or []),
        )
        assessment = self.policy.assess(proposal, changed_content=description)
        self.ledger.append(proposal, assessment)
        record = {
            "proposal": {
                "id": proposal.proposal_id,
                "title": proposal.title,
                "description": proposal.description,
                "files": proposal.normalized_files(),
                "tests": proposal.tests,
                "evidence": proposal.evidence,
            },
            "assessment": assessment.to_dict(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._append_history(record)
        return record

    def record_reflection(self, reflection: dict[str, Any], improvements: list[dict[str, Any]]) -> dict[str, Any]:
        mistakes = reflection.get("mistakes", [])[:5]
        proposed = []
        for improvement in improvements[:5]:
            mistake = str(improvement.get("mistake") or "observed failure")
            fix = str(improvement.get("suggested_fix") or "")
            files = self._infer_files(mistake)
            proposed.append(self.propose(
                title=f"Improve recovery: {mistake[:100]}",
                description=f"Observed: {mistake}. Suggested improvement: {fix[:700]}",
                files=files,
                tests=["add regression test for the observed failure", "run targeted test suite"],
                evidence=[f"reflection_mistakes={len(mistakes)}"],
            ))
        return {
            "mistakes": len(mistakes),
            "improvements": len(improvements),
            "proposals": len(proposed),
            "auto_allowed": sum(p["assessment"]["decision"] == ChangeDecision.ALLOW.value for p in proposed),
            "review_required": sum(p["assessment"]["decision"] == ChangeDecision.REVIEW.value for p in proposed),
            "denied": sum(p["assessment"]["decision"] == ChangeDecision.DENY.value for p in proposed),
            "items": proposed,
        }

    @staticmethod
    def _infer_files(mistake: str) -> list[str]:
        text = mistake.lower()
        candidates = []
        if "tool" in text:
            candidates.append("tools/")
        if "voice" in text:
            candidates.append("core/voice/")
        if "memory" in text:
            candidates.append("core/memory/")
        if "scheduler" in text or "schedule" in text:
            candidates.append("scheduler/")
        if "browser" in text:
            candidates.append("tools/browser/")
        return candidates or ["core/"]

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = [json.loads(line) for line in self._history_path.read_text().splitlines() if line.strip()]
            except (OSError, ValueError):
                rows = []
        return rows[-max(1, min(100, int(limit))):][::-1]

    def status(self) -> dict[str, Any]:
        rows = self.history(100)
        counts = {"allow": 0, "review": 0, "deny": 0}
        for row in rows:
            decision = row.get("assessment", {}).get("decision")
            if decision in counts:
                counts[decision] += 1
        return {
            "proposals": len(rows),
            "decisions": counts,
            "recent": rows[:10],
            "protected_policy": "core.evolution.EvolutionPolicy",
        }

    def _append_history(self, record: dict[str, Any]) -> None:
        try:
            with self._history_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            pass


self_improvement_controller = SelfImprovementController()

__all__ = ["SelfImprovementController", "self_improvement_controller"]
