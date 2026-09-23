"""Per-requirement oracles: what actually happened, checked one at a time.

A mission succeeding overall has never meant each requirement happened. The
engine used to set ``satisfied = True`` on every requirement in one loop when the
verdict came back good, and gave them all the same evidence.

These checks are deterministic and non-executing on purpose: they read the
filesystem and the evidence the run already produced. Anything that would run a
command belongs behind the ToolGateway and its policy gate, not here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evidence import EvidenceRecord
from .verifier_registry import SOURCE_OBSERVED

SATISFIED = "satisfied"
BREACHED = "breached"
UNOBSERVED = "unobserved"
CLAIMED = "claimed"


@dataclass
class OracleOutcome:
    """What an oracle saw, in a shape the mission and the evidence log both use."""

    satisfied: bool
    status: str
    detail: str
    kind: str
    target: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "status": self.status,
            "detail": self.detail,
            "oracle": self.kind,
            "target": self.target,
            "evidence": list(self.evidence),
        }


def _split(target: str) -> tuple[Path | None, str]:
    """``path`` or ``path::must contain`` — one string field, two parts."""
    raw = str(target or "")
    if "::" in raw:
        head, _, tail = raw.partition("::")
        return Path(head.strip()), tail
    return (Path(raw.strip()) if raw.strip() else None), ""


def _resolve(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else root / path


def _file_exists(target: str, ctx: dict[str, Any]) -> OracleOutcome:
    path, needle = _split(target)
    resolved = _resolve(path, ctx["workspace_root"]) if path else None
    base = {
        "kind": "file_exists",
        "target": target,
        "evidence": [f"{resolved} present" if resolved and resolved.exists() else f"{resolved} missing" if resolved else "no target"],
    }
    if resolved is None:
        return OracleOutcome(False, UNOBSERVED, "requirement declares no target path", **base)
    if not resolved.is_file():
        return OracleOutcome(False, BREACHED, f"required file is not on disk: {resolved}", **base)
    if needle:
        try:
            body = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return OracleOutcome(False, BREACHED, f"required file unreadable: {exc}", **base)
        if needle not in body:
            return OracleOutcome(False, BREACHED, f"{resolved} does not contain {needle!r}", **base)
        return OracleOutcome(True, SATISFIED, f"{resolved} contains {needle!r}", **base)
    return OracleOutcome(True, SATISFIED, f"{resolved} exists", **base)


def _file_nonempty(target: str, ctx: dict[str, Any]) -> OracleOutcome:
    path, _ = _split(target)
    resolved = _resolve(path, ctx["workspace_root"]) if path else None
    outcome = _file_exists(target, ctx)
    if not outcome.satisfied:
        return OracleOutcome(False, outcome.status, outcome.detail, kind="file_nonempty", target=target)
    try:
        size = resolved.stat().st_size if resolved else 0
    except OSError:
        size = 0
    if size == 0:
        return OracleOutcome(False, BREACHED, f"{resolved} exists but is empty", kind="file_nonempty", target=target, evidence=[f"{resolved} size 0"])
    return OracleOutcome(True, SATISFIED, f"{resolved} is {size} bytes", kind="file_nonempty", target=target, evidence=[f"{resolved} {size}B"])


def _artifact_present(target: str, ctx: dict[str, Any]) -> OracleOutcome:
    needle = str(target or "").strip().casefold()
    artifacts = [str(a) for a in (ctx.get("artifacts") or [])]
    hits = [a for a in artifacts if not needle or needle in a.casefold()]
    real = [a for a in hits if Path(a).is_absolute() and Path(a).exists() or (ctx["workspace_root"] / a).exists()]
    if not hits:
        return OracleOutcome(False, BREACHED, f"no reported deliverable matches {target!r}", kind="artifact_present", target=target)
    if not real:
        return OracleOutcome(False, BREACHED, f"{hits[0]} was reported but is not on disk", kind="artifact_present", target=target, evidence=hits[:3])
    return OracleOutcome(True, SATISFIED, f"deliverable present: {real[0]}", kind="artifact_present", target=target, evidence=real[:3])


def _observed_evidence(target: str, ctx: dict[str, Any]) -> OracleOutcome:
    needle = str(target or "").strip().casefold()
    records: list[EvidenceRecord] = list(ctx.get("evidence_records") or [])
    grounded = [r for r in records if r.source == SOURCE_OBSERVED]
    if not needle:
        return OracleOutcome(
            bool(grounded),
            SATISFIED if grounded else BREACHED,
            f"{len(grounded)} observed evidence record(s)" if grounded else "nothing was observed independently of the worker's text",
            kind="observed_evidence",
            evidence=[r.id for r in grounded[:5]],
        )
    hits = [r for r in grounded if needle in f"{r.check} {r.summary} {r.artifact_path}".casefold()]
    return OracleOutcome(
        bool(hits),
        SATISFIED if hits else BREACHED,
        f"{hits[0].id}: {hits[0].summary}" if hits else f"no observed evidence mentions {target!r}",
        kind="observed_evidence",
        target=target,
        evidence=[r.id for r in hits[:5]],
    )


ORACLES: dict[str, Callable[[str, dict[str, Any]], OracleOutcome]] = {
    "file_exists": _file_exists,
    "file_nonempty": _file_nonempty,
    "artifact_present": _artifact_present,
    "observed_evidence": _observed_evidence,
}


def evaluate(
    requirement: Any,
    *,
    workspace_root: Path,
    artifacts: list[str] | None = None,
    evidence_records: list[EvidenceRecord] | None = None,
    elapsed_s: float | None = None,
) -> OracleOutcome:
    """Check one requirement on its own terms; never inherit the mission verdict."""
    oracle = str(getattr(requirement, "oracle", "") or "").strip().lower()
    target = str(getattr(requirement, "target", "") or "")
    ctx = {
        "workspace_root": Path(workspace_root),
        "artifacts": list(artifacts or []),
        "evidence_records": list(evidence_records or []),
    }

    if not oracle:
        # No oracle means nothing checked this. It is reported as claimed, and it
        # is up to the caller to keep whatever overall verdict still applies.
        return OracleOutcome(False, CLAIMED, "no oracle declared for this requirement", kind="", target=target)

    check = ORACLES.get(oracle)
    if check is None:
        return OracleOutcome(False, UNOBSERVED, f"unknown oracle {oracle!r} (available: {', '.join(sorted(ORACLES))})", kind=oracle, target=target)

    outcome = check(target, ctx)
    deadline = getattr(requirement, "deadline_s", None)
    if deadline and elapsed_s is not None and elapsed_s > float(deadline) and not outcome.satisfied:
        return OracleOutcome(
            False,
            BREACHED,
            f"{outcome.detail} — and the {float(deadline):.0f}s window closed after {elapsed_s:.0f}s",
            kind=oracle,
            target=target,
            evidence=outcome.evidence,
        )
    return outcome


def apply(report: Any, *, workspace_root: Path, evidence_records: list[EvidenceRecord] | None = None, elapsed_s: float | None = None) -> dict[str, list[Any]]:
    """Evaluate every requirement and record it on the report.

    Returns ``{satisfied, breached, unverified}`` requirement ids so the caller can
    decide what a mixed result means — a mission that produced one required file
    and skipped another is not a clean success.
    """
    buckets: dict[str, list[Any]] = {SATISFIED: [], BREACHED: [], CLAIMED: [], UNOBSERVED: []}
    for requirement in report.requirements:
        outcome = evaluate(
            requirement,
            workspace_root=workspace_root,
            artifacts=report.artifacts,
            evidence_records=evidence_records,
            elapsed_s=elapsed_s,
        )
        requirement.status = outcome.status
        requirement.verified_by = outcome.evidence
        requirement.check_detail = outcome.detail
        if outcome.status == SATISFIED:
            requirement.satisfied = True
            requirement.evidence = outcome.evidence or [outcome.detail]
            buckets[SATISFIED].append(requirement.id)
        elif outcome.status == BREACHED:
            requirement.satisfied = False
            buckets[BREACHED].append(requirement.id)
        else:
            buckets[outcome.status if outcome.status in buckets else UNOBSERVED].append(requirement.id)
    return buckets
