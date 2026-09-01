"""Machine-readable Hermus red-line policy.

The Markdown documents are the human constitution. This module is the small,
stdlib-only loader used by tests and future enforcement code to read the same
policy from ``policies/red_lines.json`` without adding a YAML dependency.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "policies" / "red_lines.json"


@dataclass(frozen=True)
class RedLineRule:
    id: int
    key: str
    title: str
    rule: str
    green: tuple[str, ...] = ()
    yellow: tuple[str, ...] = ()
    red: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RedLineRule":
        return cls(
            id=int(data["id"]),
            key=str(data["key"]),
            title=str(data["title"]),
            rule=str(data["rule"]),
            green=tuple(str(x) for x in data.get("green", ())),
            yellow=tuple(str(x) for x in data.get("yellow", ())),
            red=tuple(str(x) for x in data.get("red", ())),
        )


@dataclass(frozen=True)
class SafetyPolicy:
    version: str
    name: str
    summary: str
    zones: dict[str, str]
    rules: tuple[RedLineRule, ...]
    protected_paths: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SafetyPolicy":
        return cls(
            version=str(data["version"]),
            name=str(data["name"]),
            summary=str(data["summary"]),
            zones={str(k): str(v) for k, v in data.get("zones", {}).items()},
            rules=tuple(RedLineRule.from_dict(item) for item in data.get("rules", ())),
            protected_paths=tuple(str(x) for x in data.get("protected_paths", ())),
        )

    def rule(self, key: str) -> RedLineRule:
        for item in self.rules:
            if item.key == key:
                return item
        raise KeyError(key)


@dataclass(frozen=True)
class ActionSafetyAssessment:
    """Deterministic red-line classification for one requested action."""

    zone: str
    red_lines: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()
    suggested_decision: str = "allow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone": self.zone,
            "red_lines": list(self.red_lines),
            "reasons": list(self.reasons),
            "suggested_decision": self.suggested_decision,
        }


@lru_cache(maxsize=4)
def load_safety_policy(path: str | Path = DEFAULT_POLICY_PATH) -> SafetyPolicy:
    raw = Path(path).read_text(encoding="utf-8")
    return SafetyPolicy.from_dict(json.loads(raw))


def assess_tool_action(tool_name: str, args: dict[str, Any] | None = None) -> ActionSafetyAssessment:
    """Classify a tool request into green/yellow/red red-line zone.

    This is intentionally conservative and deterministic. It does **not** decide
    whether the user truly owns a target; it spots actions that need explicit
    scope/approval or that are red-line violations by their shape. Runtime
    permission managers can combine this with configured grants.
    """
    args = args or {}
    text = _action_text(tool_name, args)
    reasons: list[str] = []
    red_lines: set[int] = set()
    zone = "green"

    def mark(next_zone: str, line: int, reason: str) -> None:
        nonlocal zone
        red_lines.add(line)
        reasons.append(reason)
        if next_zone == "red" or zone != "red" and next_zone == "yellow":
            zone = next_zone

    # Red Line 1: shutdown/control evasion.
    if re.search(r"\b(disable|bypass|ignore|kill|remove|turn off)\b.{0,40}\b(emergency[-_ ]?stop|shutdown|cancel|revocation|pause)\b", text):
        mark("red", 1, "attempts to disable or evade shutdown/revocation controls")

    # Red Line 2: audit/history concealment.
    if re.search(r"\b(delete|wipe|remove|falsify|hide|corrupt|tamper)\b.{0,45}\b(audit|ledger|log|history|evidence|mission record|tool history)\b", text):
        mark("red", 2, "attempts to hide, delete, or falsify audit/evidence history")

    # Red Line 3: private data/secret handling. Broad local scans are yellow;
    # exfiltration/logging/committing obvious secrets is red.
    # ``~`` is deliberately not wrapped in a word-boundary alternation: a raw
    # ``~/Documents`` path has a non-word boundary before the tilde in
    # ``path=~/documents``, so ``\b~`` silently fails to match and the action
    # is incorrectly classified green.
    if re.search(
        r"(?:home directory|~|/home/|/users/|/documents/|/downloads/|/desktop/|"
        r"documents?|downloads?|desktops?|browser profile|\.ssh|\.env|id_rsa|credentials?)",
        text,
    ):
        mark("yellow", 3, "touches broad/private local data and needs approved scope/purpose")
    if re.search(r"\b(upload|post|send|publish|commit|log|paste)\b.{0,50}\b(secret|credential|token|api key|\.env|id_rsa|private key|cookie)\b", text):
        mark("red", 3, "would expose or persist secrets/private data outside approved purpose")

    # Red Line 4: abuse/security. Authorized security tools can be yellow, but
    # credential theft/phishing/spam/malware deployment are red by shape.
    if re.search(r"\b(port scan|network scan|vulnerability scan|exploit|pentest|scrape|malware analysis|reverse engineer)\b", text):
        mark("yellow", 4, "security/discovery action needs owned/administered/in-scope target")
    if re.search(r"\b(phishing|credential theft|steal (?:token|cookie|password)|spam campaign|botnet|deploy malware|ransomware|keylogger|market manipulation|insider trading)\b", text):
        mark("red", 4, "matches unlawful abuse or real-system malware/credential misuse")

    # Red Line 5: destructive/unrecoverable actions.
    if re.search(r"\b(rm\s+-rf\s+/(?:\s|$)|mkfs|dd\s+if=|wipe disk|format disk|delete backup|drop database|force push)\b", text):
        mark("red", 5, "destructive or potentially unrecoverable action")
    elif re.search(r"\b(delete|overwrite|reset|destroy|wipe|remove)\b.{0,40}\b(database|backup|config|user file|repository|production)\b", text):
        mark("yellow", 5, "destructive action needs approval and recovery path")

    # Red Line 6: wallet/spending/trading.
    if re.search(r"\b(wallet|spend|purchase|buy|subscribe|transfer|trade|stock|crypto|invest|paid compute|cloud bill)\b", text):
        mark("yellow", 6, "financial action needs isolated agent wallet/account, ledger, reserve, and risk limits")
    if re.search(r"\b(personal bank|credit card|debit card|empty (?:the )?wallet|launder|pump and dump|insider trading)\b", text):
        mark("red", 6, "financial action violates isolated wallet/lawful earning boundaries")

    # Red Line 7: safety layer changes. Protected paths are yellow for review;
    # weakening/bypass language is red.
    paths = " ".join(str(v) for k, v in args.items() if k in {"path", "file", "filename", "target"})
    if _touches_protected_policy_path(paths):
        mark("yellow", 7, "touches protected red-line/safety control plane and requires review")
    if re.search(r"\b(disable|bypass|weaken|skip|remove|turn off)\b.{0,50}\b(permission|approval|sandbox|red[-_ ]?line|safety|secret protection|audit|capability escalation)\b", text):
        mark("red", 7, "attempts to weaken or bypass safety enforcement")

    # Red Line 8: broad/third-party reach.
    if re.search(r"\b(random (?:network|ip|ips)|internet-wide|third-party system|private system|local network|subnet|account|device)\b", text):
        mark("yellow", 8, "broad reach needs explicit authorized scope")
    if re.search(r"\b(use leaked credentials|persist on|backdoor|unauthorized access|scan random internet)\b", text):
        mark("red", 8, "reaches third-party systems without authorization or adds persistence")

    # Red Line 9: delegated communication/identity.
    if re.search(r"\b(send email|send message|reply as|post as|negotiate|submit form|publish post|transact on behalf)\b", text):
        mark("yellow", 9, "delegated communication/external action needs authorization or pre-approved rule")
    if re.search(r"\b(social engineer|impersonate to get access|fake account|pretend to be someone else|harass)\b", text):
        mark("red", 9, "abusive impersonation or social engineering")

    # Red Line 10: fake evidence.
    if re.search(r"\b(fake|fabricate|invent|pretend)\b.{0,40}\b(test result|evidence|citation|permission|success|observation)\b", text):
        mark("red", 10, "requests fabricated certainty/evidence")

    # Red Line 11: silent power gain.
    if re.search(r"\b(activate|grant|enable|install|add)\b.{0,45}\b(connector|permission|capability|power|admin|scope)\b", text):
        mark("yellow", 11, "capability expansion must be visible, approved, and logged")
    if re.search(r"\b(silently|secretly|without logging|without approval)\b.{0,45}\b(activate|grant|enable|escalate|permission|capability|power)\b", text):
        mark("red", 11, "silent capability gain or escalation")

    suggested = "deny" if zone == "red" else "ask" if zone == "yellow" else "allow"
    return ActionSafetyAssessment(zone=zone, red_lines=tuple(sorted(red_lines)), reasons=tuple(reasons), suggested_decision=suggested)


def _action_text(tool_name: str, args: dict[str, Any]) -> str:
    parts = [tool_name]
    for key, value in sorted(args.items()):
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={value}")
        elif isinstance(value, (list, tuple)):
            parts.append(f"{key}=" + " ".join(str(x) for x in value[:20]))
        elif isinstance(value, dict):
            parts.append(f"{key}=" + " ".join(f"{k}:{v}" for k, v in list(value.items())[:20]))
    return " ".join(parts).lower()


def _touches_protected_policy_path(paths: str) -> bool:
    if not paths:
        return False
    normalized = paths.replace("\\", "/").lower()
    protected_tokens = (
        "red_lines.md",
        "autonomy_boundaries.md",
        "capability_ledger.md",
        "policies/red_lines.json",
        "core/safety_policy.py",
        "core/permissions.py",
        "core/sandbox.py",
        "core/evolution.py",
        "test_red_lines_policy.py",
        "safety",
    )
    return any(token in normalized for token in protected_tokens)


__all__ = [
    "DEFAULT_POLICY_PATH",
    "ActionSafetyAssessment",
    "RedLineRule",
    "SafetyPolicy",
    "assess_tool_action",
    "load_safety_policy",
]
