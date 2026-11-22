"""Quiet judgement: whether this is a moment to say anything at all.

``core/proactive.py`` decides *what* is worth saying; this module decides
*whether to say it now*. Every proactive surface is meant to pass through
:class:`AttentionGate`, which suppresses while the person is typing (input within
``typing_seconds``), in a call (a meeting window has focus), fullscreen, outside
quiet hours, or inside a hard rate limit (two per hour, never twice within twenty
minutes). Anything withheld is not dropped: it waits in one digest that the person
pulls when they are already looking at the product — never a modal, never a focus
steal, never a banner over work in progress.

Two suggestion sources ship here, both evidence-based and both cheap to ignore:

* ``power.low_battery`` — on battery under the configured threshold;
* ``focus.long_session`` — continuous activity past the configured sitting length.

A "not now" mutes that pattern for seven days, and the choice is stored in the
awareness settings file.
"""

from __future__ import annotations

import time
import uuid
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

MUTE_DAYS = 7
RATE_WINDOW_SECONDS = 3600.0
IDLE_BREAK_SECONDS = 300.0
SUGGESTION_TTL_SECONDS = 24 * 3600.0
DIGEST_PREFIX = "digest_"


@dataclass
class Suggestion:
    """One thing Hermus noticed and could offer."""

    id: str
    pattern: str
    title: str
    body: str
    task: str
    created_at: float
    batched: bool = False
    fired_at: float | None = None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pattern": self.pattern,
            "title": self.title,
            "body": self.body,
            "created_at": datetime.fromtimestamp(self.created_at).astimezone().isoformat(timespec="seconds"),
            "batched": self.batched,
            "kind": "suggestion",
        }


@dataclass
class GateSignals:
    """Everything the gate knows about the current moment."""

    paused: bool = False
    typing: bool = False
    in_call: bool = False
    fullscreen: bool = False
    quiet_hours: bool = False
    rate_limited: bool = False
    idle_seconds: float | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "paused": self.paused,
            "typing": self.typing,
            "in_call": self.in_call,
            "fullscreen": self.fullscreen,
            "quiet_hours": self.quiet_hours,
            "rate_limited": self.rate_limited,
            "idle_seconds": self.idle_seconds,
        }


class AttentionGate:
    """Suppression, rate limiting and digest batching for proactive surfaces."""

    def __init__(
        self,
        *,
        settings_provider: Callable[[], Any],
        snapshot_provider: Callable[[], dict[str, Any]] | None = None,
        journal: Any = None,
        enqueue: Callable[[str], Any] | None = None,
        mute_setter: Callable[[str, str], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings_provider
        self._mute_setter = mute_setter
        self._snapshot = snapshot_provider or (lambda: {})
        self._journal = journal
        self._enqueue = enqueue
        self._clock = clock
        self._items: list[Suggestion] = []
        self._fired: list[float] = []
        self._last_by_pattern: dict[str, float] = {}
        self._active_since: float | None = None
        self._long_session_fired_for: float | None = None

    # -- signals ------------------------------------------------------------

    def signals(self, snapshot: dict[str, Any] | None = None, now: float | None = None) -> GateSignals:
        settings = self._settings()
        snap = snapshot if snapshot is not None else self._snapshot()
        now = self._clock() if now is None else now
        signals = GateSignals()

        if not getattr(settings, "enabled", True) or getattr(settings, "paused", False):
            signals.paused = True
            signals.reasons.append("awareness is paused")

        idle = snap.get("idle") or {}
        idle_seconds = (idle.get("value") or {}).get("seconds") if idle.get("ok") else None
        signals.idle_seconds = idle_seconds
        if isinstance(idle_seconds, (int, float)) and idle_seconds <= float(getattr(settings, "typing_seconds", 3.0)):
            signals.typing = True
            signals.reasons.append("typing")

        if getattr(settings, "respect_calls", True) and snap.get("in_call"):
            signals.in_call = True
            signals.reasons.append("in a call")

        fullscreen = snap.get("fullscreen") or {}
        if getattr(settings, "respect_fullscreen", True) and fullscreen.get("ok") and fullscreen.get("value"):
            signals.fullscreen = True
            signals.reasons.append("fullscreen")

        if self._in_quiet_hours(settings, now):
            signals.quiet_hours = True
            signals.reasons.append("quiet hours")

        if not self._rate_ok(settings, now):
            signals.rate_limited = True
            signals.reasons.append("rate limited")
        return signals

    def can_speak(self, snapshot: dict[str, Any] | None = None, now: float | None = None) -> dict[str, Any]:
        return self.signals(snapshot=snapshot, now=now).to_dict()

    @staticmethod
    def _in_quiet_hours(settings: Any, now: float) -> bool:
        window = settings.quiet_window() if hasattr(settings, "quiet_window") else None
        if not window:
            return False
        start, end = window
        local = datetime.fromtimestamp(now)
        current = local.hour * 60 + local.minute
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def _rate_ok(self, settings: Any, now: float) -> bool:
        max_per_hour = int(getattr(settings, "max_per_hour", 2) or 0)
        min_gap = float(getattr(settings, "min_gap_minutes", 20) or 0) * 60.0
        self._fired = [ts for ts in self._fired if now - ts < RATE_WINDOW_SECONDS]
        if max_per_hour <= 0:
            return False
        if len(self._fired) >= max_per_hour:
            return False
        if self._fired and now - self._fired[-1] < min_gap:
            return False
        return True

    # -- offering -----------------------------------------------------------

    def offer(
        self,
        *,
        pattern: str,
        title: str,
        body: str,
        task: str = "",
        snapshot: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Fire a suggestion if the moment allows it, otherwise batch it."""
        settings = self._settings()
        now = self._clock() if now is None else now
        muted_until = self._muted_until(settings, pattern, now)
        if muted_until is not None:
            return {"delivered": False, "batched": False, "reason": f"{pattern} is muted until {muted_until}"}

        signals = self.signals(snapshot=snapshot, now=now)
        self._prune(now)

        suggestion = Suggestion(
            id=f"sug_{uuid.uuid4().hex[:10]}",
            pattern=str(pattern),
            title=str(title),
            body=str(body),
            task=str(task),
            created_at=now,
            batched=not signals.allowed,
        )
        if signals.allowed:
            suggestion.fired_at = now
            self._fired.append(now)
            self._last_by_pattern[suggestion.pattern] = now
        self._items.append(suggestion)
        self._record(
            "suggestion_offered",
            {"pattern": suggestion.pattern, "id": suggestion.id, "batched": suggestion.batched, "reasons": signals.reasons},
        )
        return {
            "delivered": signals.allowed,
            "batched": suggestion.batched,
            "reason": None if signals.allowed else signals.reasons[0] if signals.reasons else "suppressed",
            "reasons": signals.reasons,
            "suggestion": suggestion.view(),
            "digest_count": self._batch_count(),
        }

    def suggestions(self, now: float | None = None) -> dict[str, Any]:
        """What a surface should show: live items, plus at most one digest."""
        now = self._clock() if now is None else now
        snapshot = self._snapshot()
        signals = self.signals(snapshot=snapshot, now=now)
        self._prune(now)
        visible = [item.view() for item in self._items if not item.batched]
        digest = None
        batch = [item for item in self._items if item.batched]
        if batch and signals.allowed:
            digest = {
                "id": f"{DIGEST_PREFIX}{len(batch)}",
                "kind": "digest",
                "count": len(batch),
                "title": f"{len(batch)} thing{'s' if len(batch) != 1 else ''} waited while you were busy",
                "body": "Nothing was shown while you were typing, in a call or fullscreen.",
                "patterns": sorted({item.pattern for item in batch}),
                "items": [item.id for item in batch],
                "batched": True,
            }
        return {"items": visible, "digest": digest, "gate": signals.to_dict(), "count": len(visible) + (1 if digest else 0)}

    def respond(self, suggestion_id: str, action: str) -> dict[str, Any]:
        """Accept, mute for seven days, or clear a digest."""
        action = str(action or "").strip().lower()
        if action not in {"accept", "not_now", "dismiss"}:
            return {"ok": False, "error": f"unknown action '{action}'"}
        now = self._clock()

        if str(suggestion_id).startswith(DIGEST_PREFIX):
            return self._respond_digest(str(suggestion_id), action, now)

        item = next((entry for entry in self._items if entry.id == suggestion_id), None)
        if item is None:
            return {"ok": False, "error": "unknown suggestion"}

        self._items.remove(item)
        if action == "accept":
            queued = self._dispatch(item)
            self._record("suggestion_accepted", {"pattern": item.pattern, "id": item.id, "queued": queued.get("queued", False)})
            return {"ok": True, "status": "accepted", "pattern": item.pattern, "task": item.task, **queued}

        until = datetime.fromtimestamp(now + MUTE_DAYS * 86400).astimezone().isoformat(timespec="seconds")
        self._mute(item.pattern, until)
        self._record("suggestion_dismissed", {"pattern": item.pattern, "id": item.id})
        return {"ok": True, "status": "muted", "pattern": item.pattern, "muted_until": until}

    def _respond_digest(self, digest_id: str, action: str, now: float) -> dict[str, Any]:
        batch = [item for item in self._items if item.batched]
        if not batch:
            return {"ok": False, "error": "no digest is pending"}
        if digest_id != f"{DIGEST_PREFIX}{len(batch)}":
            return {"ok": False, "error": "that digest is no longer pending"}
        if action == "accept":
            for item in batch:
                item.batched = False
            self._record("digest_opened", {"count": len(batch)})
            return {"ok": True, "status": "opened", "count": len(batch), "ids": [item.id for item in batch]}
        for item in batch:
            self._items.remove(item)
        self._record("digest_dismissed", {"count": len(batch)})
        return {"ok": True, "status": "dismissed", "count": len(batch)}

    def _dispatch(self, item: Suggestion) -> dict[str, Any]:
        if not item.task:
            return {"queued": False, "reason": "this suggestion needs no work"}
        if self._enqueue is None:
            return {"queued": False, "reason": "no runtime queue is attached"}
        try:
            result = self._enqueue(item.task)
            return {"queued": True, "result": result}
        except Exception as exc:  # noqa: BLE001 - accepting must never raise at the surface
            return {"queued": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}

    # -- built-in sources ---------------------------------------------------

    def tick(self, snapshot: dict[str, Any] | None = None, now: float | None = None) -> list[dict[str, Any]]:
        """Evaluate the shipped sources once. Cheap, idempotent, never throws out."""
        settings = self._settings()
        if not getattr(settings, "enabled", True) or not getattr(settings, "proactive", True):
            return []
        now = self._clock() if now is None else now
        snap = snapshot if snapshot is not None else self._snapshot()
        fired: list[dict[str, Any]] = []
        try:
            fired.extend(self._source_low_battery(settings, snap, now))
            fired.extend(self._source_long_session(settings, snap, now))
        except Exception:
            return fired
        return fired

    def _source_low_battery(self, settings: Any, snap: dict[str, Any], now: float) -> list[dict[str, Any]]:
        power = snap.get("power") or {}
        if not power.get("ok"):
            return []
        value = power.get("value") or {}
        percent = value.get("percent")
        threshold = int(getattr(settings, "battery_warn_percent", 20) or 20)
        if value.get("on_ac") or not isinstance(percent, int) or percent > threshold:
            return []
        return [
            self.offer(
                pattern="power.low_battery",
                title=f"Battery at {percent}%",
                body="You are on battery and below the level you asked to hear about. Hermus can hold long jobs until you plug in.",
                task="Tell me in one line which running Hermus jobs would be interrupted if this machine lost power soon.",
                snapshot=snap,
                now=now,
            )
        ]

    def _source_long_session(self, settings: Any, snap: dict[str, Any], now: float) -> list[dict[str, Any]]:
        idle = snap.get("idle") or {}
        if not idle.get("ok"):
            return []
        seconds = float((idle.get("value") or {}).get("seconds") or 0.0)
        if seconds > IDLE_BREAK_SECONDS:
            self._active_since = None
            return []
        if self._active_since is None:
            self._active_since = now
            self._long_session_fired_for = None
            return []
        if self._long_session_fired_for == self._active_since:
            return []
        minutes = int(getattr(settings, "long_session_minutes", 90) or 90)
        elapsed = now - self._active_since
        if elapsed < minutes * 60:
            return []
        self._long_session_fired_for = self._active_since
        return [
            self.offer(
                pattern="focus.long_session",
                title=f"{int(elapsed // 60)} minutes at the desk",
                body=f"You have been active for {int(elapsed // 60)} minutes without a break. Nothing needs doing about it; this is just the one nudge you asked for.",
                task="Summarise where I left off in this session in two lines, so I can stop and pick it up later.",
                snapshot=snap,
                now=now,
            )
        ]

    # -- helpers ------------------------------------------------------------

    def _muted_until(self, settings: Any, pattern: str, now: float) -> str | None:
        muted = getattr(settings, "muted_patterns", {}) or {}
        raw = muted.get(pattern)
        if not raw:
            return None
        try:
            expiry = datetime.fromisoformat(str(raw))
        except ValueError:
            return None
        if expiry.timestamp() > now:
            return expiry.astimezone().isoformat(timespec="seconds")
        return None

    def _mute(self, pattern: str, until_iso: str) -> None:
        if self._mute_setter is not None:
            self._mute_setter(pattern, until_iso)
            return
        muted = getattr(self._settings(), "muted_patterns", None)
        if isinstance(muted, dict):
            muted[pattern] = until_iso

    def _record(self, kind: str, data: dict[str, Any]) -> None:
        if self._journal is None:
            return
        try:
            self._journal.record(kind, data)
        except Exception:
            return

    def _prune(self, now: float) -> None:
        self._items = [item for item in self._items if now - item.created_at < SUGGESTION_TTL_SECONDS]

    def _batch_count(self) -> int:
        return sum(1 for item in self._items if item.batched)


_gates: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def attention_gate(service: Any = None) -> AttentionGate:
    """The gate bound to an awareness service (one per service instance)."""
    if service is None:
        from .awareness import get_awareness

        service = get_awareness()
    gate = _gates.get(service)
    if gate is not None:
        return gate
    gate = AttentionGate(
        settings_provider=service.store.load,
        snapshot_provider=service.snapshot,
        journal=service.journal,
        enqueue=_runtime_enqueue,
        mute_setter=service.store.mute_pattern,
    )
    _gates[service] = gate
    return gate


def _runtime_enqueue(task: str) -> Any:
    """Hand an accepted suggestion to the normal queue; approval rules still apply."""
    from gateway.queue import job_queue

    return job_queue.submit(
        "runtime.turn",
        {"task": task, "trigger": {"source": "awareness"}, "awareness": True},
        session_key="awareness",
    ).id


__all__ = ["AttentionGate", "GateSignals", "Suggestion", "attention_gate", "MUTE_DAYS"]
