"""A receipt for what a turn changed, so "done" is a claim with evidence.

A turn that only talks proves nothing, and a turn that claims work it did not do
is worse than a failure. So every turn that changed something — or said it did —
has to leave a receipt: the command and its exit status, the file and its hash,
the URL and what came back. When no evidence can be produced, the receipt says
so in the answer instead of letting the turn report success.

The verdict itself is :class:`core.critic.IndependentCritic`'s: the repo already
owns an outcome verifier that refuses requirements without executable proof, and
this module's job is to feed it real evidence rather than a paragraph of prose.

A failed receipt earns exactly one repair attempt. Never a second, and never a
silent success — after the repair the receipt is recomputed and the answer
carries whatever it says.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: tools whose success means the world changed
MUTATING_TOOLS = frozenset(
    {
        "file_write",
        "file_edit",
        "append_file",
        "move_file",
        "delete_file",
        "shell_execute",
        "sandbox_run",
        "write_file",
        "edit_file",
    }
)

#: tools whose result is a fact worth citing
WEB_TOOLS = frozenset(
    {"web_search", "web_fetch", "web_read", "web_crawl", "web_extract", "web_search_and_extract", "research_deep"}
)

#: verbs that assert a change happened ("I wrote …", "the file is created")
_CLAIM_RE = re.compile(
    r"\b(?:i|we|it)\b[^.\n]{0,40}?\b(?:wrote|written|created|saved|updated|edited|modified|renamed|moved|"
    r"deleted|removed|ran|executed|installed|generated|added)\b",
    re.I,
)

#: a path token in the answer plus a completion word is a claim too
_COMPLETION_WORDS = ("done", "completed", "finished", "successfully", "ready", "here you go")

#: a turn that narrated its own action instead of doing it ("I will write …")
_INTENT_CLAIM_RE = re.compile(
    r"\b(?:i will|i'll|i am going to|i'm going to|let me|now i will|next i will)\b[^.\n]{0,40}?"
    r"\b(?:write|create|save|update|edit|rename|move|delete|run|execute|install|generate|add)\b",
    re.I,
)

#: an explicit, first-person refusal is a legitimate end to an action request.
#: Third-person statements ("you cannot delete a directory") explain what a tool
#: did; they are not the companion declining, so they never match here.
_REFUSAL_RE = re.compile(
    r"\b(?:i (?:won't|will not|cannot|can't|am not able to|am unable to|refuse to|am declining|decline to)|"
    r"i'd rather not|i would not)\b",
    re.I,
)

#: tools whose *attempt* on a person's data must be accounted for
DESTRUCTIVE_TOOLS = frozenset({"delete_file"})

#: shell commands that destroy data
_DESTRUCTIVE_CMD_RE = re.compile(
    r"\b(?:rm\s+-[rf]{1,2}|del\s+/[fsq]|rmdir\s+/s|format\s+[a-z]:|remove-item\b.*-recurse|truncate)\b",
    re.I,
)

MAX_REPAIR_ATTEMPTS = 1


@dataclass
class Evidence:
    """One verifiable fact about the turn."""

    kind: str  # command | file | web
    substantiated: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ok": self.substantiated, "detail": self.detail, "data": dict(self.data)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(path_value: str, base_dir: Path) -> Path | None:
    try:
        candidate = Path(str(path_value))
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        return candidate if candidate.exists() else None
    except Exception:
        return None


def _file_evidence(name: str, result: dict, base_dir: Path) -> Evidence:
    """Read the file back and hash what is actually on disk."""
    path_value = str(result.get("path") or result.get("file") or "")
    resolved = _resolve(path_value, base_dir) if path_value else None
    if resolved is None:
        return Evidence(
            kind="file",
            substantiated=False,
            detail=f"{name} reported success for {path_value or 'an unnamed path'}, but the file is not there",
            data={"tool": name, "path": path_value},
        )
    if resolved.is_dir():
        entries = sorted(p.name for p in resolved.iterdir())
        return Evidence(
            kind="file",
            substantiated=True,
            detail=f"{resolved} is a directory with {len(entries)} entries",
            data={"tool": name, "path": str(resolved), "entries": entries[:20]},
        )
    digest = _sha256(resolved)
    size = resolved.stat().st_size
    return Evidence(
        kind="file",
        substantiated=True,
        detail=f"{resolved} sha256:{digest[:16]} {size}B",
        data={"tool": name, "path": str(resolved), "sha256": digest, "size": size},
    )


def _command_evidence(name: str, result: dict) -> Evidence:
    command = str(result.get("command") or "")
    returncode = result.get("returncode")
    ok = bool(result.get("success")) and returncode in (0, None) and not result.get("error")
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    detail = f"$ {command} -> exit {returncode if returncode is not None else '?'}"
    if stdout:
        detail += f"; stdout: {stdout[:160]!r}"
    if not ok and (stderr or result.get("error")):
        detail += f"; error: {(stderr or str(result.get('error')))[:160]!r}"
    return Evidence(
        kind="command",
        substantiated=ok,
        detail=detail,
        data={"tool": name, "command": command, "returncode": returncode, "stdout": stdout[:2000]},
    )


def _web_evidence(name: str, result: dict) -> Evidence:
    url = str(result.get("url") or result.get("source") or "")
    text = str(result.get("content") or result.get("text") or result.get("results") or "")
    ok = not result.get("error") and bool(text)
    detail = f"{name} {url or '(no url)'} -> " + (f"{len(text)} chars" if ok else "nothing returned")
    return Evidence(kind="web", substantiated=ok, detail=detail, data={"tool": name, "url": url})


def collect_evidence(tool_results: list[dict[str, Any]] | None, *, base_dir: Path | None = None) -> list[Evidence]:
    """Turn a turn's tool results into facts someone else could re-check."""
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    evidence: list[Evidence] = []
    for entry in tool_results or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("tool") or "")
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        if name in MUTATING_TOOLS and "error" not in result:
            if result.get("path") or result.get("file"):
                evidence.append(_file_evidence(name, result, base))
            elif result.get("command") is not None:
                evidence.append(_command_evidence(name, result))
        elif name == "shell_execute":
            evidence.append(_command_evidence(name, result))
        elif name in WEB_TOOLS and "error" not in result:
            evidence.append(_web_evidence(name, result))
    return evidence


def claims_change(answer: str) -> bool:
    """Does the answer assert that something was changed — or that it did it?"""
    text = str(answer or "")
    if not text.strip():
        return False
    if _CLAIM_RE.search(text) or _INTENT_CLAIM_RE.search(text):
        return True
    low = text.lower()
    has_path = bool(re.search(r"[\w./\\-]+\.(?:txt|md|json|csv|py|js|html|log|yml|yaml|toml|ini)\b", text))
    return has_path and any(w in low for w in _COMPLETION_WORDS)


def refuses(answer: str) -> bool:
    """Does the answer decline in the first person, instead of pretending?"""
    return bool(_REFUSAL_RE.search(str(answer or "")))


def destructive_attempts(tool_results: list[dict[str, Any]] | None) -> list[str]:
    """Tool names that were pointed at destroying data, whatever the outcome."""
    attempts: list[str] = []
    for entry in tool_results or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("tool") or "")
        if name in DESTRUCTIVE_TOOLS:
            attempts.append(f"{name}({str((entry.get('args') or {}).get('path') or '')[:60]})")
        elif name in ("shell_execute", "sandbox_run"):
            command = str((entry.get("args") or {}).get("command") or "")
            if _DESTRUCTIVE_CMD_RE.search(command):
                attempts.append(f"{name}({command[:60]})")
    return attempts


@dataclass
class Receipt:
    """What this turn proved, and what it could not."""

    verified: bool
    verdict: str
    claim: str
    evidence: list[Evidence] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    changed: bool = False
    repair_required: bool = False
    critic_verdict: str = ""
    critic_score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "verdict": self.verdict,
            "claim": self.claim,
            "evidence": [e.to_dict() for e in self.evidence],
            "problems": list(self.problems),
            "changed": self.changed,
            "repair_required": self.repair_required,
            "critic_verdict": self.critic_verdict,
            "critic_score": self.critic_score,
        }

    def summary_line(self) -> str:
        """One line a person can read, in the answer or the room."""
        if self.verified:
            return f"Verified: {self.evidence[0].detail}" if self.evidence else f"Verified: {self.claim}"
        return "Not verified: " + ("; ".join(self.problems) or self.claim)


def build_receipt(
    task: str,
    tool_results: list[dict[str, Any]] | None,
    answer: str,
    *,
    base_dir: Path | None = None,
    attempt: int = 0,
    expects_change: bool | None = None,
) -> Receipt:
    """Compose the receipt for one turn, using the critic's outcome verifier.

    ``expects_change`` is the request's own intent: an action request that
    changed nothing is *unmet*, not "nothing to check" — otherwise a turn that
    narrates the work it never did reads as a clean pass.
    """
    evidence = collect_evidence(tool_results, base_dir=base_dir)
    changed = any(e.kind in ("file", "command") for e in evidence) or any(
        str((tr or {}).get("tool") or "") in MUTATING_TOOLS
        and isinstance((tr or {}).get("result"), dict)
        and "error" not in (tr or {}).get("result", {})
        for tr in (tool_results or [])
    )
    claimed = claims_change(answer)
    declined = refuses(answer)
    attempts = destructive_attempts(tool_results)
    wants_change = claims_change(task) if expects_change is None else bool(expects_change)

    if attempts and not declined:
        return Receipt(
            verified=False,
            verdict="unaccounted_attempt",
            claim="a destructive action was attempted",
            evidence=evidence,
            problems=[f"tried to destroy data without saying why: {', '.join(attempts[:3])}"],
            changed=changed,
            repair_required=attempt < MAX_REPAIR_ATTEMPTS,
        )

    if not changed and declined:
        return Receipt(
            verified=True,
            verdict="refused",
            claim="the turn declined the request and said why",
            evidence=evidence,
            changed=False,
        )

    if not changed and wants_change:
        return Receipt(
            verified=False,
            verdict="unmet",
            claim="the request asked for a change and none happened",
            evidence=evidence,
            problems=["the request asked for a change, but no file or command result proves one"],
            changed=False,
            repair_required=attempt < MAX_REPAIR_ATTEMPTS,
        )

    if not changed and not claimed:
        return Receipt(
            verified=True,
            verdict="not_applicable",
            claim="this turn changed nothing, so there is nothing to check",
            evidence=evidence,
            changed=False,
        )

    if not changed and claimed:
        return Receipt(
            verified=False,
            verdict="unproven_claim",
            claim="the answer says work was done",
            evidence=evidence,
            problems=["the answer claims work was done, but no tool changed anything"],
            changed=False,
            repair_required=attempt < MAX_REPAIR_ATTEMPTS,
        )

    good = [e for e in evidence if e.substantiated]
    bad = [e for e in evidence if not e.substantiated]
    problems = [e.detail for e in bad] if bad else []

    critic_verdict, critic_score = "", 0
    try:
        from ..critic import critic_manager

        report = critic_manager.verify_outcome(
            task,
            execution_log="\n".join(e.detail for e in evidence),
            artifacts=[str((e.data or {}).get("path")) for e in good if (e.data or {}).get("path")],
            verification_evidence=[{"check": e.kind, "status": "valid" if e.substantiated else "invalid"} for e in evidence],
        )
        critic_verdict, critic_score = report.verdict, int(report.score)
    except Exception as exc:  # never let the verifier break the turn
        problems.append(f"the outcome verifier could not run: {type(exc).__name__}: {exc}")

    if not evidence:
        problems.append("nothing changed that could be read back — no file, command or fetch to cite")
    verified = bool(good) and not bad and not problems and critic_verdict != "rejected"

    kind_counts: dict[str, int] = {}
    for e in good:
        kind_counts[e.kind] = kind_counts.get(e.kind, 0) + 1
    claim = "verified " + ", ".join(f"{n} {kind}{'s' if n > 1 else ''}" for kind, n in kind_counts.items())

    return Receipt(
        verified=verified,
        verdict="verified" if verified else "unverified",
        claim=claim if verified else "the turn changed something it could not prove",
        evidence=evidence,
        problems=problems,
        changed=True,
        repair_required=not verified and attempt < MAX_REPAIR_ATTEMPTS,
        critic_verdict=critic_verdict,
        critic_score=critic_score,
    )


def repair_instruction(receipt: Receipt, original_task: str) -> str:
    """The one repair turn's instruction: fix the proof, or own the gap."""
    problems = "\n".join(f"- {p}" for p in receipt.problems) or "- the change could not be verified"
    return (
        "Your previous turn was not verified, so it does not count as done.\n"
        f"The original request was: {original_task[:400]}\n"
        "What went wrong:\n"
        f"{problems}\n\n"
        "Fix it now, using tools, so the result can be checked: make the change again and confirm it "
        "by reading the file or the command output back. If you genuinely cannot, say in one sentence "
        "exactly what you could not do and why. Do not claim success without evidence."
    )
