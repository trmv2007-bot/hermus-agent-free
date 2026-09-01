"""Controlled self-improvement and release policy.

This module is the *independent control layer* for agent-generated changes.  An
agent may propose and test almost any change, but it cannot use a proposal to
rewrite the rules that approve, observe, stop, or roll back the running system.

The policy is deliberately deterministic and has no LLM in the decision path.
It is safe to use before a GitHub PR, CI job, canary deployment, or local
checkout.  It does not push, merge, or deploy anything itself.
"""
from __future__ import annotations

import fnmatch
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional


class ChangeDecision(str, Enum):
    ALLOW = "allow"       # safe to develop/test automatically
    REVIEW = "review"     # proposal is valid, but needs independent approval
    DENY = "deny"         # cannot be changed by an autonomous run


@dataclass(frozen=True)
class ChangeAssessment:
    decision: ChangeDecision
    reasons: tuple[str, ...] = ()
    protected_files: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["decision"] = self.decision.value
        return data


@dataclass
class ChangeProposal:
    title: str
    description: str
    files: list[str]
    tests: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    branch: Optional[str] = None
    proposal_id: str = field(default_factory=lambda: f"evo_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def normalized_files(self) -> list[str]:
        """Return repository-relative, POSIX-style paths for deterministic checks."""
        out: list[str] = []
        for value in self.files:
            path = _normalize_repo_path(value)
            if path and path not in out:
                out.append(path)
        return out


# These are the brakes and the independent control plane.  Changes here may be
# proposed, but an autonomous process may not approve/deploy them itself.
RED_LINE_PATTERNS: tuple[str, ...] = (
    # Private credentials and repository internals.
    ".env*",
    "**/.env*",
    "**/*secret*",
    "**/*credential*",
    "**/*token*",
    "**/*password*",
    ".git/**",
    # The independent control plane: changes require review, but may be
    # proposed. Ordinary agent, UI, skill, test, and deployment code remains
    # green-line work.
    "RED_LINES.md",
    "AUTONOMY_BOUNDARIES.md",
    "CAPABILITY_LEDGER.md",
    "policies/**",
    "core/evolution.py",
    "core/permissions.py",
    "core/safety_policy.py",
    "core/emergency_stop.py",
    "core/capability_ledger.py",
    "core/autonomy_preflight.py",
    "core/capability_registry.py",
    "core/local_defense_scanner.py",
    "core/local_defense_workflow.py",
    "core/safety_report.py",
    "core/rollback.py",
    "core/sandbox.py",
    "core/computer/permissions.py",
    "core/computer/remote.py",
    "core/counsel/constitution.py",
    "tests/test_red_lines_policy.py",
    "tests/test_emergency_stop.py",
    "tests/test_execution_path_guards.py",
    "tests/test_approval_waiting_flow.py",
    "tests/test_capability_ledger.py",
    "tests/test_safety_events_timeline.py",
    "tests/test_safety_report.py",
    "tests/test_autonomy_preflight.py",
    "tests/test_mission_preflight.py",
    "tests/test_approval_bundles.py",
    "tests/test_capability_activation.py",
    "tests/test_local_defense_scanner.py",
    "tests/test_local_defense_workflow.py",
    "tests/test_*safety*.py",
    "deploy/secrets/**",
    "scripts/emergency_stop*",
    "scripts/rollback*",
)

# Content checks catch attempts to evade path rules by placing control changes
# in a new file or changing a setting through a generated config.
RED_LINE_CONTENT: tuple[tuple[str, str], ...] = (
    (r"(?i)HERMUS_?(?:PERMISSIONS|ASK_POLICY|SANDBOX|GATEWAY_TOKEN)", "security configuration"),
    (r"(?i)(?:disable|bypass|skip|turn off).{0,30}(?:approval|permission|audit|rollback|sandbox|emergency)", "control bypass"),
    (r"(?i)(?:rm\s+-rf|chmod\s+777|--privileged|/var/run/docker\.sock)", "dangerous host access"),
)


def _normalize_repo_path(value: object) -> str:
    """Normalize a path without stripping leading dots from filenames.

    ``str.lstrip('./')`` treats its argument as a character set, so it turns
    ``.env.example`` into ``env.example``. Red-line path checks must preserve
    leading dots while removing only explicit ``./`` prefixes.
    """
    path = str(value).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _matches(path: str, pattern: str) -> bool:
    # fnmatch's ** behavior differs slightly across Python versions; checking
    # both the full path and the same path under an arbitrary parent makes the
    # policy predictable for root and nested files.
    if fnmatch.fnmatch(path, pattern):
        return True
    return fnmatch.fnmatch(f"x/{path}", pattern)


class EvolutionPolicy:
    """Deterministic policy for agent-created code and release proposals."""

    def __init__(self, protected_patterns: Iterable[str] = RED_LINE_PATTERNS):
        self.protected_patterns = tuple(protected_patterns)

    def protected_files(self, files: Iterable[str]) -> list[str]:
        protected: list[str] = []
        for raw in files:
            path = _normalize_repo_path(raw)
            if any(_matches(path, pattern) for pattern in self.protected_patterns):
                protected.append(path)
        return sorted(set(protected))

    def assess(self, proposal: ChangeProposal, changed_content: str = "") -> ChangeAssessment:
        files = proposal.normalized_files()
        protected = self.protected_files(files)
        reasons: list[str] = []
        tags: list[str] = []

        if protected:
            reasons.append("proposal touches the independent control plane or supply-chain configuration")
            tags.append("protected-control-plane")

        for expression, tag in RED_LINE_CONTENT:
            if re.search(expression, changed_content or ""):
                reasons.append(f"content matches red-line rule: {tag}")
                tags.append(tag)

        if not files:
            reasons.append("proposal contains no repository files")
            return ChangeAssessment(ChangeDecision.DENY, tuple(reasons), tuple(protected), tuple(tags))
        if not proposal.tests:
            reasons.append("no tests or evaluation commands supplied")
            tags.append("missing-evaluation")

        # A protected change is never auto-approved.  A bypass attempt is denied;
        # ordinary core changes are reviewable, and low-risk changes can proceed
        # to sandbox testing.
        if any(tag in tags for tag in ("control bypass", "dangerous host access")):
            decision = ChangeDecision.DENY
        elif protected:
            decision = ChangeDecision.REVIEW
        elif not proposal.tests:
            decision = ChangeDecision.REVIEW
        else:
            decision = ChangeDecision.ALLOW
        return ChangeAssessment(decision, tuple(reasons), tuple(protected), tuple(tags))


class EvolutionLedger:
    """Append-only proposal ledger, kept outside the repository by default."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, proposal: ChangeProposal, assessment: ChangeAssessment) -> None:
        record = {
            "proposal": asdict(proposal),
            "assessment": assessment.to_dict(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


__all__ = [
    "ChangeAssessment",
    "ChangeDecision",
    "ChangeProposal",
    "EvolutionLedger",
    "EvolutionPolicy",
    "RED_LINE_PATTERNS",
]
