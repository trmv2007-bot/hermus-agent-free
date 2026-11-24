"""Free endpoints answer for nothing, and say so at two requests a minute.

A keyless tier is the rung that lets a fresh install answer at all, so it must
not be spent carelessly and must not be abandoned at the first refusal. Two
failures follow from ignoring its ceiling: a burst burns the allowance and every
later turn is an HTTP 429 shown to the person as an error, and a limit that
looks like a dead end sends the product to the stub while a second endpoint sits
there answering.

So a documented ceiling is a *budget*, held per provider and per model (the
endpoint counts them separately), and a request that would exceed it is queued
behind the budget with one plain sentence — never converted into a failure:

    the free endpoint allows 2 requests a minute — this will go in 12s

The buckets are a token bucket, not a fixed window: a burst of the allowance
goes straight through, and the next request waits only for the token it needs.
Waiting is bounded by :data:`MAX_WAIT_ONE_WAIT`; a request that would wait longer
gives up and the caller moves to the next rung, because a person should not sit
in front of a spinner for a minute when a second free endpoint is idle.

Nothing here invents a number. Only an endpoint with a documented, published
ceiling appears in :data:`BUDGETS`, and an unlisted provider is unmetered.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import model_catalog as catalog

#: the longest a single request will sit in the queue before giving up on this rung
MAX_WAIT_ONE_WAIT = 30.0


@dataclass(frozen=True)
class Budget:
    """A documented anonymous allowance: requests a minute, per provider+model."""

    provider: str
    per_minute: int
    sentence: str


#: published ceilings only — the number and the wording both come from the tier
BUDGETS: dict[str, Budget] = {
    "ovh": Budget(
        provider="ovh",
        per_minute=2,
        sentence="the free endpoint allows 2 requests a minute",
    ),
}


def budget_for(provider: str) -> Budget | None:
    return BUDGETS.get(str(provider or "").lower())


class TokenBucket:
    """``per_minute`` tokens a minute, refilled continuously, reserved by callers.

    A reservation that finds the bucket empty is still honoured — it goes into
    debt by one token and is told how long the wait is. Debt is what makes the
    queue ordered: every caller behind the first waits one refill interval longer
    than the one before it, instead of all of them waking at the same instant.
    """

    def __init__(self, per_minute: int, *, clock=time.monotonic):
        if per_minute <= 0:
            raise ValueError("a bucket needs at least one request a minute")
        self.per_minute = int(per_minute)
        self.capacity = float(per_minute)
        self.rate = self.capacity / 60.0
        self._tokens = self.capacity
        self._stamp: float | None = None
        self._clock = clock
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        if self._stamp is None:
            self._stamp = now
            return
        elapsed = max(0.0, now - self._stamp)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._stamp = now

    def reserve(self, now: float | None = None) -> float:
        """Take one token. Returns 0.0, or the seconds until this request may go."""
        with self._lock:
            moment = self._clock() if now is None else float(now)
            self._refill(moment)
            self._tokens -= 1.0
            if self._tokens >= 0.0:
                return 0.0
            return -self._tokens / self.rate

    def reset(self) -> None:
        with self._lock:
            self._tokens = self.capacity
            self._stamp = None


@dataclass(frozen=True)
class Slot:
    """One reservation: when it may go, and the sentence that says why it waits."""

    provider: str
    model: str
    seconds: float = 0.0
    sentence: str = ""

    @property
    def waiting(self) -> bool:
        return self.seconds > 0.0

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.model}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "ref": self.ref,
            "seconds": round(self.seconds, 1),
            "waiting": self.waiting,
            "sentence": self.sentence,
        }


class Board:
    """Every budget in play, one bucket per provider and model."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._lock = threading.Lock()

    def bucket(self, provider: str, model: str) -> TokenBucket | None:
        budget = budget_for(provider)
        if budget is None:
            return None
        with self._lock:
            key = (budget.provider, str(model or "").lower())
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(budget.per_minute, clock=self._clock)
                self._buckets[key] = bucket
            return bucket

    def reserve(self, provider: str, model: str, *, now: float | None = None) -> Slot | None:
        """Reserve one request, or ``None`` when this provider publishes no limit."""
        bucket = self.bucket(provider, model)
        if bucket is None:
            return None
        seconds = bucket.reserve(now)
        budget = budget_for(provider)
        sentence = ""
        if seconds > 0 and budget is not None:
            sentence = f"{budget.sentence} — this will go in {seconds:.0f}s"
        return Slot(provider=str(provider).lower(), model=str(model), seconds=seconds, sentence=sentence)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


#: the process-wide board. Tests build their own with a fake clock.
board = Board()

#: the last few sentences a wait produced, newest last. The first answer stream
#: and the run log read this so a queue is visible rather than a silent pause.
_notices: list[str] = []
_notice_lock = threading.Lock()


def note(sentence: str) -> None:
    text = str(sentence or "").strip()
    if not text:
        return
    with _notice_lock:
        _notices.append(text)
        del _notices[:-8]


def recent_notices(limit: int = 4) -> list[str]:
    with _notice_lock:
        return _notices[-max(1, int(limit)) :]


def clear_notices() -> None:
    with _notice_lock:
        _notices.clear()


@dataclass
class Waited:
    """What actually happened to one queued request."""

    seconds: float = 0.0
    sentence: str = ""
    gave_up: bool = False
    providers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seconds": round(self.seconds, 1),
            "sentence": self.sentence,
            "gave_up": self.gave_up,
        }


def wait_for_turn(slot: Slot | None, *, sleeper=time.sleep, max_wait: float = MAX_WAIT_ONE_WAIT) -> Waited:
    """Sit out the reservation, or refuse to when the wait is longer than a turn.

    ``gave_up`` is not an error: it means this rung is too busy right now, which
    is the caller's cue to try the next one instead of blaming the person's key
    or the model.
    """
    if slot is None or not slot.waiting:
        return Waited()
    note(slot.sentence)
    if slot.seconds > max_wait:
        return Waited(seconds=0.0, sentence=slot.sentence, gave_up=True)
    sleeper(slot.seconds)
    return Waited(seconds=slot.seconds, sentence=slot.sentence)


def next_rung(provider: str) -> str | None:
    """The next keyless provider to try after this one, or ``None`` at the end.

    Kept here rather than in the transport so the ladder order lives in the
    catalog: the second free endpoint exists precisely so a rate limit on the
    first is not the end of the answer.
    """
    provider = str(provider or "").lower()
    if provider not in catalog.KEYLESS_PROVIDERS:
        return None
    position = catalog.KEYLESS_PROVIDERS.index(provider)
    rest = catalog.KEYLESS_PROVIDERS[position + 1 :]
    return rest[0] if rest else None


# -- what the last few requests learned about each rung's health -------------

#: how long a hard failure is believed before the rung is offered again. Long
#: enough that a retired model name does not cost a request per page load, short
#: enough that an outage does not hide a working endpoint for an hour.
DOWN_TTL = 900.0

#: the longest a refusal is believed. An endpoint that says "come back in 37
#: days" is honest, but a month of silence is worse than one wasted request a
#: day, so the window is capped there and re-measured.
MAX_DOWN = 86_400.0

_down: dict[str, tuple[float, str]] = {}
_down_lock = threading.Lock()


def mark_down(provider: str, detail: str, *, seconds: float | None = None) -> None:
    """Record that this rung refused a request, and for how long to believe it.

    An endpoint that says ``retry-after: 3242329`` is not rate-limiting a minute,
    it is telling the truth about an allowance that is spent, so the wait it
    states is the wait that is honoured rather than the default suspicion window.
    """
    with _down_lock:
        _down[str(provider or "").lower()] = (time.monotonic(), str(detail or "")[:200], float(seconds or DOWN_TTL))


def mark_up(provider: str) -> None:
    with _down_lock:
        _down.pop(str(provider or "").lower(), None)


def down_reason(provider: str) -> str:
    """Why this rung is being skipped right now, or '' when it is not."""
    with _down_lock:
        row = _down.get(str(provider or "").lower())
    if row is None:
        return ""
    at, detail, window = row
    if time.monotonic() - at > window:
        with _down_lock:
            _down.pop(str(provider or "").lower(), None)
        return ""
    return detail or "it refused the last request"


def describe_wait(seconds: float) -> str:
    """A duration in the words a person reads, never a raw count of seconds."""
    seconds = max(0.0, float(seconds))
    if seconds >= 86_400:
        return f"{seconds / 86_400:.0f} days"
    if seconds >= 3_600:
        return f"{seconds / 3_600:.0f} hours"
    if seconds >= 60:
        return f"{seconds / 60:.0f} minutes"
    return f"{max(1, round(seconds))}s"


def reset_health() -> None:
    with _down_lock:
        _down.clear()
