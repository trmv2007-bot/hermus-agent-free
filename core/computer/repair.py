"""Diagnose desktop failures and produce bounded, verifiable repair plans.

The repair engine deliberately does *not* execute desktop actions.  It turns a
structured failure into a :class:`RepairPlan`; the visual state machine remains
the single place that executes and verifies actions.  Keeping that boundary
means repair actions pass through the same permission, recording, and emergency
stop gates as original task actions.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from ..llm import FreeLLM, free_llm


class FailureKind(str, Enum):
    """Stable categories used in traces, artifacts, and future skill learning."""

    BLOCKING_DIALOG = "blocking_dialog"
    TARGET_NOT_FOUND = "target_not_found"
    WRONG_WINDOW = "wrong_window"
    INPUT_FOCUS = "input_focus"
    MISCLICK = "misclick"
    TIMEOUT = "timeout"
    ACTION_REJECTED = "action_rejected"
    ACTION_ERROR = "action_error"
    NO_VISUAL_CHANGE = "no_visual_change"
    VISUAL_MISMATCH = "visual_mismatch"
    UNKNOWN = "unknown"


@dataclass
class FailureDiagnosis:
    kind: str
    summary: str
    evidence: str = ""
    confidence: float = 0.0
    retryable: bool = True
    suggested_strategy: str = ""
    source: str = "heuristic"
    plan: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepairStep:
    name: str
    action: Dict[str, Any]
    expected: str
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepairPlan:
    """A diagnosis plus the recovery actions required before retrying."""

    diagnosis: FailureDiagnosis
    steps: List[RepairStep] = field(default_factory=list)
    retry_original: bool = True
    source: str = "none"
    reason: str = ""
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    @property
    def available(self) -> bool:
        return bool(self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "diagnosis": self.diagnosis.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "retry_original": self.retry_original,
            "source": self.source,
            "reason": self.reason,
            "available": self.available,
        }


class RepairEngine:
    """Classify failures and generate short, safe, visually-verifiable repairs.

    Common failures are handled deterministically so a missing model never
    turns a popup into repeated blind clicks.  Ambiguous, retryable failures may
    be sent to the configured LLM.  Model output is constrained to supported
    GUI actions, capped at ``max_steps``, and stripped of invented coordinates.
    """

    SAFE_ACTIONS = {
        "click_target",
        "press_key",
        "hotkey",
        "type_text",
        "scroll",
        "focus_window",
        "open_application",
        "wait_until",
    }
    DISMISS_LABELS: Sequence[str] = (
        "not now",
        "no thanks",
        "close",
        "dismiss",
        "cancel",
        "skip",
        "later",
        "got it",
    )

    def __init__(
        self,
        llm: Optional[FreeLLM] = None,
        max_steps: int = 3,
        use_llm: bool = True,
    ):
        self.llm = llm or free_llm
        self.max_steps = max(1, min(int(max_steps), 5))
        self.use_llm = bool(use_llm)
        self.known_repairs: List[Dict[str, Any]] = []

    def set_known_repairs(self, repairs: Optional[List[Dict[str, Any]]]) -> None:
        """Attach evidence-backed repairs recalled with the active skill."""
        self.known_repairs = [item for item in (repairs or []) if isinstance(item, dict)][-50:]

    @staticmethod
    def _context_parts(last_action: Optional[Dict[str, Any]]) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        context = last_action if isinstance(last_action, dict) else {}
        spec = context.get("spec") if isinstance(context.get("spec"), dict) else {}
        result = context.get("result") if isinstance(context.get("result"), dict) else context
        verification = context.get("verification") if isinstance(context.get("verification"), dict) else {}
        return spec, result, verification

    def diagnose(
        self,
        failure_detail: str,
        expected: str,
        last_action: Optional[Dict[str, Any]] = None,
    ) -> FailureDiagnosis:
        """Return a deterministic first-pass diagnosis from all failure signals."""
        spec, result, verification = self._context_parts(last_action)
        evidence_parts = [
            str(failure_detail or ""),
            str(result.get("error") or result.get("detail") or ""),
            str(verification.get("error") or verification.get("detail") or ""),
        ]
        evidence = " | ".join(dict.fromkeys(part.strip() for part in evidence_parts if part and part.strip()))
        text = " ".join((evidence, str(expected or ""))).lower()

        # Safety/policy failures cannot be fixed by inventing more UI actions.
        if re.search(r"emergency stop|permission (?:policy )?denied|policy denied|not allowed|approval required|no controller|unknown action kind", text):
            return FailureDiagnosis(
                kind=FailureKind.ACTION_REJECTED.value,
                summary="The action was rejected by a safety, permission, or configuration gate.",
                evidence=evidence,
                confidence=0.98,
                retryable=False,
                suggested_strategy="Stop and preserve the exact rejection reason for the user.",
            )

        # Dialog detection intentionally precedes target-not-found: a dialog is
        # often the reason the requested target cannot be seen.
        if re.search(r"cookie|consent|banner", text):
            step = {"name": "DISMISS_COOKIE_BANNER", "action": {"kind": "click_target", "target": "Accept all"}, "expected": "Cookie banner is dismissed"}
            return FailureDiagnosis(
                kind=FailureKind.BLOCKING_DIALOG.value,
                summary="A cookie consent banner is blocking the page.",
                evidence=evidence,
                confidence=0.92,
                retryable=True,
                suggested_strategy="Accept or dismiss the cookie banner, then retry the original action.",
                source="heuristic",
                plan=[step],
            )

        if re.search(r"popup|pop-up|modal|dialog|overlay|prompt is blocking|obscur(?:e|ed|ing)|unexpected window", text):
            return FailureDiagnosis(
                kind=FailureKind.BLOCKING_DIALOG.value,
                summary="An unexpected dialog or overlay is blocking the original action.",
                evidence=evidence,
                confidence=0.9,
                retryable=True,
                suggested_strategy="Dismiss the obstruction, verify it disappeared, then retry the original action.",
            )

        if re.search(r"wrong (?:application|app|window)|another (?:application|app|window)|inactive window|window is not active", text):
            return FailureDiagnosis(
                kind=FailureKind.WRONG_WINDOW.value,
                summary="The original action ran against the wrong or inactive window.",
                evidence=evidence,
                confidence=0.88,
                retryable=True,
                suggested_strategy="Focus the intended application and verify it is active.",
            )

        if re.search(r"timed? out|timeout|still loading|loading (?:did not|has not)|not ready", text):
            return FailureDiagnosis(
                kind=FailureKind.TIMEOUT.value,
                summary="The expected UI state was not ready before the timeout.",
                evidence=evidence,
                confidence=0.86,
                retryable=True,
                suggested_strategy="Wait for a concrete visual condition, then retry if it appears.",
            )

        if re.search(r"target not (?:found|visible)|could not (?:find|locate)|cannot (?:find|locate)|no matching target|element not (?:found|visible)", text):
            return FailureDiagnosis(
                kind=FailureKind.TARGET_NOT_FOUND.value,
                summary="The requested visual target could not be located.",
                evidence=evidence,
                confidence=0.9,
                retryable=True,
                suggested_strategy="Retry only within the state retry budget unless another visible obstruction is diagnosed.",
            )

        if re.search(r"not focus(?:ed)?|focus (?:was|is) elsewhere|typed into|wrong field|caret", text):
            return FailureDiagnosis(
                kind=FailureKind.INPUT_FOCUS.value,
                summary="Keyboard input focus was not on the intended control.",
                evidence=evidence,
                confidence=0.82,
                retryable=True,
                suggested_strategy="Restore focus to the intended control and verify it before retrying.",
            )

        if re.search(r"mis-?click|clicked (?:the )?wrong|click (?:missed|landed outside)|pointer moved", text):
            return FailureDiagnosis(
                kind=FailureKind.MISCLICK.value,
                summary="The pointer action missed or selected the wrong visual target.",
                evidence=evidence,
                confidence=0.84,
                retryable=True,
                suggested_strategy="Re-detect the original target and use one bounded original-action retry.",
            )

        if re.search(r"no (?:screen|visual|visible) change|screen change only", text) or verification.get("changed") is False:
            return FailureDiagnosis(
                kind=FailureKind.NO_VISUAL_CHANGE.value,
                summary="The action completed but produced no verified visual transition.",
                evidence=evidence,
                confidence=0.78,
                retryable=True,
                suggested_strategy="Inspect for focus or obstruction problems before retrying.",
            )

        if result and not result.get("ok", False):
            return FailureDiagnosis(
                kind=FailureKind.ACTION_ERROR.value,
                summary="The desktop action backend reported an execution error.",
                evidence=evidence,
                confidence=0.84,
                retryable=True,
                suggested_strategy="Repair the environmental cause if one is visible; otherwise use the bounded retry policy.",
            )

        if expected:
            return FailureDiagnosis(
                kind=FailureKind.VISUAL_MISMATCH.value,
                summary="The screen did not match the expected post-action state.",
                evidence=evidence,
                confidence=0.72,
                retryable=True,
                suggested_strategy="Remove the observed blocker and restore the expected visual state.",
            )

        return FailureDiagnosis(
            kind=FailureKind.UNKNOWN.value,
            summary="The action failed without enough evidence for a specific diagnosis.",
            evidence=evidence,
            confidence=0.35,
            retryable=True,
            suggested_strategy="Use only the bounded retry policy; do not invent a destructive repair.",
        )

    def _dialog_plan(self, diagnosis: FailureDiagnosis, expected: str) -> RepairPlan:
        lowered = diagnosis.evidence.lower()
        if "cookie" in lowered or "consent" in lowered or "banner" in lowered:
            action = {"kind": "click_target", "target": "Accept all"}
            name = "DISMISS_COOKIE_BANNER"
        else:
            label = next((candidate for candidate in self.DISMISS_LABELS if candidate in lowered), None)
            if label:
                action = {"kind": "click_target", "target": label.title()}
                name = f"DISMISS_{re.sub(r'[^A-Z0-9]+', '_', label.upper()).strip('_')}"
            else:
                action = {"kind": "click_target", "target": "Close"}
                name = "DISMISS_BLOCKING_DIALOG"
        condition = "The unexpected blocking dialog or popup is no longer visible"
        if expected:
            condition += f", and the screen is ready for: {expected}"
        return RepairPlan(
            diagnosis=diagnosis,
            steps=[RepairStep(
                name=name,
                action=action,
                expected=condition,
                rationale="Remove the diagnosed obstruction before replaying the original action.",
            )],
            retry_original=True,
            source="heuristic",
            reason="A blocking dialog has a safe bounded dismissal repair.",
        )

    @staticmethod
    def _intended_window(spec: Dict[str, Any], expected: str) -> str:
        if spec.get("kind") in {"open_application", "focus_window"} and spec.get("name"):
            return str(spec["name"]).strip()
        # Use only an explicit "X window/application" phrase; do not guess from
        # arbitrary expected-state prose.
        match = re.search(r"\b([A-Za-z][A-Za-z0-9 ._-]{1,40})\s+(?:window|application|app)\b", expected or "", re.I)
        return match.group(1).strip() if match else ""

    def _known_repair_plan(
        self,
        diagnosis: FailureDiagnosis,
        expected: str,
    ) -> Optional[RepairPlan]:
        """Reuse the best previously-verified repair for a recalled skill."""
        failure_words = set(re.findall(r"[a-z0-9]+", f"{diagnosis.kind} {diagnosis.summary} {diagnosis.evidence}".lower()))
        candidates = []
        for repair in self.known_repairs:
            if repair.get("success") is False or repair.get("outcome") == "failure":
                continue
            repair_failure = str(repair.get("failure") or repair.get("failure_reason") or repair.get("repair_for") or "")
            score = len(failure_words & set(re.findall(r"[a-z0-9]+", repair_failure.lower())))
            raw_action = repair.get("action_spec") or repair.get("action")
            if isinstance(raw_action, dict) and isinstance(raw_action.get("action_spec"), dict):
                raw_action = raw_action["action_spec"]
            if isinstance(raw_action, dict):
                kind = raw_action.get("kind") or raw_action.get("action")
                action = {"kind": kind} if kind else {}
                for key in ("target", "key", "keys", "text", "amount", "name", "condition", "timeout"):
                    if key in raw_action:
                        action[key] = raw_action[key]
                sanitized = self._sanitize_steps([{
                    "name": str(repair.get("state") or "KNOWN_REPAIR"),
                    "action": action,
                    "expected": str(
                        (repair.get("verification") or {}).get("expected_state")
                        or expected
                        or "The known obstruction is no longer visible"
                    ),
                    "rationale": "Previously verified repair recalled from skill evidence.",
                }])
                if sanitized:
                    candidates.append((score, sanitized[0]))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if not candidates or candidates[0][0] <= 0:
            return None
        return RepairPlan(
            diagnosis=diagnosis,
            steps=[candidates[0][1]],
            retry_original=True,
            source="skill_memory",
            reason="Reusing a previously verified repair for this failure pattern.",
        )

    def _heuristic_plan(
        self,
        diagnosis: FailureDiagnosis,
        expected: str,
        last_action: Optional[Dict[str, Any]],
    ) -> Optional[RepairPlan]:
        spec, _, _ = self._context_parts(last_action)
        if diagnosis.kind == FailureKind.BLOCKING_DIALOG.value:
            return self._dialog_plan(diagnosis, expected)
        if diagnosis.kind == FailureKind.WRONG_WINDOW.value:
            window = self._intended_window(spec, expected)
            if window:
                return RepairPlan(
                    diagnosis=diagnosis,
                    steps=[RepairStep(
                        name="FOCUS_INTENDED_WINDOW",
                        action={"kind": "focus_window", "name": window},
                        expected=f"The {window} window is active and visible",
                        rationale="Restore the original action's intended window context.",
                    )],
                    retry_original=True,
                    source="heuristic",
                    reason="The intended window can be recovered without guessing coordinates.",
                )
        return None

    @staticmethod
    def _json_value(content: str) -> Any:
        text = (content or "").strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()
        decoder = json.JSONDecoder()
        starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        for start in sorted(starts):
            try:
                value, _ = decoder.raw_decode(text[start:])
                return value
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def _sanitize_steps(self, raw_steps: Any) -> List[RepairStep]:
        if not isinstance(raw_steps, list):
            return []
        steps: List[RepairStep] = []
        for index, raw in enumerate(raw_steps[: self.max_steps]):
            if not isinstance(raw, dict):
                continue
            action = raw.get("action")
            if not isinstance(action, dict):
                continue
            kind = str(action.get("kind") or "").strip().lower()
            if kind not in self.SAFE_ACTIONS:
                continue
            cleaned: Dict[str, Any] = {"kind": kind}
            if kind == "click_target":
                target = str(action.get("target") or "").strip()
                if not target:
                    continue
                cleaned["target"] = target[:160]
            elif kind == "press_key":
                key = str(action.get("key") or "").strip()
                if not key:
                    continue
                cleaned["key"] = key[:40]
            elif kind == "hotkey":
                keys = action.get("keys")
                if not isinstance(keys, list) or not keys:
                    continue
                cleaned["keys"] = [str(key)[:30] for key in keys[:4]]
            elif kind == "type_text":
                text = str(action.get("text") or "")
                # Repairs are UI corrections, not a route for generating shell
                # scripts or unbounded text payloads.
                if not text or len(text) > 500 or re.search(r"\brm\s+-rf\b|\bmkfs\b|\bshutdown\b", text, re.I):
                    continue
                cleaned["text"] = text
            elif kind == "scroll":
                try:
                    cleaned["amount"] = max(-20.0, min(float(action.get("amount", 0)), 20.0))
                except (TypeError, ValueError):
                    continue
            elif kind in {"focus_window", "open_application"}:
                name = str(action.get("name") or "").strip()
                if not name:
                    continue
                cleaned["name"] = name[:100]
            elif kind == "wait_until":
                condition = str(action.get("condition") or raw.get("expected") or "").strip()
                if not condition:
                    continue
                cleaned["condition"] = condition[:300]
                try:
                    cleaned["timeout"] = max(1.0, min(float(action.get("timeout", 10.0)), 60.0))
                except (TypeError, ValueError):
                    cleaned["timeout"] = 10.0

            expected = str(raw.get("expected") or "").strip()
            if not expected:
                expected = "The obstruction is removed and the original action can be retried safely"
            steps.append(RepairStep(
                name=str(raw.get("name") or f"REPAIR_STEP_{index + 1}")[:80],
                action=cleaned,
                expected=expected[:500],
                rationale=str(raw.get("rationale") or "")[:500],
            ))
        return steps

    def _llm_plan(
        self,
        diagnosis: FailureDiagnosis,
        expected: str,
        last_action: Optional[Dict[str, Any]],
    ) -> RepairPlan:
        spec, result, verification = self._context_parts(last_action)
        prompt = f"""You are the Hermus desktop Repair Planner.

The original action failed. Create the smallest safe recovery plan that restores the UI so the ORIGINAL action can be retried. Do not repeat the original action in the repair. Do not invent screen coordinates. Never bypass a permission or emergency-stop denial.

DIAGNOSIS: {json.dumps(diagnosis.to_dict(), default=str)}
ORIGINAL ACTION SPEC: {json.dumps(spec, default=str)}
ACTION RESULT: {json.dumps(result, default=str)}
VERIFICATION: {json.dumps(verification, default=str)}
EXPECTED POST-ACTION STATE: {expected}

Return only JSON with this shape:
{{
  "retry_original": true,
  "steps": [
    {{
      "name": "SHORT_NAME",
      "rationale": "why this repairs the diagnosed cause",
      "action": {{"kind": "click_target", "target": "visible label"}},
      "expected": "a concrete visual condition proving this repair worked"
    }}
  ]
}}

Allowed action kinds: click_target, press_key, hotkey, type_text, scroll, focus_window, open_application, wait_until.
Generate at most {self.max_steps} steps. An empty steps list is better than an unsafe guess.
"""
        try:
            response = self.llm.chat([{"role": "user", "content": prompt}])
            parsed = self._json_value(getattr(response, "content", str(response)))
        except Exception as exc:  # noqa: BLE001
            return RepairPlan(
                diagnosis=diagnosis,
                steps=[],
                retry_original=diagnosis.retryable,
                source="llm_error",
                reason=f"Repair planner failed: {exc}",
            )

        payload = parsed if isinstance(parsed, dict) else {"steps": parsed}
        steps = self._sanitize_steps(payload.get("steps") if isinstance(payload, dict) else None)
        retry_original = bool(payload.get("retry_original", True)) if isinstance(payload, dict) else True
        return RepairPlan(
            diagnosis=diagnosis,
            steps=steps,
            retry_original=retry_original and diagnosis.retryable,
            source="llm" if steps else "none",
            reason=("The repair planner returned a validated recovery sequence."
                    if steps else "The repair planner found no safe, valid recovery sequence."),
        )

    def create_plan(
        self,
        failure_detail: str,
        expected: str,
        last_action: Optional[Dict[str, Any]] = None,
    ) -> RepairPlan:
        """Diagnose a failure and return a bounded, validated repair plan."""
        diagnosis = self.diagnose(failure_detail, expected, last_action)
        if not diagnosis.retryable:
            return RepairPlan(
                diagnosis=diagnosis,
                steps=[],
                retry_original=False,
                source="none",
                reason="The diagnosed failure is explicitly non-retryable.",
            )

        known = self._known_repair_plan(diagnosis, expected)
        if known is not None:
            return known

        heuristic = self._heuristic_plan(diagnosis, expected, last_action)
        if heuristic is not None:
            return heuristic

        # These cases have no safe environmental fix without additional visual
        # evidence.  Let the state machine apply its bounded retry policy rather
        # than asking a text model to guess.
        if diagnosis.kind in {
            FailureKind.TARGET_NOT_FOUND.value,
            FailureKind.MISCLICK.value,
            FailureKind.TIMEOUT.value,
            FailureKind.UNKNOWN.value,
        }:
            return RepairPlan(
                diagnosis=diagnosis,
                steps=[],
                retry_original=True,
                source="none",
                reason="No safe repair was inferred; a bounded original-action retry is allowed.",
            )

        if self.use_llm:
            return self._llm_plan(diagnosis, expected, last_action)

        return RepairPlan(
            diagnosis=diagnosis,
            steps=[],
            retry_original=True,
            source="none",
            reason="LLM repair planning is disabled and no deterministic repair matched.",
        )

    def repair(
        self,
        failure_detail: str,
        expected: str,
        last_action: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Backward-compatible list API.

        New integrations should use :meth:`create_plan` so diagnosis and retry
        policy are retained in the state-machine trace.
        """
        return [step.to_dict() for step in self.create_plan(failure_detail, expected, last_action).steps]
