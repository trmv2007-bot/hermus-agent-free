"""What Hermus is, what it can actually do right now, and what it must not do.

An agent that describes itself from memory describes the version of itself that
existed when its prompt was written. This module exists so the chat prompt
carries a *measured* self-description instead: the name the person chose, the
tools the registry really holds, the capabilities the doctor's own probe says
are working, the ones it says are not, and the command that would fix each gap.

Three rules hold the whole thing up:

* **Truthfulness over confidence.** A capability appears as usable only when the
  registry holds its tools *and* the probe has no failing check standing in its
  way. A capability the doctor says is unavailable is never listed as usable —
  it moves to the "cannot do right now" line with the command that enables it,
  so the model offers instead of apologising.
* **Bounded.** The section is capped (:data:`MAX_SECTION_CHARS`) and
  deduplicated, because it is injected into every chat turn. When it does not
  fit, entries are dropped in a stated priority order rather than truncated
  mid-sentence.
* **Reused, not re-derived.** The probe is :func:`core.diagnostics.run_diagnostics`
  and the diagnosis is :class:`core.doctor.HermusDoctor`'s own findings. Nothing
  here writes a second opinion about the machine.

:func:`get_self_model` also carries the self-diagnosis pass the attention digest
and the Systems console read: at most three things broken now with their fix and
at most three worth adding, each carrying the probe result that proves it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Off with ``HERMUS_SELF_MODEL=0``; on everywhere else, because a prompt that
#: knows what it can do is the whole point of this module.
ENV_FLAG = "HERMUS_SELF_MODEL"

#: Hard cap on the injected section. Every turn pays for this text.
MAX_SECTION_CHARS = 1500

#: A probe costs imports and a couple of seconds; a cached answer that is ten
#: minutes old is still a fact about this machine, and the next turn must not
#: pay for it again.
PROBE_TTL_SECONDS = 600.0
DIAGNOSIS_TTL_SECONDS = 600.0

#: The most items either list may carry, on the prompt, the digest and the
#: Systems row alike.
MAX_ITEMS = 3


@dataclass(frozen=True)
class Family:
    """One group of tools that either works now or has one command standing in its way."""

    key: str
    label: str
    tools: tuple[str, ...]
    #: Name of the diagnostics check that must pass. ``None`` means the registry
    #: holding the tools is the whole proof (the probe raises no objection).
    gate: str | None = None
    #: The one command that enables the family when its gate fails.
    enable: str = ""


#: Ordered by how often a request needs them, so the cap drops the rarest last.
FAMILIES: tuple[Family, ...] = (
    Family("files", "files", ("file_read", "file_write", "file_edit", "file_search")),
    Family("shell", "commands", ("shell_execute", "sandbox_run")),
    Family("web", "web and research", ("web_search", "web_fetch", "research_deep")),
    Family("memory", "memory and skills", ("memory2_recall", "memory2_remember", "skill_use")),
    Family("missions", "missions and subagents", ("mission_start", "subagent_spawn", "harness_message")),
    Family(
        "browser",
        "browser automation",
        ("browser_navigate", "browser_click", "browser_screenshot"),
        gate="playwright",
        enable="pip install playwright && playwright install chromium",
    ),
    Family(
        "desktop",
        "desktop control",
        ("computer_action", "screen_analyze", "screen_action_before"),
        gate="desktop_control",
        enable="pip install pyautogui pygetwindow",
    ),
    Family(
        "voice",
        "voice transcription",
        ("transcribe_audio", "speech_synthesize"),
        gate="faster_whisper",
        enable="pip install faster_whisper",
    ),
)

#: What a missing capability leaves behind, kept where the model can see it.
TRY_RULE = (
    "When a request needs a capability, check your tool set and this list first and use what is there; "
    "when it is missing, name the one command above instead of apologising or failing quietly."
)


def _flag_on(value: str | None) -> bool:
    """``HERMUS_*`` boolean semantics: anything but 0/false means on."""
    return (value or "") not in ("0", "false", "False")


def enabled() -> bool:
    return _flag_on(os.environ.get(ENV_FLAG))


# ---------------------------------------------------------------------------
# The probe: what this machine can actually do, from the doctor's own checks
# ---------------------------------------------------------------------------
_probe_cache: dict[str, Any] = {}
_probe_lock = threading.Lock()


def run_probe(*, force: bool = False) -> dict[str, Any]:
    """The real capability probe, cached for :data:`PROBE_TTL_SECONDS`.

    Returns ``{"checks": {name: check}, "at": ts, "error": str}``. A probe that
    raises is not a green probe: the error is kept and every gated family is
    treated as unavailable, because "I could not check" is not "it works".
    """
    now = time.time()
    with _probe_lock:
        cached = _probe_cache.get("value")
        if cached and not force and now - _probe_cache.get("at", 0.0) < PROBE_TTL_SECONDS:
            return cached
    result: dict[str, Any] = {"checks": {}, "at": _now(), "error": ""}
    try:
        from .diagnostics import run_diagnostics

        report = run_diagnostics()
        result["checks"] = {str(c.get("name")): c for c in report.get("checks") or [] if c.get("name")}
    except Exception as exc:  # noqa: BLE001 - a broken probe must degrade, not crash a turn
        result["error"] = f"{type(exc).__name__}: {exc}"
    with _probe_lock:
        _probe_cache["value"] = result
        _probe_cache["at"] = now
    return result


def _tool_names() -> list[str]:
    """Real registered tool names, or ``[]`` when the registry cannot be read."""
    try:
        from .tool_registry import tool_registry

        return list(tool_registry.list_tools().get("tools") or [])
    except Exception:  # noqa: BLE001
        return []


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------
class SelfModel:
    """Assembles the self-description section, one turn at a time.

    ``probe`` and ``tools`` are injectable so a test can prove the truthfulness
    rule against a capability the doctor has declared broken without installing
    anything.
    """

    def __init__(
        self,
        *,
        probe: dict[str, Any] | None = None,
        tools: list[str] | None = None,
        identity: str | None = None,
        red_lines: list[str] | None = None,
        limit: int = MAX_SECTION_CHARS,
    ) -> None:
        self._probe = probe
        self._tools = tools
        self._identity = identity
        self._red_lines = red_lines
        self.limit = int(limit)

    # -- inputs -------------------------------------------------------------
    def probe(self) -> dict[str, Any]:
        return self._probe if self._probe is not None else run_probe()

    def tools(self) -> list[str]:
        return list(self._tools) if self._tools is not None else _tool_names()

    def identity(self) -> str:
        if self._identity is not None:
            return self._identity
        return identity_sentence()

    def red_lines(self) -> list[str]:
        if self._red_lines is not None:
            return list(self._red_lines)
        return red_line_titles()

    # -- capability truth ---------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        """Which families are usable now, and why each unusable one is not."""
        held = set(self.tools())
        checks = (self.probe() or {}).get("checks") or {}
        probe_error = (self.probe() or {}).get("error") or ""
        available: list[dict[str, Any]] = []
        unavailable: list[dict[str, Any]] = []
        for family in FAMILIES:
            present = [name for name in family.tools if name in held]
            entry = {
                "key": family.key,
                "label": family.label,
                "tools": present,
                "enable": family.enable,
            }
            if not present:
                continue
            gate = checks.get(family.gate) if family.gate else None
            if family.gate and probe_error:
                entry["reason"] = f"the probe failed ({probe_error}), so it cannot be counted as working"
                unavailable.append(entry)
            elif family.gate and (gate is None or not gate.get("ok")):
                detail = (gate or {}).get("detail") or "not detected"
                entry["reason"] = f"{family.gate}: {detail}"
                unavailable.append(entry)
            else:
                available.append(entry)
        return {"available": available, "unavailable": unavailable}

    # -- rendering ----------------------------------------------------------
    def section(self) -> str:
        capabilities = self.capabilities()
        available = capabilities["available"]
        unavailable = capabilities["unavailable"]
        total = len(self.tools())

        seen: set[str] = set()
        blocks: list[str] = [self.identity().strip()]

        if available:
            named: list[str] = []
            for entry in available:
                for name in entry["tools"]:
                    if name in seen:
                        continue
                    seen.add(name)
                    named.append(name)
            count = f"{total} tools registered" if total else "tool registry unreadable"
            blocks.append(f"Can do now ({count}): " + ", ".join(named) + ".")
        else:
            blocks.append("Can do now: no tool family is proven working on this machine right now.")

        if unavailable:
            lines = ["Cannot do right now — offer the fix instead of refusing:"]
            for entry in unavailable:
                tools = "/".join(entry["tools"]) or entry["key"]
                enable = entry["enable"] or "no single command enables it"
                lines.append(f"- {entry['label']} ({tools}): {enable}. Probe said {entry['reason']}")
            blocks.append("\n".join(lines))

        blocks.append(TRY_RULE)

        restraints = self.red_lines()
        if restraints:
            blocks.append("Never break: " + " · ".join(restraints) + ".")

        return _fit(blocks, self.limit)


def _fit(blocks: list[str], limit: int) -> str:
    """Join blocks with a blank line, dropping whole blocks until it fits.

    The identity block and the try-everything rule survive to the end, because a
    self-description without either is the prose claim this module exists to
    replace. Only the last-resort slice can cut mid-sentence, and it says so.
    """
    optional = [blocks[0]]
    rest = blocks[1:]
    text = "\n\n".join(optional + rest)
    while len(text) > limit and rest:
        # Drop the lowest-value block first: restraints are last, then the
        # unavailable list, then any trailing available-tool detail.
        index = next((i for i, block in enumerate(rest) if block.startswith("Never break:")), None)
        if index is None:
            index = next((i for i, block in enumerate(rest) if block.startswith("Cannot do right now")), None)
        if index is None:
            index = len(rest) - 1
        rest.pop(index)
        text = "\n\n".join(optional + rest)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def identity_sentence() -> str:
    """Who it is, from the record the person actually filled in."""
    name = "Hermus"
    profile = ""
    try:
        from .onboarding import PROFILES, load

        state = load()
        name = str(state.get("name") or name).strip() or name
        chosen = PROFILES.get(str(state.get("permissions") or "")) or {}
        sentence = str(chosen.get("sentence") or "").strip().rstrip(".")
        if sentence:
            # The onboarding sentence is written about Hermus in the third
            # person ("It asks before…"); read it as a sentence about itself.
            profile = ("it " + sentence[3:]) if sentence.startswith("It ") else sentence
            profile = profile[0].lower() + profile[1:] if not sentence.startswith("It ") else profile
    except Exception:  # noqa: BLE001 - identity must never break a turn
        profile = ""
    line = f"You are {name}, a free, local-first companion working on this PC with the person who set you up."
    if profile:
        line += f" Permissions they chose: {profile}."
    return line


def red_line_titles() -> list[str]:
    """One short line per red line, read from the policy file the tests pin."""
    path = Path(__file__).resolve().parents[1] / "policies" / "red_lines.json"
    try:
        rules = json.loads(path.read_text(encoding="utf-8")).get("rules") or []
    except Exception:  # noqa: BLE001 - a missing policy file degrades to no restraint line
        return []
    titles: list[str] = []
    for rule in rules:
        title = str(rule.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    return titles


# ---------------------------------------------------------------------------
# Self-diagnosis: what is broken now, and what is worth adding
# ---------------------------------------------------------------------------
_diagnosis_cache: dict[str, Any] = {}
_diagnosis_lock = threading.Lock()


def diagnose(*, force: bool = False) -> dict[str, Any]:
    """At most three broken-now and three worth-adding items, each with evidence.

    Broken-now reuses :class:`core.doctor.HermusDoctor`'s deterministic findings
    — its evidence strings and its fixes are already written there — plus any
    gate that failed in the last ``hermus improve`` run, because a failing test
    id is evidence of a different and more literal kind. Worth-adding reuses the
    capability ledger the product already keeps for Red Line 11.
    """
    now = time.time()
    with _diagnosis_lock:
        cached = _diagnosis_cache.get("value")
        if cached and not force and now - _diagnosis_cache.get("at", 0.0) < DIAGNOSIS_TTL_SECONDS:
            return cached

    broken: list[dict[str, Any]] = []
    worth: list[dict[str, Any]] = []

    for item in _doctor_items(MAX_ITEMS):
        broken.append(item)
    for item in _last_edit_gate_items(MAX_ITEMS - len(broken)):
        broken.append(item)
    for item in _unavailable_items(MAX_ITEMS):
        worth.append(item)
    for item in _ledger_items(MAX_ITEMS - len(worth)):
        worth.append(item)

    result = {
        "at": _now(),
        "broken": broken[:MAX_ITEMS],
        "worth": worth[:MAX_ITEMS],
        "view": attention_view(broken[:MAX_ITEMS], worth[:MAX_ITEMS]),
    }
    with _diagnosis_lock:
        _diagnosis_cache["value"] = result
        _diagnosis_cache["at"] = now
    return result


def _doctor_items(limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    try:
        from .doctor import doctor

        findings = doctor.analyze()
    except Exception:  # noqa: BLE001 - a doctor that cannot look is not a clean bill of health
        return [
            {
                "kind": "broken",
                "item": "the doctor could not examine this machine",
                "evidence": "HermusDoctor.analyze raised",
                "fix": "hermus doctor",
            }
        ]
    return [
        {
            "kind": "broken",
            "item": str(finding.title),
            "evidence": f"doctor {finding.category}/{finding.severity}: {finding.evidence}"[:220],
            "fix": (finding.fixes[0] if finding.fixes else "hermus doctor"),
        }
        for finding in findings[:limit]
    ]


def _last_edit_gate_items(limit: int) -> list[dict[str, Any]]:
    """Failing gates from the newest self-edit run, named by test id."""
    if limit <= 0:
        return []
    try:
        from .self_edit import last_run

        run = last_run() or {}
    except Exception:  # noqa: BLE001
        return []
    if not run or run.get("status") != "gates_failed":
        return []
    out = []
    for gate in (run.get("gates") or [])[:limit]:
        if gate.get("ok"):
            continue
        out.append(
            {
                "kind": "broken",
                "item": f"self-edit gate {gate.get('command')} failed",
                "evidence": f"run {run.get('id')}: {str(gate.get('tail') or '')[-160:]}"[:220],
                "fix": str(gate.get("command") or ""),
            }
        )
    return out


def _unavailable_items(limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    model = SelfModel()
    out = []
    for entry in model.capabilities()["unavailable"][:limit]:
        out.append(
            {
                "kind": "worth_adding",
                "item": entry["label"],
                "evidence": f"probe {entry['reason']}"[:220],
                "fix": entry["enable"] or "no single command",
            }
        )
    return out


def _ledger_items(limit: int) -> list[dict[str, Any]]:
    """Powers the ledger already records as wanted but not granted."""
    if limit <= 0:
        return []
    try:
        from .capability_ledger import get_capability_ledger

        rows = get_capability_ledger().list_discovered()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for row in rows:
        power = str(row.get("power") or "").strip()
        if not power:
            continue
        out.append(
            {
                "kind": "worth_adding",
                "item": power,
                "evidence": f"capability ledger, status {row.get('status') or 'unknown'}: {row.get('use') or 'no use recorded'}"[
                    :220
                ],
                "fix": str(row.get("needed_approval_setup") or "needs the person's approval")[:220],
            }
        )
        if len(out) >= limit:
            break
    return out


def attention_view(broken: list[dict[str, Any]], worth: list[dict[str, Any]]) -> dict[str, Any]:
    """The shape the Systems row renders: two counts and one list to open."""
    rows = [
        {
            "what": f"{'broken' if item['kind'] == 'broken' else 'to add'}: {item['item']}",
            "evidence": item["evidence"],
            "next": item["fix"],
        }
        for item in list(broken) + list(worth)
    ]
    return {
        "broken_now": len(broken),
        "worth_adding": len(worth),
        "checked_at": _now(),
        "items": rows,
    }


def attention_summary() -> dict[str, Any]:
    """What the attention digest offers: counts plus the one-line nouns."""
    diagnosis = diagnose()
    broken = diagnosis["broken"]
    worth = diagnosis["worth"]
    return {
        "broken": len(broken),
        "worth": len(worth),
        "headline": "; ".join(item["item"] for item in broken[:2]),
        "additions": "; ".join(item["item"] for item in worth[:2]),
    }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_model: SelfModel | None = None
_model_lock = threading.Lock()


def get_self_model() -> SelfModel:
    global _model
    with _model_lock:
        if _model is None:
            _model = SelfModel()
        return _model


def section() -> str:
    """The section the chat prompt injects; '' when switched off."""
    if not enabled():
        return ""
    try:
        return get_self_model().section()
    except Exception:  # noqa: BLE001 - a turn without a self-model is better than no turn
        return ""


__all__ = [
    "ENV_FLAG",
    "FAMILIES",
    "MAX_SECTION_CHARS",
    "Family",
    "SelfModel",
    "attention_summary",
    "attention_view",
    "diagnose",
    "enabled",
    "get_self_model",
    "identity_sentence",
    "red_line_titles",
    "run_probe",
    "section",
]
