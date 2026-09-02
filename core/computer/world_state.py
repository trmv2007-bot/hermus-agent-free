"""Shared, persistent model of the desktop currently visible to Hermus.

Vision, planning, execution, verification, repair, persistence, and resume all
read and update this one model instead of independently guessing what is on the
screen.  The model accepts structured vision output when available and keeps a
small heuristic compatibility path for existing string-only verifiers.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from collections.abc import Iterable


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _later(*candidates: Optional[str]) -> str:
    """Newest of the given ISO timestamps (falls back to the last non-empty one).

    ``update`` accepts a caller-supplied timestamp, so without this a replayed or
    clock-skewed observation could move the snapshot's clock backwards and make
    the state look older than the observations it already contains.
    """
    best: Optional[str] = None
    best_dt: Optional[datetime] = None
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        if best is None:
            best, best_dt = text, _parse_ts(text)
            continue
        parsed = _parse_ts(text)
        if parsed is None or best_dt is None:
            best = text if text > best else best
            best_dt = parsed or best_dt
            continue
        if parsed >= best_dt:
            best, best_dt = text, parsed
    return best or _now()


def _parse_ts(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone() if parsed.tzinfo is None else parsed


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


# Tokens that carry no world-state meaning. Filtering them (instead of filtering
# by length) keeps short-but-real labels such as "OK" or "No" matchable while
# still ignoring prose like "click the OK button".
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is", "are",
    "it", "be", "for", "with", "that", "this", "then", "from", "into", "onto",
    "click", "press", "tap", "type", "enter", "select", "choose", "scroll",
    "drag", "wait", "ensure", "verify", "check", "make", "sure", "open",
})


def _unique(values: Iterable[Any], limit: int = 50) -> list[str]:
    output: list[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output[-limit:]


@dataclass
class WorldObservation:
    source: str
    detail: str = ""
    confidence: float = 0.0
    timestamp: str = field(default_factory=_now)
    application: Optional[str] = None
    window: Optional[str] = None
    visible_targets: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    task_state: Optional[str] = None
    verification_ok: Optional[bool] = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorldState:
    """Canonical desktop state shared by all computer-agent components."""

    active_application: Optional[str] = None
    active_window: Optional[str] = None
    visible_targets: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    task: Optional[str] = None
    task_state: str = "UNKNOWN"
    confidence: float = 0.0
    timestamp: str = field(default_factory=_now)
    revision: int = 0
    last_action: Optional[dict[str, Any]] = None
    last_verification: Optional[dict[str, Any]] = None
    completed_states: list[str] = field(default_factory=list)
    failed_states: list[str] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    max_observations: int = 100

    # ``_lock`` is deliberately NOT a dataclass field: keeping it out of the field
    # list means ``dataclasses.asdict`` / ``fields`` never try to pickle an RLock.
    # ``to_dict`` remains the canonical serializer; this just removes the trap.
    def __post_init__(self) -> None:
        object.__setattr__(self, "_lock", threading.RLock())
        self.max_observations = _positive_int(self.max_observations, 100)

    @property
    def _rl(self) -> threading.RLock:
        lock = self.__dict__.get("_lock")
        if lock is None:  # e.g. an instance restored by unpickling
            lock = threading.RLock()
            object.__setattr__(self, "_lock", lock)
        return lock

    def _touch(self) -> None:
        """Bump revision + timestamp. Callers must already hold ``_lock``."""
        self.revision += 1
        self.timestamp = _now()

    # Compatibility aliases for the initial WorldState API.
    #
    # These setters are real write paths (``state_machine`` drives diagnose and
    # transition phases through ``current_state``), so they must go through the
    # same change accounting as ``update``: normalize the value, and bump
    # ``revision``/``timestamp`` only when something actually changed. Without
    # that, a checkpoint could move between states with no revision bump and any
    # consumer using ``revision`` as a change counter would miss the transition.
    @property
    def application(self) -> Optional[str]:
        return self.active_application

    @application.setter
    def application(self, value: Optional[str]) -> None:
        self._set("active_application", str(value).strip() if value else None)

    @property
    def window(self) -> Optional[str]:
        return self.active_window

    @window.setter
    def window(self, value: Optional[str]) -> None:
        self._set("active_window", str(value).strip() if value else None)

    @property
    def elements(self) -> list[str]:
        return self.visible_targets

    @elements.setter
    def elements(self, value: list[str]) -> None:
        # Dedupe like ``update`` does, so the two write paths cannot disagree.
        self._set("visible_targets", _unique(value or []))

    @property
    def modal(self) -> Optional[str]:
        return self.dialogs[-1] if self.dialogs else None

    @modal.setter
    def modal(self, value: Optional[str]) -> None:
        # Push onto the dialog stack instead of replacing it: the previous dialogs
        # are still part of what is on screen and dropping them loses evidence.
        text = str(value).strip() if value else ""
        with self._rl:
            if not text:
                dialogs: list[str] = []
            elif self.dialogs and self.dialogs[-1].casefold() == text.casefold():
                dialogs = list(self.dialogs)
            else:
                dialogs = _unique([*self.dialogs, text], limit=10)
            self._set("dialogs", dialogs, _locked=True)

    @property
    def current_state(self) -> str:
        return self.task_state

    @current_state.setter
    def current_state(self, value: Optional[str]) -> None:
        self._set("task_state", str(value).strip() if value else "UNKNOWN")

    def _set(self, name: str, value: Any, *, _locked: bool = False) -> None:
        """Assign a field and bump the revision counter iff the value changed."""
        def apply() -> None:
            if getattr(self, name) == value:
                return
            setattr(self, name, value)
            self._touch()

        if _locked:
            apply()
        else:
            with self._rl:
                apply()

    @staticmethod
    def _confidence(value: Any, default: float = 0.0) -> float:
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return _unique(value)
        return [str(value)] if str(value).strip() else []

    @staticmethod
    def _heuristics(detail: str) -> dict[str, Any]:
        """Extract conservative structure from legacy prose observations."""
        text = str(detail or "").strip()
        lowered = text.lower()
        result: dict[str, Any] = {"visible_targets": [], "dialogs": []}

        app_match = re.search(
            r"\b(?:in|inside|shows?|visible in)\s+(?:the\s+)?([A-Z][A-Za-z0-9 ._-]{1,40})\s+(?:app|application|browser)\b",
            text,
        )
        if app_match:
            result["application"] = app_match.group(1).strip()
        window_match = re.search(r"\b([A-Z][A-Za-z0-9 ._:/-]{1,60})\s+window\b", text)
        if window_match:
            result["window"] = window_match.group(1).strip()

        # Quoted labels and common control phrases are useful target names but
        # arbitrary prose is not added to visible_targets.
        quoted = re.findall(r"[\"']([^\"']{1,80})[\"']", text)
        controls = re.findall(
            r"\b([A-Za-z0-9][A-Za-z0-9 _-]{0,50}\s+(?:button|link|field|tab|menu|address bar|search box))\b",
            text,
            flags=re.I,
        )
        result["visible_targets"] = _unique([*quoted, *controls], limit=30)

        if re.search(r"\b(?:popup|pop-up|modal|dialog|permission prompt|alert)\b", lowered):
            dialog_match = re.search(
                r"([^.!?]{0,100}\b(?:popup|pop-up|modal|dialog|permission prompt|alert)\b[^.!?]{0,100})",
                text,
                flags=re.I,
            )
            result["dialogs"] = [(dialog_match.group(1) if dialog_match else text)[:200]]
        if re.search(r"(?:dialog|popup|modal).*(?:closed|dismissed|gone|no longer visible)", lowered):
            result["dialogs"] = []
            result["clear_dialogs"] = True
        return result

    def reset(self, task: Optional[str] = None) -> None:
        with self._rl:
            self.active_application = None
            self.active_window = None
            self.visible_targets = []
            self.dialogs = []
            self.task = task
            self.task_state = "PLANNING" if task else "UNKNOWN"
            self.confidence = 0.0
            self.timestamp = _now()
            self.revision += 1
            self.last_action = None
            self.last_verification = None
            self.completed_states = []
            self.failed_states = []
            self.observations = []

    def update(self, observation: dict[str, Any], source: str = "vision") -> dict[str, Any]:
        """Merge one structured or prose observation and return the new snapshot."""
        if not isinstance(observation, dict):
            observation = {"detail": str(observation)}
        detail = str(
            observation.get("detail")
            or observation.get("description")
            or observation.get("visual_result")
            or ""
        ).strip()
        heuristic = self._heuristics(detail)

        application = (
            observation.get("active_application")
            or observation.get("application")
            or observation.get("app")
            or heuristic.get("application")
        )
        window = (
            observation.get("active_window")
            or observation.get("window")
            or observation.get("title")
            or heuristic.get("window")
        )
        targets = self._as_list(
            observation.get("visible_targets", observation.get("targets", observation.get("elements")))
        ) or heuristic.get("visible_targets", [])
        dialogs_supplied = any(key in observation for key in ("dialogs", "dialog", "modal"))
        dialogs = self._as_list(
            observation.get("dialogs", observation.get("dialog", observation.get("modal")))
        )
        if not dialogs_supplied:
            dialogs = heuristic.get("dialogs", [])
        clear_dialogs = bool(observation.get("clear_dialogs") or heuristic.get("clear_dialogs"))
        raw_confidence = observation.get("confidence")
        task_state = observation.get("task_state") or observation.get("current_state")
        verification_ok = observation.get("ok") if "ok" in observation else observation.get("matched")
        # Only verification-shaped observations may overwrite ``last_verification``.
        # A plain vision read is not a verification result, and recording it as one
        # made the field lie about what the last verification concluded.
        is_verification = (
            "ok" in observation
            or "matched" in observation
            or "verification_ok" in observation
            or "verification" in str(source).lower()
        )

        with self._rl:
            if application:
                self.active_application = str(application).strip()
            if window:
                self.active_window = str(window).strip()
            if observation.get("replace_targets"):
                self.visible_targets = _unique(targets)
            else:
                self.visible_targets = _unique([*self.visible_targets, *targets])
            if clear_dialogs or (dialogs_supplied and not dialogs):
                self.dialogs = []
            elif dialogs:
                self.dialogs = _unique(dialogs, limit=10)
            if task_state:
                self.task_state = str(task_state)
            # Read the fallback under the lock so a concurrent writer cannot be
            # interleaved between the read and the write.
            self.confidence = self._confidence(raw_confidence, self.confidence)
            self.timestamp = _later(
                observation.get("timestamp"), observation.get("ts"), self.timestamp, _now()
            )
            self.revision += 1
            if is_verification:
                self.last_verification = dict(observation)
            record = WorldObservation(
                source=source,
                detail=detail,
                confidence=self.confidence,
                timestamp=self.timestamp,
                application=self.active_application,
                window=self.active_window,
                visible_targets=list(targets),
                dialogs=list(self.dialogs),
                task_state=self.task_state,
                verification_ok=bool(verification_ok) if verification_ok is not None else None,
                evidence=dict(observation.get("evidence") or {}),
            ).to_dict()
            self.observations.append(record)
            self.observations = self.observations[-_positive_int(self.max_observations, 100):]
            return self.to_dict()

    def begin_task(self, task: str, state: str = "PLANNING") -> None:
        with self._rl:
            self.task = task
            self.task_state = state
            self.timestamp = _now()
            self.revision += 1

    def before_action(self, state: str, action: dict[str, Any]) -> None:
        with self._rl:
            self.task_state = state
            self.last_action = {"state": state, "action": dict(action), "started": _now()}
            self.timestamp = _now()
            self.revision += 1

    def mark_state(self, state: str, success: bool, detail: str = "") -> None:
        with self._rl:
            target = self.completed_states if success else self.failed_states
            target.append(state)
            if success:
                self.failed_states = [item for item in self.failed_states if item != state]
            self.completed_states = _unique(self.completed_states, limit=1000)
            self.failed_states = _unique(self.failed_states, limit=1000)
            self.task_state = state if success else f"FAILED:{state}"
            self.timestamp = _now()
            self.revision += 1
            if detail:
                self.observations.append(WorldObservation(
                    source="state_machine",
                    detail=detail,
                    confidence=self.confidence,
                    timestamp=self.timestamp,
                    application=self.active_application,
                    window=self.active_window,
                    task_state=self.task_state,
                    verification_ok=success,
                ).to_dict())
                self.observations = self.observations[-_positive_int(self.max_observations, 100):]

    def finish_task(self, success: bool) -> None:
        with self._rl:
            self.task_state = "SUCCESS" if success else "FAILURE"
            self.timestamp = _now()
            self.revision += 1

    def satisfies(self, condition: str) -> dict[str, Any]:
        """Best-effort local precondition check before asking vision again.

        Tokens are filtered by *meaning* (a stopword list of function words and
        GUI action verbs) rather than by length. The old ``len(token) > 2`` filter
        threw away real short labels such as "OK" and "No", and a condition made
        up entirely of short words produced ``0/0`` and reported ``matched:
        False`` — a false negative against state that actually contained the
        target. A condition with no substantive tokens is now vacuously satisfied.
        """
        wanted = str(condition or "").strip().casefold()
        if not wanted:
            return {"matched": True, "confidence": 1.0, "detail": "empty condition"}
        with self._rl:
            haystack = " ".join(
                filter(None, [
                    self.active_application,
                    self.active_window,
                    *self.visible_targets,
                    *self.dialogs,
                    self.task_state,
                ])
            ).casefold()
            confidence = self.confidence
        tokens = [
            token for token in re.findall(r"[a-z0-9]+", wanted)
            if token not in _STOPWORDS
        ]
        if not tokens:
            return {
                "matched": True,
                "confidence": 1.0,
                "detail": "condition has no substantive tokens",
            }
        matched_tokens = [token for token in tokens if token in haystack]
        ratio = len(matched_tokens) / len(tokens)
        return {
            "matched": ratio >= 0.7,
            "confidence": round(min(confidence, ratio), 3),
            "detail": f"world-state token match {len(matched_tokens)}/{len(tokens)}",
        }

    def to_dict(self, include_history: bool = True) -> dict[str, Any]:
        with self._rl:
            data = {
                "active_application": self.active_application,
                "active_window": self.active_window,
                "visible_targets": list(self.visible_targets),
                "dialogs": list(self.dialogs),
                "task": self.task,
                "task_state": self.task_state,
                "confidence": round(self._confidence(self.confidence), 4),
                "timestamp": self.timestamp,
                "revision": self.revision,
                "last_action": self.last_action,
                "last_verification": self.last_verification,
                "completed_states": list(self.completed_states),
                "failed_states": list(self.failed_states),
                "max_observations": _positive_int(self.max_observations, 100),
                # Compatibility names for older checkpoints.
                "application": self.active_application,
                "window": self.active_window,
                "elements": list(self.visible_targets),
                "modal": self.modal,
                "current_state": self.task_state,
            }
            if include_history:
                data["observations"] = list(self.observations)
            return data

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "WorldState":
        data = data if isinstance(data, dict) else {}
        return cls(
            active_application=data.get("active_application", data.get("application")),
            active_window=data.get("active_window", data.get("window")),
            visible_targets=cls._as_list(data.get("visible_targets", data.get("elements"))),
            dialogs=cls._as_list(data.get("dialogs", data.get("modal"))),
            task=data.get("task"),
            task_state=str(data.get("task_state", data.get("current_state", "UNKNOWN"))),
            confidence=cls._confidence(data.get("confidence"), 0.0),
            timestamp=str(data.get("timestamp") or _now()),
            revision=_positive_int(data.get("revision"), 0),
            last_action=data.get("last_action"),
            last_verification=data.get("last_verification"),
            completed_states=cls._as_list(data.get("completed_states")),
            failed_states=cls._as_list(data.get("failed_states")),
            observations=list(data.get("observations") or []),
            # Restored so a save/load round trip keeps the caller's buffer size.
            max_observations=_positive_int(data.get("max_observations"), 100),
        )

    def save(self, path: str) -> str:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        temporary.replace(target)
        return str(target)

    @classmethod
    def load(cls, path: str, *, strict: bool = False) -> "WorldState":
        """Load a snapshot.

        ``strict=False`` (the default) keeps the original forgiving behaviour: a
        missing or unreadable file yields an empty state so a live run is never
        blocked by a bad checkpoint. ``strict=True`` raises instead, which is what
        migration needs — silently substituting an empty state and then writing it
        back over the input destroys the only copy of the data.
        """
        target = Path(path).expanduser().resolve()
        if not target.exists():
            if strict:
                raise FileNotFoundError(f"world-state {target} not found")
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            if strict:
                raise ValueError(f"world-state {target} is not valid JSON: {exc}") from exc
            return cls()
        if not isinstance(data, dict):
            if strict:
                raise ValueError(f"world-state {target} is not a JSON object")
            return cls()
        return cls.from_dict(data)
