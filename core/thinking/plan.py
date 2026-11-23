"""A plan a person can see before anything is touched, updated as it verifies.

The repo already drafts plans (``core.reasoning.scaffold``) and the mission
engine already drives one. What was missing for a chat turn is *visibility*: a
multi-step request ran, and the first the person heard about the shape of the
work was the answer. A companion shows the list first, in four or five plain
lines, and keeps it honest while it works — a step is ``done`` only when a tool
result backs it, ``failed`` when one contradicts it, and the evidence says which.

Nothing here calls a model: the steps come from the request's own clauses, so
planning a turn costs zero tokens and the same request always produces the same
list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..reasoning.scaffold import Plan, PlanStep

#: step categories, in the order a person would expect to see them
CATEGORY_LABELS = {
    "investigate": "look",
    "edit": "change",
    "shell": "run",
    "web": "fetch",
    "verify": "verify",
    "answer": "answer",
}

#: tool name -> step category
TOOL_CATEGORIES = {
    "file_write": "edit",
    "file_edit": "edit",
    "append_file": "edit",
    "move_file": "edit",
    "delete_file": "edit",
    "shell_execute": "shell",
    "sandbox_run": "shell",
    "web_search": "web",
    "web_fetch": "web",
    "web_read": "web",
    "web_crawl": "web",
    "web_extract": "web",
    "web_search_and_extract": "web",
    "research_deep": "web",
    "file_read": "investigate",
    "file_search": "investigate",
}

_WEB_WORDS = ("search the web", "look up", "fetch", "download", "url", "http", "website", "web page", "webpage")
_SHELL_WORDS = ("run ", "command", "shell", "terminal", "install", "pip ", "npm ", "python -c", "execute")
_EDIT_WORDS = (
    "write",
    "create",
    "save",
    "edit",
    "rename",
    "move",
    "delete",
    "append",
    "replace",
    "update the file",
    "add to the file",
)
_PATH_RE = re.compile(r"[\w./\\-]+\.(?:txt|md|json|csv|py|js|html|log|yml|yaml|toml|ini)\b")
_SEQUENCE_RE = re.compile(r"\b(?:and then|then|after that|next,|finally|followed by)\b|;(?=\s)", re.I)

#: multi-step cues that make a plan worth showing even on an easy task
_MULTI_ACTION_RE = re.compile(
    r"\b(?:\w+)\b.{0,40}\b(?:and then|then|after that|finally)\b",
    re.I,
)


def _category_of(clause: str) -> str:
    low = (clause or "").lower()
    if any(w in low for w in _WEB_WORDS):
        return "web"
    if any(w in low for w in _SHELL_WORDS):
        return "shell"
    if _PATH_RE.search(clause or "") or any(w in low for w in _EDIT_WORDS):
        return "edit"
    return "investigate"


def _clauses(text: str) -> list[str]:
    """Split a request into ordered action clauses, dropping filler."""
    raw = (text or "").strip()
    if not raw:
        return []
    numbered = [
        re.sub(r"^\s*(?:\d+[.):]|[-*])\s*", "", line).strip()
        for line in raw.splitlines()
        if re.match(r"^\s*(?:\d+[.)]|[-*])\s+", line)
    ]
    if len(numbered) >= 2:
        return [c for c in numbered if len(c) > 2][:6]
    parts = [p.strip(" .") for p in _SEQUENCE_RE.split(raw) if p and len(p.strip(" .")) > 6]
    return parts[:6]


def needs_plan(text: str, *, mode: str = "agent", difficulty: int | None = None) -> bool:
    """Does this request need more than one action, and therefore a plan?"""
    raw = (text or "").strip()
    if not raw or str(mode or "").lower() in ("chat", "multi-chat"):
        return False
    try:
        from ..runtime import INTENT_ACTION, detect_intent

        intent = detect_intent(raw)
    except Exception:  # pragma: no cover - runtime is always importable
        intent = INTENT_ACTION if _category_of(raw) != "investigate" else ""
    if intent != INTENT_ACTION:
        return False
    # A change to the person's machine is always shown first — that is the
    # crew's rule that every mutating action is previewable, not a difficulty
    # question.
    if _category_of(raw) in ("edit", "shell"):
        return True
    if len(_clauses(raw)) >= 2:
        return True
    if _MULTI_ACTION_RE.search(raw):
        return True
    try:
        from ..reasoning.governor import governor

        diff = governor.classify_difficulty(raw) if difficulty is None else difficulty
    except Exception:  # pragma: no cover
        diff = difficulty or 1
    return int(diff) >= 3


def _template_for(category: str, goal: str) -> list[PlanStep]:
    short = goal[:150]
    if category == "edit":
        return [
            PlanStep(goal="Read the current state of what will change", action="investigate"),
            PlanStep(goal=f"Make the change: {short}", action="edit", verify="read the result back"),
            PlanStep(goal="Verify the change took effect", action="verify"),
        ]
    if category == "shell":
        return [
            PlanStep(goal=f"Run the command: {short}", action="shell", verify="check the exit status and output"),
            PlanStep(goal="Verify the output says what it should", action="verify"),
        ]
    if category == "web":
        return [
            PlanStep(goal=f"Fetch the source: {short}", action="web"),
            PlanStep(goal="Report what the source actually says", action="answer"),
        ]
    return [
        PlanStep(goal=f"Look into: {short}", action="investigate"),
        PlanStep(goal="Answer from what was found", action="answer"),
    ]


def build_turn_plan(text: str, *, session_id: str = "", difficulty: int = 3) -> Plan:
    """Deterministic plan for a turn: one step per clause, or a category template."""
    clauses = _clauses(text)
    plan = Plan(goal=(text or "")[:300], session_id=session_id, difficulty=int(difficulty))
    if len(clauses) >= 2:
        for clause in clauses:
            plan.steps.append(PlanStep(goal=clause[:200], action=_category_of(clause)))
        plan.steps.append(PlanStep(goal="Check the work against what was asked", action="verify"))
        plan.strategy = "react"
        return plan
    plan.steps = _template_for(_category_of(text), (text or "").strip())
    plan.strategy = "react"
    return plan


@dataclass
class PlanProgress:
    """Advance a plan from tool results, and keep the view a person sees short."""

    plan: Plan
    cursor: int = 0

    def observe(self, tool_name: str, ok: bool, detail: str = "") -> dict[str, Any] | None:
        """Mark the step a tool result belongs to. Returns a plan event, or None.

        A read before a change is expected and never advances a change step; a
        step is only matched to a tool of its own category. When no later step
        matches, the result belongs to work the plan did not name, and the plan
        is left alone rather than credited for it.
        """
        category = TOOL_CATEGORIES.get(tool_name)
        if not category:
            return None
        for idx in range(self.cursor, len(self.plan.steps)):
            step = self.plan.steps[idx]
            if step.status == "done":
                continue
            if step.action != category:
                continue
            step.status = "done" if ok else "failed"
            evidence = f"{tool_name} {'ok' if ok else 'failed'}" + (f" — {detail}" if detail else "")
            step.evidence.append(evidence)
            if idx == self.cursor:
                self.cursor = idx + 1
            return {
                "step": idx + 1,
                "goal": step.goal,
                "action": step.action,
                "status": step.status,
                "evidence": evidence,
                "of": len(self.plan.steps),
            }
        return None

    def settle(self) -> dict[str, Any]:
        """Close the plan: steps with no tool behind them are reported, never faked."""
        for idx, step in enumerate(self.plan.steps):
            if step.status in ("pending", "active"):
                step.status = "skipped"
                step.evidence.append("no tool result backed this step")
        plan_done = all(s.status in ("done", "skipped") for s in self.plan.steps)
        self.plan.status = "done" if plan_done else "active"
        return self.render()

    def render(self) -> list[dict[str, Any]]:
        return [
            {
                "n": idx + 1,
                "goal": step.goal,
                "action": step.action,
                "label": CATEGORY_LABELS.get(step.action, step.action),
                "status": step.status,
                "evidence": list(step.evidence),
            }
            for idx, step in enumerate(self.plan.steps)
        ]

    def verified_ratio(self) -> float:
        """Share of steps backed by a successful tool result (excluding verify)."""
        actionable = [s for s in self.plan.steps if s.action != "verify"]
        if not actionable:
            return 0.0
        return round(sum(1 for s in actionable if s.status == "done") / len(actionable), 2)
