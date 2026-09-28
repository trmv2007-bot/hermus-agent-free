"""The ambient loop: what HERMUS watches, and the rare moment it speaks up.

``core/intent.py`` is the decision function. This module is the thing that
*calls* it — the caller that module was written to be called by and, until now,
had never been. The split is the whole design:

* :mod:`core.intent` decides **whether** to speak. It is pure, it reads no
  clock, it does no IO, and every path out of it carries a reason.
* :mod:`core.proactivity` decides **what to look at** and **where the words go**.
  It owns the IO, the clock, the thread, and the wire.

That separation is why this file is allowed to be the untidy one. Everything
here can be wrong in the way that produces noise — a probe that fires on every
tick, a threshold that flaps, a detail string with a timestamp in it — and the
judge downstream is what stops that from becoming a talking product. So the
probes are written to be *boring*: edge-triggered, deduplicated, and willing to
return nothing. The default expectation of a tick is silence.

Three things this loop is careful about, each of which is a real bug that a
simpler version would ship
-----------------------------------------------------------

**Cooldowns must not be spent on words nobody heard.** ``IntentJudge.observe``
records an utterance in its history the moment it returns ``should_speak``. If
this loop then decides not to deliver that utterance — because it is 3am, or
because the per-tick cap was already full — the cooldown has been consumed by
speech that never happened, and the *same fault* is now suppressed for the next
three minutes of the user's waking life. So every gate that can suppress an
utterance runs **before** :meth:`IntentJudge.observe`, never after. The judge
only ever sees candidates it is actually being allowed to speak.

**A detail string is an identity, not a message.** ``event_signature`` keys
repeat suppression and the error escalation ladder on ``event_detail``, and a
timestamp in there makes every occurrence unique, which silently disables both.
Every probe here builds a stable key — a component/operation/fault-hash for an
error, a job *kind* for a finished task, a mission id, a bare ``disk_free``.

**Time-of-day is a gate, not an event.** There is no "it is late" observation
worth announcing; a system that greets you because a clock moved is the
wallpaper failure the intent design exists to prevent. What the wall-clock is
actually good for is deciding whether a real observation may be *spoken at
all* (:class:`QuietHours`), and for telling the judge how long the room has
really been quiet after a restart, when its in-memory history is empty and
would otherwise report "never spoken to" forever.

On restart
----------
A fresh process has an empty :class:`~core.intent.SpeechHistory`, and
``gap_seconds`` returns ``None``, which the judge reads as *unproven attention*
rather than *infinitely quiet* — correctly, so that the first routine event
after a boot does not announce itself uninvited. The cost is that a genuinely
long-finished task would then be unspeakable until something else spoke first.
:class:`FloorState` closes that gap from the outside: the gateway tells the loop
when it last put words in the room, and the loop passes that as
``seconds_since_last_speech``. A gap that is *known* is passed; one that is not
is left as ``None`` and the judge stays conservative.

What this module does not do
----------------------------
* It does not call a model. ``Decision.line`` is the utterance, verbatim. A
  generator belongs downstream of the judge saying yes, never inside it.
* It does not execute anything, schedule anything, or hold a permission. Every
  proactive word is a sentence, not an action.
* It does not decide. There is no "is this worth saying" logic here beyond the
  two mechanical gates (quiet hours, per-tick cap); that is the judge's job and
  duplicating it here would make the two disagree.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, time as dtime
from typing import Any, Optional, Protocol

from core.intent import (
    Decision,
    EventKind,
    IntentJudge,
    JudgeConfig,
    Moment,
    RoomState,
    SAY_NOTHING,
    Urgency,
    format_duration,
)
from core.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "FloorState",
    "JobCompletionProbe",
    "LongSilence",
    "MissionCompletionProbe",
    "Observation",
    "ProactivityConfig",
    "ProactivityLoop",
    "ResourceThresholdProbe",
    "RuntimeIssueProbe",
    "Utterance",
    "default_probes",
    "get_floor",
    "get_proactivity",
    "in_quiet_hours",
    "start_proactivity",
    "stop_proactivity",
]


# --------------------------------------------------------------------------
# The run/tick vocabulary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One thing a probe saw that might be worth a sentence.

    Deliberately not a :class:`~core.intent.Moment`. A ``Moment`` is the
    judge's *input* and carries the room/floor context that only the loop
    knows; an ``Observation`` is a probe's *output* and carries only the fact.
    The loop joins them, so a probe never has to know what time it is or who
    holds the floor.
    """

    event: EventKind
    #: Stable identity for "the same thing again". Never a timestamp.
    detail: str
    #: Did the system actually check this. An unverified completion is
    #: silence, and the judge enforces that above everything else.
    verified: bool
    #: Which probe produced it, for the log line and the UI.
    source: str
    #: Room override. A probe that knows the room is mid-mission may pin it;
    #: ``None`` means "whatever presence says right now".
    room: Optional[RoomState] = None
    #: Extra, redacted-before-publish context for the UI (elapsed seconds, a
    #: threshold value, an error type). Never credentials.
    meta: dict[str, Any] = field(default_factory=dict)
    #: How urgent this fact is *before* the judge weighs the room. Used only to
    #: order candidates so the per-tick cap keeps the best one, never to
    #: decide.
    weight: int = 0


@dataclass(frozen=True)
class Utterance:
    """A decision that was allowed through, with the reasons it survived.

    ``origin`` is the field the frontend keys on to draw an unsolicited bubble
    differently from a reply. ``reason`` travels with it so a user can ask why
    the assistant chose to speak (Amershi et al., CHI 2019, guideline 11) —
    without it the bubble is just a popup.
    """

    id: str
    text: str
    reason: str
    rule: str
    urgency: str
    confidence: str
    source: str
    event: str
    detail: str
    at: float
    origin: str = "unsolicited"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "utterance_id": self.id,
            "type": "hermus_spoke",
            "origin": self.origin,
            "text": self.text,
            "reason": self.reason,
            "rule": self.rule,
            "urgency": self.urgency,
            "confidence": self.confidence,
            "source": self.source,
            "event": self.event,
            "detail": self.detail,
            "at": self.at,
            "meta": dict(self.meta),
        }


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProactivityConfig:
    """How often the loop looks, and the few hard gates in front of it.

    Defaults are deliberately slow. The judge is what rations *content*; this
    is what rations *attention*, and a 20-second poll on a machine with nothing
    wrong is a busy loop that has learned to be ignored.
    """

    enabled: bool = True
    #: Seconds between ticks. Also the floor on how often a burst of events can
    #: reach the user, independent of the judge's own minimum gap.
    interval_seconds: float = 20.0
    #: Local-time window in which only an interrupt may be spoken. ``""``
    #: disables the gate. Aware of a crossing midnight ("22:30-08:00").
    quiet_hours: str = "22:30-08:00"
    #: The most utterances one tick may deliver. Two lines of unprompted text
    #: in the same instant is already a conversation, and a conversation is
    #: something the user started.
    max_utterances_per_tick: int = 1
    #: A job shorter than this is not a long-running task and is not news.
    long_job_seconds: float = 120.0
    #: Free disk below this percentage is a fault worth one interruption.
    disk_min_free_pct: float = 5.0
    #: Refuse to consider anything while the gateway is draining at shutdown.
    drain_grace_seconds: float = 10.0

    @staticmethod
    def from_config() -> "ProactivityConfig":
        """Build from :mod:`core.config`, falling back to the defaults above.

        Read through ``getattr`` so a build whose config predates this module
        still starts; a missing flag must not be a reason HERMUS cannot boot.
        """
        try:
            from core.config import config
        except Exception:  # noqa: BLE001 - config is optional at this layer
            return ProactivityConfig()
        return ProactivityConfig(
            enabled=bool(getattr(config, "proactivity_enabled", True)),
            interval_seconds=float(getattr(config, "proactivity_interval_seconds", 20.0) or 20.0),
            quiet_hours=str(getattr(config, "proactivity_quiet_hours", "22:30-08:00")),
            max_utterances_per_tick=max(1, int(getattr(config, "proactivity_max_per_tick", 1) or 1)),
            long_job_seconds=float(getattr(config, "proactivity_long_job_seconds", 120.0) or 120.0),
            disk_min_free_pct=float(getattr(config, "proactivity_disk_min_free_pct", 5.0) or 5.0),
        )

    def quiet_window(self) -> Optional[tuple[dtime, dtime]]:
        """Parse ``"HH:MM-HH:MM"``. ``None`` when unset or unparseable.

        A malformed value disables the gate rather than raising: a typo in a
        settings file must not stop the loop, and the conservative failure
        direction (keep looking, let the judge ration) is the one that keeps
        the feature alive.
        """
        raw = (self.quiet_hours or "").strip()
        if not raw or "-" not in raw:
            return None
        start_text, _, end_text = raw.partition("-")
        try:
            start = dtime.fromisoformat(start_text.strip())
            end = dtime.fromisoformat(end_text.strip())
        except ValueError:
            return None
        return start, end


def in_quiet_hours(when: dtime, window: Optional[tuple[dtime, dtime]]) -> bool:
    """Is ``when`` inside the window, including a wrap past midnight."""
    if window is None:
        return False
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= when < end
    return when >= start or when < end


# --------------------------------------------------------------------------
# Floor / room context
# --------------------------------------------------------------------------


class FloorState:
    """What the loop knows about the room and who holds the floor.

    Kept outside the judge on purpose. ``core/intent`` refuses to infer user
    state from behavioural signals — that refusal is a design decision, not a
    missing feature — so the evidence has to be handed to it. The gateway is
    the only thing that can supply it honestly, because it is the only thing
    that sees a turn start.

    Every setter is a *fact*, not an inference. ``user_typing`` is True because
    a client said so, not because a gap between keystrokes looked short.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._room = RoomState.IDLE
        self._user_just_spoke = False
        self._user_mid_sentence = False
        self._user_typing = False
        self._blocked_since: Optional[float] = None
        self._last_spoke_at: Optional[float] = None

    # -- room ---------------------------------------------------------------
    def set_room(self, room: RoomState | str, now: Optional[float] = None) -> None:
        """Record the room's state, and when a block began.

        ``now`` is the same clock the loop passes to the judge. It has to be:
        the judge is handed a caller-supplied ``Moment.now`` precisely so a
        test can place two events three minutes apart without sleeping, and a
        floor that quietly read ``time.monotonic()`` instead would put a real
        wall-clock timestamp into the same moment. The two would then disagree
        — every block would read as "0s old" under simulated time, and the
        block-age escalation would be unreachable in any test that could
        actually exercise it. Defaulting to the real clock keeps the common
        caller (the gateway, which has no simulated time) working.
        """
        at = time.monotonic() if now is None else float(now)
        with self._lock:
            value = room if isinstance(room, RoomState) else RoomState(str(room))
            if value is RoomState.BLOCKED and self._room is not RoomState.BLOCKED:
                self._blocked_since = at
            elif value is not RoomState.BLOCKED:
                self._blocked_since = None
            self._room = value

    def room(self) -> RoomState:
        with self._lock:
            return self._room

    # -- user floor ---------------------------------------------------------
    def note_user_speech(self, *, mid_sentence: bool = False) -> None:
        """The user said something. ``mid_sentence`` while they are still going."""
        with self._lock:
            self._user_just_spoke = True
            self._user_mid_sentence = bool(mid_sentence)

    def set_typing(self, typing: bool) -> None:
        with self._lock:
            self._user_typing = bool(typing)
            if typing:
                self._user_mid_sentence = True

    def clear_floor(self) -> None:
        """Called at a turn boundary, once the user has stopped talking."""
        with self._lock:
            self._user_just_spoke = False
            self._user_mid_sentence = False
            self._user_typing = False

    # -- speech accounting --------------------------------------------------
    def note_spoke(self, now: Optional[float] = None) -> None:
        """The room heard words — from this loop or from a reply."""
        with self._lock:
            self._last_spoke_at = time.monotonic() if now is None else float(now)

    def seconds_since_last_speech(self, now: float) -> Optional[float]:
        """A *known* gap, or ``None`` when nothing is known.

        ``None`` is the honest answer after a cold start and it matters: the
        judge reads it as unproven attention rather than as a long silence, so
        a reboot cannot manufacture a quiet window worth filling.
        """
        with self._lock:
            if self._last_spoke_at is None:
                return None
            return max(0.0, now - self._last_spoke_at)

    def blocked_for_seconds(self, now: float) -> float:
        with self._lock:
            if self._blocked_since is None:
                return 0.0
            return max(0.0, now - self._blocked_since)

    def snapshot(self, now: Optional[float] = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        with self._lock:
            return {
                "room": self._room.value,
                "user_just_spoke": self._user_just_spoke,
                "user_mid_sentence": self._user_mid_sentence,
                "user_typing": self._user_typing,
                "blocked_for_seconds": round(self.blocked_for_seconds(now), 1),
                "seconds_since_last_speech": self.seconds_since_last_speech(now),
            }

    def moment_fields(self, now: float) -> dict[str, Any]:
        """The subset of context that rides on every :class:`Moment`.

        ``blocked_since is not None`` rather than a truthiness test, because
        ``0.0`` is a perfectly valid block timestamp — it is what a caller
        driving the loop from a zeroed clock will pass. Testing truthiness
        would silently report that block as "never blocked" and make the whole
        block-age escalation unreachable for exactly the callers that reason
        about simulated time.
        """
        with self._lock:
            return {
                "user_just_spoke": self._user_just_spoke,
                "user_mid_sentence": self._user_mid_sentence,
                "user_typing": self._user_typing,
                "blocked_for_seconds": (
                    max(0.0, now - self._blocked_since) if self._blocked_since is not None else 0.0
                ),
            }


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------


class Probe(Protocol):
    """A source of observations. Implementations do the IO; the loop does not."""

    name: str

    def poll(self, now: float) -> list[Observation]:  # pragma: no cover - protocol
        ...


def _fault_key(*parts: str) -> str:
    """A short, stable hash of the *shape* of a fault.

    The same failure with a different row number is the same failure. A key
    built from the raw message would make every occurrence unique, which
    defeats repeat suppression and lets a flapping log talk over a human.
    """
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:8]


class RuntimeIssueProbe:
    """Errors already recorded by the runtime's structured issue log.

    Reads :func:`core.run_events.recent_issues` rather than scraping log files,
    because that ring is where every ``except`` that was converted from a
    silent pass already lands with a component, an operation and a type. This
    probe therefore adds no new instrumentation requirement: anything the
    codebase already decided was worth recording is worth one sentence.

    Dedup is per fault key and is *not* time-bounded. A fault that keeps
    happening is escalated by the judge's own occurrence ladder, which needs to
    keep seeing it; dropping repeats here would quietly disable escalation.
    """

    name = "runtime_issue"
    #: What the judge counts as the cost of speaking: an error interrupts once.
    _event = EventKind.ERROR

    def __init__(self, limit: int = 40) -> None:
        self._seen: set[str] = set()
        self._limit = max(1, int(limit))

    def poll(self, now: float) -> list[Observation]:
        try:
            from core.run_events import recent_issues

            issues = recent_issues(limit=self._limit)
        except Exception as exc:  # noqa: BLE001 - a probe must never break a tick
            logger.debug(f"[Proactivity] issue probe unavailable: {type(exc).__name__}: {exc}")
            return []
        out: list[Observation] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            component = str(issue.get("component") or "runtime")
            operation = str(issue.get("operation") or "unknown")
            error = str(issue.get("error") or "")
            error_type = str(issue.get("error_type") or "")
            # The key is the fault's *identity*: where it happened and what
            # shape it was. Two occurrences of the same bug share it, so the
            # judge's escalation ladder can see the second one. The hash is
            # only a differentiator for two distinct faults in the same place —
            # it is never the part a person reads, because `detail` is what ends
            # up inside the spoken sentence ("X failed…"). Keeping it there
            # would make the assistant say a hex string at 3am.
            fingerprint = _fault_key(error_type, error[:120])
            key = f"{component}.{operation}#{fingerprint}"
            if key in self._seen:
                continue
            self._seen.add(key)
            # Bound the memory. The ring is already bounded; this set is not,
            # and an unbounded dedup set in a long-lived process is a slow
            # leak that looks like nothing until it does.
            if len(self._seen) > 500:
                self._seen = set(list(self._seen)[-250:])
            # Spoken as "component.operation" — enough to locate the fault by
            # hand, short enough to say out loud. The fingerprint rides along in
            # meta for the log and the UI, where nobody is reading it aloud.
            out.append(
                Observation(
                    event=self._event,
                    detail=f"{component}.{operation}",
                    verified=True,
                    source=self.name,
                    meta={
                        "component": component,
                        "operation": operation,
                        "fingerprint": fingerprint,
                        "error_type": error_type,
                        "error": error[:200],
                    },
                    weight=90,
                )
            )
        return out


class JobCompletionProbe:
    """A long-running job finished, or failed, and nobody is waiting on it.

    Reports the job *kind*, not its id: a run of ``runtime.turn`` finishing is
    one piece of news however many times it happens, and six distinct ids would
    be six announcements of the same sentence. That is the difference between a
    queue that is busy and a queue that has news.

    ``event_verified`` is taken from ``has_result``, not from the status. A job
    that reports ``succeeded`` with no result is a return value, not an
    outcome, and the judge refuses to announce it — which is the correct answer
    and is exercised in the tests rather than assumed.
    """

    name = "job"

    def __init__(self, long_job_seconds: float = 120.0) -> None:
        self._long = max(0.0, float(long_job_seconds))
        self._announced: set[str] = set()
        #: job id -> True once it has been seen running/queued. A job that was
        #: already terminal when the loop started is not news; it is history.
        self._in_flight: dict[str, bool] = {}

    def poll(self, now: float) -> list[Observation]:
        try:
            from gateway.queue import job_queue

            jobs = job_queue.list_jobs(limit=50)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Proactivity] job probe unavailable: {type(exc).__name__}: {exc}")
            return []

        out: list[Observation] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("id") or "")
            status = str(job.get("status") or "")
            kind = str(job.get("kind") or job.get("type") or "job")
            if not job_id:
                continue
            if status in ("running", "queued"):
                self._in_flight[job_id] = True
                continue

            started_running = self._in_flight.pop(job_id, False)
            if not started_running:
                # Never saw it in flight. Announcing the backlog on startup is
                # how a quiet system greets you with yesterday's news.
                continue
            if job_id in self._announced:
                continue
            self._announced.add(job_id)
            if len(self._announced) > 500:
                self._announced = set(list(self._announced)[-250:])

            elapsed_ms = job.get("elapsed_ms")
            try:
                elapsed = float(elapsed_ms or 0) / 1000.0
            except (TypeError, ValueError):
                elapsed = 0.0
            if elapsed < self._long:
                # A job that finished in under a minute was never a
                # long-running task, and "your 2s command finished" is noise
                # dressed as a report.
                continue

            has_result = bool(job.get("has_result"))
            failed = status not in ("succeeded",)
            # The job *kind* is the identity and also the speakable part: a
            # queue running `runtime.turn` is one piece of news however many
            # times it runs, and six job ids would be six announcements of the
            # same sentence. The state stays in meta, where the UI can show it
            # without it appearing in the spoken line.
            out.append(
                Observation(
                    event=EventKind.ERROR if failed else EventKind.TOOL_FINISHED,
                    detail=kind,
                    verified=has_result and not failed,
                    source=self.name,
                    meta={
                        "job_id": job_id,
                        "kind": kind,
                        "status": status,
                        "elapsed": format_duration(elapsed),
                        "error": str(job.get("error") or "")[:200],
                    },
                    weight=85 if failed else 40,
                )
            )
        return out


class MissionCompletionProbe:
    """A mission reached a terminal state, filtered on what was *verified*.

    ``state == completed`` is a lifecycle position. ``outcome_state`` is how
    much of the claim the system checked for itself, and the two disagree
    often enough to matter — so this probe reads the second one and reports
    ``verified=False`` for anything short of :attr:`OutcomeState.VERIFIED`.
    The judge then turns that into silence with an explicit reason, which is
    the correct outcome: a mission that finished without anyone checking is
    not a result.
    """

    name = "mission"
    #: Outcome states strong enough to announce. Anything else is claimed, not
    #: confirmed, and claiming is the failure mode worth avoiding.
    _ANNOUNCABLE = frozenset({"verified", "partially_verified"})

    def __init__(self) -> None:
        self._reported: dict[str, str] = {}

    def poll(self, now: float) -> list[Observation]:
        try:
            from core.mission import mission_engine

            reports = mission_engine.list_missions()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Proactivity] mission probe unavailable: {type(exc).__name__}: {exc}")
            return []

        out: list[Observation] = []
        for report in reports or []:
            mission_id = str(getattr(report, "mission_id", "") or "")
            state = str(getattr(report, "state", "") or "")
            if not mission_id or state not in ("completed", "failed", "cancelled"):
                continue
            if self._reported.get(mission_id) == state:
                continue
            self._reported[mission_id] = state
            outcome = str(getattr(report, "outcome_state", "unknown") or "unknown")
            goal = str(getattr(report, "goal", "") or mission_id)[:160]
            # The id is the identity and it is also speakable — "msn_ab12 is
            # done" is a thing a person can act on, unlike a fingerprint.
            out.append(
                Observation(
                    event=EventKind.MISSION_COMPLETE if state == "completed" else EventKind.ERROR,
                    detail=mission_id,
                    verified=outcome in self._ANNOUNCABLE and state == "completed",
                    source=self.name,
                    meta={
                        "mission_id": mission_id,
                        "state": state,
                        "outcome_state": outcome,
                        "goal": goal,
                    },
                    weight=70,
                )
            )
        if len(self._reported) > 300:
            self._reported = dict(list(self._reported.items())[-150:])
        return out


class ResourceThresholdProbe:
    """A resource crossed a line worth interrupting about — once.

    Edge-triggered on purpose. A level-triggered version re-reports the same
    full disk every tick; the judge would suppress most of them, but the
    suppression is the wrong mechanism to be relying on for a condition that is
    still true. Re-arming only after recovery makes the crossing the event,
    which is what it actually is.

    Disk is used rather than CPU or memory because it is the one threshold on
    this machine that is both cheap to read and genuinely unrecoverable on its
    own: a full log volume stops the audit trail that every other guarantee in
    this codebase depends on.
    """

    name = "resource"
    _event = EventKind.ERROR

    def __init__(self, path: str | os.PathLike[str] | None = None, min_free_pct: float = 5.0) -> None:
        self._min_free = max(0.0, float(min_free_pct))
        self._path = path
        #: True while the threshold is known to be breached. Starts False, so
        #: a machine that is *already* over the line at boot reports once and
        #: then stays quiet until it recovers.
        self._breached = False
        self._last_read: Optional[float] = None

    def _target(self) -> str:
        if self._path is not None:
            return str(self._path)
        try:
            from core.workspace import workspace

            return str(workspace.dirs["logs"])
        except Exception:  # noqa: BLE001 - fall back to a path that always exists
            return os.getcwd()

    def poll(self, now: float) -> list[Observation]:
        # Free space does not change meaningfully between ticks, and a stat
        # call per tick on a poll loop is how an ambient feature becomes a
        # measurable cost for zero extra information.
        if self._last_read is not None and (now - self._last_read) < 60.0:
            return []
        self._last_read = now
        try:
            usage = shutil.disk_usage(self._target())
        except (OSError, ValueError) as exc:
            logger.debug(f"[Proactivity] resource probe unavailable: {exc}")
            return []
        if not usage.total:
            return []
        free_pct = (usage.free / usage.total) * 100.0
        if free_pct >= self._min_free:
            if self._breached:
                # Recovery re-arms the edge so the next crossing is news again.
                self._breached = False
            return []
        if self._breached:
            return []
        self._breached = True
        return [
            Observation(
                event=self._event,
                detail="disk_free",
                verified=True,
                source=self.name,
                meta={
                    "free_pct": round(free_pct, 1),
                    "threshold_pct": self._min_free,
                    "path": self._target(),
                },
                weight=80,
            )
        ]


@dataclass(frozen=True)
class LongSilence:
    """How long the room has been quiet, for the case where it is knowable.

    Not a probe — it produces no observation. It exists so ``status()`` and the
    log can answer "why has it not said anything?" with a number instead of a
    shrug, which is the question a quiet system provokes.
    """

    seconds: float
    known: bool

    def render(self) -> str:
        return format_duration(self.seconds) if self.known else "never spoken to"


def default_probes(config: ProactivityConfig) -> list[Any]:
    """The probe set a live gateway runs.

    Constructed lazily and defensively: an import error in one probe must not
    take the other two down with it, because a partially-blind loop that still
    respects the judge is much better than no loop.

    The machine-state probe is in this list because without it every other probe
    is about HERMUS itself: the loop could notice that one of its own jobs failed
    and stay silent while the machine ran out of memory or a build wedged the
    CPU. It reads the OS rather than the screen, which is exact and costs no
    model call - see ``core.machine_state`` for why that is not a vision
    problem, and ``core.vision_routing`` for the measured evidence that no
    vision model is installed on this machine.
    """
    probes: list[Any] = []
    for factory in (
        lambda: RuntimeIssueProbe(),
        lambda: JobCompletionProbe(config.long_job_seconds),
        lambda: MissionCompletionProbe(),
        lambda: ResourceThresholdProbe(min_free_pct=config.disk_min_free_pct),
        # Imported here, not at module scope. ``core.machine_state`` imports
        # ``Observation`` from this module, so a top-level import would be
        # circular. The failure mode this guards against is worth recording:
        # the first version imported it at the top of the module and every
        # ``default_probes()`` call logged "probe unavailable:
        # NameError: MachineStateProbe is not defined" and returned four probes
        # instead of five. The existing per-probe ``except`` swallowed it, which
        # is correct behaviour for a genuinely broken probe and terrible
        # behaviour for an import mistake - the loop would have run forever
        # without the ambient machine probe and nothing would say so.
        lambda: __import__("core.machine_state", fromlist=["MachineStateProbe"]).MachineStateProbe(),
    ):
        try:
            probes.append(factory())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Proactivity] probe unavailable: {type(exc).__name__}: {exc}")
    return probes


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


class ProactivityLoop:
    """Polls probes, asks the judge, delivers the rare yes.

    Two entry points, deliberately:

    * :meth:`consider` — poll, weigh, return. No IO beyond the probes, no
      delivery, no sleeping. This is what the tests drive.
    * :meth:`tick` — :meth:`consider` plus delivery. What the thread calls.

    Splitting them is what makes "it stayed quiet" a testable claim: the test
    can run three hours of simulated time in a millisecond and assert on the
    decisions, without a bus, a thread, or a clock it has to wait on.
    """

    def __init__(
        self,
        *,
        judge: IntentJudge | None = None,
        probes: Iterable[Any] | None = None,
        config: ProactivityConfig | None = None,
        floor: FloorState | None = None,
        emit: Callable[[Utterance], Any] | None = None,
        judge_config: JudgeConfig | None = None,
    ) -> None:
        self.config = config or ProactivityConfig()
        self.judge = judge or IntentJudge(config=judge_config)
        self.probes: list[Any] = list(probes) if probes is not None else default_probes(self.config)
        self.floor = floor or FloorState()
        self._emit = emit
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._quiet_window = self.config.quiet_window()
        #: Content the judge said was worth saying but at the wrong moment,
        #: waiting for the user's sentence to end. The judge is explicit that
        #: this is a queue and not a drop, so it is one. Bounded, because a
        #: stale line is worse than no line: anything older than the window is
        #: dropped with a reason rather than delivered into a conversation that
        #: has moved on.
        self._deferred: deque[tuple[Observation, Decision, float]] = deque(maxlen=8)
        self._defer_window = 120.0
        # Observability. Silence is a first-class outcome, which means it has
        # to be readable afterwards: how many ticks, what was seen, and why the
        # last utterance was the one it was.
        self._ticks = 0
        self._observations = 0
        self._spoken = 0
        self._suppressed = 0
        self._deferred_dropped = 0
        self._last_decision: Optional[Decision] = None
        self._last_utterance: Optional[Utterance] = None
        self._last_tick_at: Optional[float] = None

    # -- gates --------------------------------------------------------------
    def _quiet_now(self, wall: dtime) -> bool:
        return in_quiet_hours(wall, self._quiet_window)

    # -- the decision -------------------------------------------------------
    def consider(self, now: Optional[float] = None, *, wall: Optional[dtime] = None) -> list[Utterance]:
        """Run one tick's worth of judging. Returns only what may be said.

        No delivery happens here, so a caller that wants to inspect the
        decision without the world hearing it can call this directly.
        """
        now = time.monotonic() if now is None else float(now)
        wall = wall or datetime.now().time()
        with self._lock:
            self._ticks += 1
            self._last_tick_at = now

        # --- gate 1: is speaking allowed at all right now? -----------------
        # Checked before the judge is consulted at all, because observe()
        # records an utterance the moment it says yes. Suppressing afterwards
        # would spend a cooldown on silence and make the same fault
        # unspeakable for the next few minutes of the user's waking life.
        if self._quiet_now(wall):
            with self._lock:
                self._suppressed += 1
            logger.debug(f"[Proactivity] quiet hours ({self.config.quiet_hours}); holding every observation")
            return []

        # --- collect ---------------------------------------------------------
        candidates: list[Observation] = []
        for probe in self.probes:
            try:
                found = probe.poll(now) or []
            except Exception as exc:  # noqa: BLE001 - one bad probe is not a dead loop
                logger.warning(f"[Proactivity] probe {getattr(probe, 'name', probe)!r} failed: {type(exc).__name__}: {exc}")
                continue
            candidates.extend(o for o in found if isinstance(o, Observation))
        with self._lock:
            self._observations += len(candidates)

        # --- judge -----------------------------------------------------------
        # The deferred queue is drained before the "nothing new" shortcut below,
        # and that ordering is load-bearing. A held line usually has no fresh
        # observations behind it — it was the *only* thing that happened, which
        # is why it was deferred in the first place. Returning early on an empty
        # candidate list would therefore mean a tick in which the one tick that
        # could have released it is also the tick that refuses to look, and a
        # line held for a missing user turn would sit there until it went stale
        # and was dropped. The user would end their sentence and hear nothing.
        floor_fields = self.floor.moment_fields(now)
        gap = self.floor.seconds_since_last_speech(now)
        out: list[Utterance] = list(self._drain_deferred(now))
        if not candidates:
            # The common case, and the one the whole design is built around.
            return out

        # --- gate 2: the per-tick cap ---------------------------------------
        # Ordering here is a courtesy to the cap, not a decision: it decides
        # which candidate gets the *chance* to be judged, and the judge still
        # gets the final word on every one it lets through. Candidates past the
        # cap are never shown to the judge, so they cost no cooldown.
        candidates.sort(key=lambda o: o.weight, reverse=True)
        cap = max(1, int(self.config.max_utterances_per_tick))
        considered = candidates[:cap]

        budget = max(0, cap - len(out))
        for observation in candidates[:budget] if budget else []:
            moment = Moment(
                now=now,
                room=observation.room or self.floor.room(),
                event=observation.event,
                event_detail=observation.detail,
                event_verified=observation.verified,
                seconds_since_last_speech=gap,
                **floor_fields,
            )
            decision = self.judge.observe(moment)
            with self._lock:
                self._last_decision = decision
            if not decision.should_speak or decision.line == SAY_NOTHING:
                with self._lock:
                    self._suppressed += 1
                continue
            # A folded acknowledgement belongs inside a reply already in
            # flight. The ambient loop has no reply, so there is nowhere to put
            # it; surfacing it as a standalone bubble would open a second turn
            # the judge explicitly did not authorise. It stays in the history as
            # an occurrence, which is what lets it count toward escalation.
            if decision.fold_into_reply:
                with self._lock:
                    self._suppressed += 1
                continue
            utterance = Utterance(
                id=f"spoke_{int(now * 1000)}_{observation.source}",
                text=decision.line,
                reason=decision.reason,
                rule=decision.rule,
                urgency=decision.urgency.value,
                confidence=decision.confidence,
                source=observation.source,
                event=observation.event.value,
                detail=observation.detail,
                at=now,
                meta=dict(observation.meta),
            )
            if decision.defers:
                # Worth saying, wrong moment. Held rather than dropped, as the
                # judge asks.
                #
                # The announcement mark that ``observe`` just wrote is
                # withdrawn here, and this is the subtle part of the whole
                # deferral path. The judge records a mark the moment it
                # accepts an utterance, because for its other caller the
                # accepted line is about to be spoken. Here it is not: the line
                # is going into a queue the user cannot see. Left in place, the
                # mark starts a cooldown on a sentence nobody heard, and the
                # re-judgement a few seconds later — with the floor now clear
                # and the content unchanged — is refused by that cooldown. The
                # deferral would then expire as stale and the content would be
                # lost, which is the exact failure the queue exists to prevent.
                #
                # The *occurrence* stays. It genuinely happened, and it is what
                # the error escalation ladder counts, so removing that would
                # quietly disable escalation for deferred faults.
                self._withdraw_mark(observation, now)
                self._deferred.append((observation, decision, now))
                with self._lock:
                    self._suppressed += 1
                continue
            out.append(utterance)
        return out

    def _withdraw_mark(self, observation: Observation, now: float) -> None:
        """Remove the announcement mark for a line that was held, not spoken.

        Narrow by construction: only a mark with this exact signature *and* this
        exact timestamp is removed, so a real utterance of the same content at
        a nearby moment is never collateral damage. An empty or missing mark is
        not an error — the judge's own bookkeeping may legitimately have
        declined to record it.
        """
        from core.intent import event_signature

        signature = event_signature(observation.event, observation.detail)
        marks = self.judge.history.marks
        for index in range(len(marks) - 1, -1, -1):
            mark = marks[index]
            if mark.signature == signature and mark.at == now:
                del marks[index]
                return

    def _drain_deferred(self, now: float) -> list[Utterance]:
        """Release anything the judge deferred, now that the floor may be clear.

        Re-judged rather than replayed. The stored :class:`Decision` was
        computed under a floor condition that has, by hypothesis, changed, and
        a decision function's whole value is that the same inputs always give
        the same answer — so the honest way to ask "is this still worth saying
        now?" is to ask it again, not to trust a cached verdict.

        Staleness is a real drop, and it is logged. A line composed for a moment
        that has passed is not a delayed message, it is a wrong one.
        """
        if not self._deferred:
            return []
        floor_fields = self.floor.moment_fields(now)
        gap = self.floor.seconds_since_last_speech(now)
        out: list[Utterance] = []
        keep: list[tuple[Observation, Decision, float]] = []
        for observation, decision, queued_at in self._deferred:
            age = now - queued_at
            if age > self._defer_window:
                with self._lock:
                    self._deferred_dropped += 1
                logger.debug(
                    f"[Proactivity] dropped deferred {observation.detail}: "
                    f"{format_duration(age)} old, past the {format_duration(self._defer_window)} window"
                )
                continue
            # Still queued only while the floor is held; otherwise it would be
            # re-deferred forever inside a loop that never clears.
            if floor_fields["user_mid_sentence"] or floor_fields["user_typing"]:
                keep.append((observation, decision, queued_at))
                continue
            moment = Moment(
                now=now,
                room=observation.room or self.floor.room(),
                event=observation.event,
                event_detail=observation.detail,
                event_verified=observation.verified,
                seconds_since_last_speech=gap,
                **floor_fields,
            )
            # evaluate(), not observe(): the occurrence was already noted when
            # this was first deferred, and noting it twice would advance the
            # error escalation ladder on a single fault.
            fresh = self.judge.evaluate(moment)
            with self._lock:
                self._last_decision = fresh
            if not fresh.should_speak or fresh.line == SAY_NOTHING or fresh.fold_into_reply:
                with self._lock:
                    self._suppressed += 1
                continue
            if fresh.defers:
                keep.append((observation, decision, queued_at))
                continue
            # It spoke, so the history must reflect that it spoke.
            self.judge.history.record(fresh, moment)
            out.append(
                Utterance(
                    id=f"spoke_{int(now * 1000)}_{observation.source}_deferred",
                    text=fresh.line,
                    reason=fresh.reason,
                    rule=fresh.rule,
                    urgency=fresh.urgency.value,
                    confidence=fresh.confidence,
                    source=observation.source,
                    event=observation.event.value,
                    detail=observation.detail,
                    at=now,
                    meta={**dict(observation.meta), "deferred_for": format_duration(age)},
                )
            )
        self._deferred.clear()
        self._deferred.extend(keep)
        return out

    # -- delivery -----------------------------------------------------------
    def tick(self, now: Optional[float] = None, *, wall: Optional[dtime] = None) -> list[Utterance]:
        """:meth:`consider`, then hand each survivor to the emitter."""
        utterances = self.consider(now, wall=wall)
        for utterance in utterances:
            self._deliver(utterance)
        return utterances

    def _deliver(self, utterance: Utterance) -> bool:
        """Publish one utterance. ``False`` when there was nowhere to send it.

        The room hearing about its own last utterance is what makes
        ``seconds_since_last_speech`` correct on the next tick, so it is
        recorded here whether or not a bus was attached. A loop running in
        tests with no emitter still keeps honest timing rather than a gap that
        grows forever.
        """
        if self._emit is not None:
            try:
                self._emit(utterance)
            except Exception as exc:  # noqa: BLE001 - delivery is best effort
                logger.error(f"[Proactivity] delivery failed: {type(exc).__name__}: {exc}")
                return False
        self.floor.note_spoke(utterance.at)
        with self._lock:
            self._spoken += 1
            self._last_utterance = utterance
        logger.info(
            f"[Proactivity] spoke via {utterance.rule} ({utterance.urgency}): {utterance.text}"
        )
        return True

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        """Start the background thread. Idempotent."""
        if not self.config.enabled:
            logger.info("[Proactivity] disabled by configuration")
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            thread = threading.Thread(target=self._run, name="hermus-proactivity", daemon=True)
            self._thread = thread
        thread.start()
        logger.info(
            f"[Proactivity] loop started · {len(self.probes)} probe(s) · every {self.config.interval_seconds:.0f}s"
        )
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the thread to finish and wait briefly for it.

        The wait is on a daemon thread that wakes at most once per interval, so
        this is bounded by the sleep, not by anything the loop is doing.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout)))
        with self._lock:
            self._thread = None
        logger.info("[Proactivity] loop stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - the loop outlives its bugs
                logger.error(f"[Proactivity] tick failed: {type(exc).__name__}: {exc}")
            # Event.wait, not sleep: stop() must not have to wait out the
            # remainder of a 20 second interval to take effect at shutdown.
            self._stop.wait(max(1.0, float(self.config.interval_seconds)))

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # -- observability ------------------------------------------------------
    def status(self, now: Optional[float] = None) -> dict[str, Any]:
        """Everything needed to answer "why hasn't it spoken?".

        Including the last decision's reason, which is the only honest answer
        to that question: an unexplained silence and a crashed loop look
        identical from the outside.
        """
        now = time.monotonic() if now is None else float(now)
        last = self.floor.snapshot(now)
        silence = LongSilence(
            seconds=last["seconds_since_last_speech"] or 0.0,
            known=last["seconds_since_last_speech"] is not None,
        )
        with self._lock:
            decision = self._last_decision
            return {
                "enabled": self.config.enabled,
                "running": self.running,
                "interval_seconds": self.config.interval_seconds,
                "quiet_hours": self.config.quiet_hours,
                "in_quiet_hours": in_quiet_hours(datetime.now().time(), self._quiet_window),
                "probes": [getattr(p, "name", type(p).__name__) for p in self.probes],
                "ticks": self._ticks,
                "observations": self._observations,
                "spoken": self._spoken,
                "suppressed": self._suppressed,
                "deferred_pending": len(self._deferred),
                "deferred_dropped": self._deferred_dropped,
                "silence": {"known": silence.known, "seconds": silence.seconds, "text": silence.render()},
                "floor": last,
                "last_decision": None
                if decision is None
                else {
                    "rule": decision.rule,
                    "should_speak": decision.should_speak,
                    "urgency": decision.urgency.value,
                    "reason": decision.reason,
                    "line": decision.line,
                },
                "last_utterance": None if self._last_utterance is None else self._last_utterance.to_dict(),
            }


# --------------------------------------------------------------------------
# The process-wide loop
# --------------------------------------------------------------------------

_loop: Optional[ProactivityLoop] = None
_loop_lock = threading.Lock()
_floor = FloorState()


def get_floor() -> FloorState:
    """The shared floor, so the gateway and the loop see the same room."""
    return _floor


def get_proactivity() -> Optional[ProactivityLoop]:
    with _loop_lock:
        return _loop


def build_proactivity(
    *,
    emit: Callable[[Utterance], Any] | None = None,
    config: ProactivityConfig | None = None,
    probes: Iterable[Any] | None = None,
) -> ProactivityLoop:
    """Create the loop with the real emitter wired.

    The emitter is the *only* place that knows about the gateway. Keeping the
    import here rather than at module scope is what lets this module be
    imported — and tested — without a running FastAPI app.
    """
    from gateway.realtime import speak_to_room

    return ProactivityLoop(
        probes=probes,
        config=config,
        floor=_floor,
        emit=emit or speak_to_room,
    )


def start_proactivity(*, config: ProactivityConfig | None = None) -> Optional[ProactivityLoop]:
    """Build and start the process loop. Idempotent; ``None`` if disabled."""
    global _loop
    with _loop_lock:
        if _loop is not None and _loop.running:
            return _loop
    try:
        loop = build_proactivity(config=config)
    except Exception as exc:  # noqa: BLE001 - a bad emitter must not stop boot
        logger.error(f"[Proactivity] could not build loop: {type(exc).__name__}: {exc}")
        return None
    with _loop_lock:
        _loop = loop
    if not loop.start():
        with _loop_lock:
            _loop = None
        return None
    return loop


def stop_proactivity(timeout: float = 5.0) -> None:
    global _loop
    with _loop_lock:
        loop = _loop
        _loop = None
    if loop is not None:
        loop.stop(timeout=timeout)
