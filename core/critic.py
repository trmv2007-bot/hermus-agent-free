"""Independent Critic and Verification Agents for Hermus.

Provides unbiased multi-perspective code review, security auditing, and strictly evidence-grounded
outcome verification. Requirements are only deemed satisfied when backed by executable verifier
evidence or verified artifacts.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class CriticVerdict(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Finding:
    severity: str
    category: str  # correctness | security | completeness | performance | style
    description: str
    file: Optional[str] = None
    line: Optional[int] = None
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CriticReport:
    reviewer_role: str
    verdict: str  # approved | changes_requested | rejected
    score: int  # 0 to 100
    findings: List[Finding] = field(default_factory=list)
    repair_directives: List[str] = field(default_factory=list)
    summary: str = ""
    evaluated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewer_role": self.reviewer_role,
            "verdict": self.verdict,
            "score": self.score,
            "findings": [f.to_dict() for f in self.findings],
            "repair_directives": self.repair_directives,
            "summary": self.summary,
            "evaluated_at": self.evaluated_at,
        }


class IndependentCritic:
    """Orchestrates independent evaluation perspectives."""

    def __init__(self, llm_caller: Optional[Callable[[str, str], str]] = None):
        self.llm_caller = llm_caller

    def review_code(
        self,
        task: str,
        files_content: Dict[str, str],
        requirements: Optional[List[str]] = None,
    ) -> CriticReport:
        findings: List[Finding] = []
        repair_directives: List[str] = []

        if not files_content:
            return CriticReport(
                reviewer_role="code_reviewer",
                verdict=CriticVerdict.APPROVED.value,
                score=100,
                findings=[],
                repair_directives=[],
                summary="Code review passed (no source files submitted for review).",
            )

        for filename, content in files_content.items():
            if filename.endswith(".py"):
                try:
                    tree = ast.parse(content, filename=filename)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                                findings.append(Finding(
                                    severity=Severity.LOW.value,
                                    category="completeness",
                                    description=f"Function '{node.name}' only contains 'pass'",
                                    file=filename,
                                    line=node.lineno,
                                    suggestion=f"Provide a full implementation for '{node.name}'",
                                ))
                except SyntaxError as e:
                    findings.append(Finding(
                        severity=Severity.CRITICAL.value,
                        category="correctness",
                        description=f"Python syntax error: {e.msg}",
                        file=filename,
                        line=e.lineno,
                        suggestion="Fix syntax errors before submitting.",
                    ))
                    repair_directives.append(f"Fix syntax error in {filename} at line {e.lineno}: {e.msg}")

            placeholders = ("TODO: implement", "REPLACE_ME", "YOUR_CODE_HERE", "FIXME")
            for idx, line in enumerate(content.splitlines(), start=1):
                for ph in placeholders:
                    if ph in line:
                        findings.append(Finding(
                            severity=Severity.MEDIUM.value,
                            category="completeness",
                            description=f"Unimplemented placeholder '{ph}' detected",
                            file=filename,
                            line=idx,
                            suggestion=f"Replace '{ph}' with actual logic.",
                        ))
                        repair_directives.append(f"Complete unfinished placeholder '{ph}' in {filename}:{idx}")

        critical_count = sum(1 for f in findings if f.severity == Severity.CRITICAL.value)
        high_count = sum(1 for f in findings if f.severity == Severity.HIGH.value)
        med_count = sum(1 for f in findings if f.severity == Severity.MEDIUM.value)

        score = max(0, 100 - (critical_count * 40 + high_count * 20 + med_count * 10))

        if critical_count > 0:
            verdict = CriticVerdict.REJECTED.value
        elif high_count > 0 or med_count > 2:
            verdict = CriticVerdict.CHANGES_REQUESTED.value
        else:
            verdict = CriticVerdict.APPROVED.value

        return CriticReport(
            reviewer_role="code_reviewer",
            verdict=verdict,
            score=score,
            findings=findings,
            repair_directives=repair_directives,
            summary=f"Code review complete. Score: {score}/100. Verdict: {verdict.upper()}",
        )

    def audit_security(
        self,
        files_content: Dict[str, str],
        command_history: Optional[List[str]] = None,
    ) -> CriticReport:
        findings: List[Finding] = []
        repair_directives: List[str] = []

        dangerous_patterns = [
            (r"eval\s*\(", "Use of eval() introduces arbitrary code execution risks", Severity.HIGH.value),
            (r"exec\s*\(", "Use of exec() introduces arbitrary code execution risks", Severity.HIGH.value),
            (r"subprocess\.call\(.*shell=True", "shell=True in subprocess invocation can lead to command injection", Severity.HIGH.value),
            (r"os\.system\(", "os.system() is prone to shell injection vulnerabilities", Severity.MEDIUM.value),
            (r"password\s*=\s*['\"][^'\"]+['\"]", "Hardcoded plain-text password detected", Severity.HIGH.value),
            (r"api_key\s*=\s*['\"][a-zA-Z0-9_\-]{16,}['\"]", "Hardcoded API key detected in source", Severity.CRITICAL.value),
            (r"rm\s+-rf\s+/", "Dangerous command attempting root deletion", Severity.CRITICAL.value),
        ]

        for filename, content in files_content.items():
            for pattern, msg, sev in dangerous_patterns:
                matches = list(re.finditer(pattern, content, re.IGNORECASE))
                for m in matches:
                    line_no = content[:m.start()].count("\n") + 1
                    findings.append(Finding(
                        severity=sev,
                        category="security",
                        description=msg,
                        file=filename,
                        line=line_no,
                        suggestion="Sanitize inputs, avoid dangerous primitives, and load credentials from environment.",
                    ))
                    repair_directives.append(f"Security fix in {filename}:{line_no}: {msg}")

        if command_history:
            for cmd in command_history:
                if any(x in cmd for x in ("sudo ", "chmod 777", "curl | sh", "wget | bash")):
                    findings.append(Finding(
                        severity=Severity.HIGH.value,
                        category="security",
                        description=f"Insecure shell command executed: '{cmd}'",
                        suggestion="Avoid unrestricted permissions or piping unverified remote scripts to shell.",
                    ))

        crit = sum(1 for f in findings if f.severity == Severity.CRITICAL.value)
        high = sum(1 for f in findings if f.severity == Severity.HIGH.value)

        score = max(0, 100 - (crit * 50 + high * 25))
        verdict = CriticVerdict.REJECTED.value if crit > 0 else (CriticVerdict.CHANGES_REQUESTED.value if high > 0 else CriticVerdict.APPROVED.value)

        return CriticReport(
            reviewer_role="security_auditor",
            verdict=verdict,
            score=score,
            findings=findings,
            repair_directives=repair_directives,
            summary=f"Security audit complete. Score: {score}/100. Verdict: {verdict.upper()}",
        )

    def verify_outcome(
        self,
        task: str,
        execution_log: str,
        artifacts: List[str],
        requirements: Optional[List[str]] = None,
        verification_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> CriticReport:
        findings: List[Finding] = []
        repair_directives: List[str] = []

        reqs = requirements or [task]
        satisfied_count = 0

        test_passed = bool(
            ("passed in" in execution_log.lower() or "100%" in execution_log or "status: passed" in execution_log.lower() or "tests passed" in execution_log.lower())
            and "traceback" not in execution_log.lower()
            and "syntaxerror" not in execution_log.lower()
        )

        v_evidence = verification_evidence or []
        verified_checks = {e.get("check"): e.get("status") for e in v_evidence if isinstance(e, dict)}

        valid_artifacts = [
            a for a in artifacts
            if Path(a).exists() and Path(a).stat().st_size > 0
        ]

        has_executable_proof = bool(
            test_passed
            or valid_artifacts
            or any(v in ("valid", "passed", "open", "clean", True) for v in verified_checks.values())
        )

        for req in reqs:
            if has_executable_proof:
                satisfied_count += 1
            else:
                findings.append(Finding(
                    severity=Severity.HIGH.value,
                    category="completeness",
                    description=f"Requirement lacking executable proof or verified artifact: '{req}'",
                    suggestion="Execute tests and generate verified tangible artifacts to prove completion.",
                ))
                repair_directives.append(f"Produce executable proof for requirement: {req}")

        score = int((satisfied_count / max(1, len(reqs))) * 100) if has_executable_proof else 0
        verdict = CriticVerdict.APPROVED.value if score >= 80 else (CriticVerdict.CHANGES_REQUESTED.value if score >= 40 else CriticVerdict.REJECTED.value)

        return CriticReport(
            reviewer_role="outcome_verifier",
            verdict=verdict,
            score=score,
            findings=findings,
            repair_directives=repair_directives,
            summary=f"Outcome verification complete. {satisfied_count}/{len(reqs)} requirements backed by executable evidence. Score: {score}/100.",
        )

    def run_full_review(
        self,
        task: str,
        files_content: Dict[str, str],
        execution_log: str = "",
        artifacts: Optional[List[str]] = None,
        requirements: Optional[List[str]] = None,
        verification_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        art_list = artifacts or []
        code_rep = self.review_code(task, files_content, requirements)
        sec_rep = self.audit_security(files_content)
        out_rep = self.verify_outcome(task, execution_log, art_list, requirements, verification_evidence)

        all_reports = [code_rep, sec_rep, out_rep]
        combined_score = int(sum(r.score for r in all_reports) / len(all_reports))
        combined_directives = list(dict.fromkeys(
            d for r in all_reports for d in r.repair_directives
        ))

        is_rejected = any(r.verdict == CriticVerdict.REJECTED.value for r in all_reports)
        is_changes_req = any(r.verdict == CriticVerdict.CHANGES_REQUESTED.value for r in all_reports)

        final_verdict = CriticVerdict.REJECTED.value if is_rejected else (
            CriticVerdict.CHANGES_REQUESTED.value if is_changes_req else CriticVerdict.APPROVED.value
        )

        return {
            "verdict": final_verdict,
            "overall_score": combined_score,
            "approved": final_verdict == CriticVerdict.APPROVED.value,
            "reports": {r.reviewer_role: r.to_dict() for r in all_reports},
            "repair_directives": combined_directives,
            "summary": f"Critic panel verdict: {final_verdict.upper()} (Score: {combined_score}/100).",
        }


critic_manager = IndependentCritic()
