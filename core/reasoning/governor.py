"""Meta-Cognition Governor — decides how hard Hermus should think for each task.

Free, deterministic, zero-token: pure heuristics classify a task 1-5, then the
budget table decides council size / rounds / plan-first / verification.

Difficulty scale:
  1 = greeting / trivial chat      -> no planning, no tools
  2 = simple question              -> plain ReAct loop
  3 = multi-step task              -> plan-first (DeepThink scaffold)
  4 = complex / research           -> convene the Council (standard)
  5 = very hard / open-ended       -> full Council (judge, more rounds, reconvene)
"""
from __future__ import annotations

import re
from typing import Optional

from ..config import config

# Keywords that push a task up the difficulty scale
_STRONG_HARD = (
    "research", "analy", "compar", "plan", "build", "create", "develop",
    "debug", "fix", "security", "pentest", "audit", "design", "implement",
    "architect", "review", "investigat", "strateg", "optimiz", "migrat",
    "refactor", "architect", "forecast", "report", "essay", "framework",
    "verif", "evaluat", "recommend", "deployment",
)
_MEDIUM_HARD = (
    "explain", "summar", "list", "write", "draft", "generate",
    "decide", "choose", "improve", "test", "detail", "example", "how to", "steps",
)
_EXPLICIT_HARD = ("complex", "hard", "thorough", "deep", "careful", "difficult", "extensive", "comprehensive", "critical", "in detail", "complete")

# Budget table: difficulty -> (max_members, max_rounds, plan_first, council)
_BUDGET_TABLE: dict[int, tuple[int, int, bool, bool]] = {
    1: (0, 0, False, False),
    2: (0, 0, False, False),
    3: (3, 2, True, False),    # mini deliberation can still help
    4: (5, 3, True, True),
    5: (6, 4, True, True),
}


class Governor:
    """Free, zero-token task classifier + thinking budget."""

    def classify_difficulty(self, text: str) -> int:
        """Heuristic difficulty 1-5. Deterministic, no LLM call, ~0 tokens."""
        text = text or ""
        low = text.lower()
        score = 1

        # Length signals real work
        if len(text) > 80:
            score += 1
        if len(text) > 200:
            score += 1

        # Explicit hard words
        if any(w in low for w in _EXPLICIT_HARD):
            score += 1

        # Numbered / multi-part requirements
        numbered = re.findall(r"(?m)^\s*(?:\d+[\.\):]|[-*])\s+", text)
        if len(numbered) >= 3:
            score += 1
        elif len(numbered) >= 1:
            score += 1 if len(numbered) >= 2 else 0

        # Keyword hits
        strong = sum(1 for k in _STRONG_HARD if k in low)
        medium = sum(1 for k in _MEDIUM_HARD if k in low)
        if strong >= 3:
            score += 2
        elif strong >= 1 or medium >= 2:
            score += 1

        # Very long + tool-flavored = hard
        if len(text) > 400 and strong >= 1:
            score += 1

        return max(1, min(5, score))

    def budget(self, difficulty: int) -> dict:
        """Thinking budget for a difficulty: members, rounds, plan_first, council."""
        b = _BUDGET_TABLE.get(difficulty, _BUDGET_TABLE[5])
        return {
            "difficulty": difficulty,
            "max_members": min(b[0], config.counsel_max_members),
            "max_rounds": min(b[1], config.counsel_max_rounds),
            "plan_first": b[2] and config.think_enabled,
            "council": b[3] and config.counsel_enabled,
        }

    def should_use_council(self, text: str, mode: str = "agent") -> bool:
        """Should we convene the Council for this task?"""
        if not config.counsel_enabled:
            return False
        if mode and str(mode).lower() in ("chat", "multi-chat"):
            return False  # chat modes stay fast; multi-chat uses fleet instead
        diff = self.classify_difficulty(text)
        if diff < config.counsel_min_difficulty:
            return False
        # Very short prompts aren't council material even if keyword-heavy
        if len((text or "").strip()) < 40:
            return False
        return True

    def council_config(self, text: str, mode: str = "agent") -> Optional[dict]:
        """Return convene parameters for the Council, or None if not worth it."""
        if not self.should_use_council(text, mode):
            return None
        diff = self.classify_difficulty(text)
        b = self.budget(diff)
        return {
            "difficulty": diff,
            "max_members": max(3, b["max_members"]),
            "max_rounds": max(1, b["max_rounds"]),
        }

    def should_plan_first(self, text: str, mode: str = "agent") -> bool:
        """DeepThink plan-first stage for multi-step tasks (not council-level)."""
        if not config.think_enabled:
            return False
        if mode and str(mode).lower() in ("chat", "multi-chat"):
            return False
        return self.classify_difficulty(text) >= 3

    # ------------------------------------------------------------ Phase 3

    # Per-difficulty share of the configured budget (config.max_tool_steps).
    # Absolute caps (2/4/6/8/12) starved hard tasks when max_tool_steps was
    # raised: a difficulty-5 goal could never use more than 12 tool rounds no
    # matter what HERMUS_MAX_TOOL_STEPS said. Shares scale with the budget.
    _STEP_SHARES = {1: 0.0625, 2: 0.125, 3: 0.25, 4: 0.5, 5: 1.0}
    _MIN_STEPS = {1: 2, 2: 2, 3: 4, 4: 6, 5: 8}

    def step_budget(self, text: str, mode: str = "agent") -> int:
        """Per-task max tool steps: easy tasks stay cheap, hard tasks get room."""
        if mode and str(mode).lower() in ("chat", "multi-chat"):
            return min(2, config.max_tool_steps)
        budget = max(1, int(getattr(config, "max_tool_steps", 8)))
        diff = self.classify_difficulty(text)
        share = self._STEP_SHARES.get(diff, 1.0)
        cap = max(self._MIN_STEPS.get(diff, 1), int(round(budget * share)))
        return min(cap, budget)

    def strategy_for(self, text: str, mode: str = "agent", council_used: bool = False) -> str:
        """Pick the deliberation strategy for a task (auto) or honor overrides.

        Returns: "none" | "reflexion" | "verify" | "self_consistency"
        """
        if council_used:
            return "none"  # the council already deliberated
        override = getattr(config, "think_strategy", "auto")
        if override != "auto":
            return override if override in STRATEGY_NAMES else "none"
        if not config.think_enabled:
            return "none"
        if mode and str(mode).lower() in ("chat", "multi-chat"):
            return "none"
        diff = self.classify_difficulty(text)
        if diff <= 2:
            return "none"
        if diff == 3:
            return "reflexion"
        if diff == 4:
            return "verify" if config.verify_threshold <= 4 else "reflexion"
        return "self_consistency"


STRATEGY_NAMES = ("none", "reflexion", "verify", "self_consistency")


governor = Governor()
