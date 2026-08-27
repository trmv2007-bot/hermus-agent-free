"""Deterministic Orchestrator (Phase 4, P2) — routes tasks by type + availability.

Replaces keyword heuristics with a table-driven router: task type + mode +
worker availability -> single / council / fleet / subagents, and the fleet
strategy (fanout | race | map | auto). Zero LLM calls, fully deterministic.
"""
from __future__ import annotations

import re

from ..config import config

_RESEARCH_KW = (
    "research", "find", "search", "investigat", "gather", "compare", "look up",
    "what is", "who is", "where", "when", "sources", "information about",
)
_CODE_KW = (
    "write", "code", "python", "function", "script", "program", "implement",
    "debug", "refactor", "fix this", "bug", "api", "class",
)
_ANALYSIS_KW = (
    "analy", "review", "evaluate", "recommend", "decide", "plan", "strategy",
    "compare", "risk", "pros and cons", "roadmap", "architecture",
)
_MULTI_PART = re.compile(r"^\s*(\d+[\.\):]|[-*])\s+", re.M)
_AND_THEN = re.compile(r"\band then\b|\bthen\b|\band\b", re.I)

_FLEET_BY_TYPE = {
    "research": "fanout",    # same question -> many models -> consensus
    "analysis": "map",       # split facets -> parallel -> merge
    "code": "map",
    "simple": "race",        # first healthy model wins (fast)
}


class Router:
    """Table-driven orchestrator — deterministic, no LLM call."""

    def classify(self, goal: str) -> str:
        low = (goal or "").lower()
        score = {"research": 0, "code": 0, "analysis": 0, "simple": 0}
        for k in _RESEARCH_KW:
            if k in low:
                score["research"] += 1
        for k in _CODE_KW:
            if k in low:
                score["code"] += 1
        for k in _ANALYSIS_KW:
            if k in low:
                score["analysis"] += 1
        if len(low) < 25:
            score["simple"] += 2
        if _MULTI_PART.search(goal or ""):
            score["analysis"] += 1
        if _AND_THEN.search(low):
            score["analysis"] += 1
        best = max(score, key=score.get)
        return best if score[best] > 0 else "simple"

    def fleet_strategy(self, goal: str, mode: str = "agent") -> str:
        """Deterministic fleet strategy for multi-key/multi-model dispatch."""
        t = self.classify(goal)
        if mode == "multi-chat":
            # multi-chat wants accuracy: same prompt to many models, then consensus
            return "fanout"
        if mode == "multi-agent":
            # multi-agent wants goal completion: split into subtasks, parallel, merge
            return "map"
        return _FLEET_BY_TYPE.get(t, "auto")

    def route(self, goal: str, mode: str = "agent", workers: int = 0) -> dict:
        """Decide: single | council | fleet | subagents, with params."""
        t = self.classify(goal)
        multi = str(mode).startswith("multi")
        decision = "single"
        params: dict = {}
        if config.counsel_enabled and not multi and len((goal or "").strip()) >= 40:
            from ..reasoning.governor import governor

            cc = governor.council_config(goal, mode=mode)
            if cc:
                decision = "council"
                params = cc
        elif multi and workers >= 2:
            decision = "fleet"
            params = {"strategy": self.fleet_strategy(goal, mode)}
        elif multi and workers < 2:
            decision = "single"  # no diversity available; keep it simple
        elif t in ("research", "analysis") and len((goal or "").strip()) >= 120:
            decision = "subagents"
            params = {"splits": 2}
        return {
            "decision": decision,
            "task_type": t,
            "mode": mode,
            "workers": workers,
            "params": params,
        }


router = Router()
