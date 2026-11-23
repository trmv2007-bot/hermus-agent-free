"""The presence bridge: one thread-safe state store the whole app reads from.

Writers (the computer-use controller, the HTTP API, the CLI demo) mutate state;
readers (the SSE/WebSocket stream, the shell's renderer) read snapshots. Three
rules make it safe to call from anywhere in HERMUS:

* **Every mutation is total.** It validates, clamps, expires and returns a new
  snapshot — it never raises out to a caller, so a bad payload cannot break an
  agent run or the gateway.
* **Nothing blocks.** There is no network hop between the controller and this
  object, and subscribers poll a version counter instead of being pushed to, so
  a caller on the agent's worker thread can never be slowed by a slow reader.
* **Time is injectable.** ``clock`` defaults to wall time; tests pass a fake so
  TTL expiry is deterministic rather than sleep-dependent.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from .geometry import (
    MAX_LABEL_CHARS,
    MAX_SAY_CHARS,
    PresencePayloadError,
    normalize_space,
    parse_displays,
    parse_rect,
    virtual_bounds,
)
from .states import STATES, normalize_state, resolve_transition, transition_duration_ms

DEFAULT_POINT_TTL_MS = 4_200
DEFAULT_SAY_TTL_MS = 4_500
DEFAULT_STATE_TTL_MS = 0  # 0 = the state stands until something replaces it
MIN_TTL_MS = 300
MAX_TTL_MS = 60_000
#: A state set with a TTL decays back to this one (``error`` reads, then clears).
DECAY_STATE = "idle"
#: Kept for the control room and for tests; the stream only needs the latest.
HISTORY = 24


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class PresenceBridge:
    """Thread-safe floating-presence state with TTLs and versioned snapshots."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._state = "idle"
        self._state_since = self._clock()
        self._state_ttl_ms = 0
        self._rationale = ""
        self._visible = True
        self._point: dict[str, Any] | None = None
        self._say: dict[str, Any] | None = None
        self._input_idle_ms: int | None = None
        self._displays: list[dict[str, Any]] = []
        self._canvas: dict[str, float] = {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY)
        self._version = 0
        self._clients = 0

    # ------------------------------------------------------------- lifecycle
    def _touch(self) -> None:
        self._version += 1

    def _record(self, state: str, reason: str) -> None:
        self._history.append({"state": state, "at": round(self._clock(), 3), "reason": reason})

    def _expire(self, now: float) -> bool:
        """Drop expired point/say/state in place. Returns True if anything changed."""
        changed = False
        if self._point and now >= float(self._point.get("expires_at", 0)):
            self._point = None
            changed = True
        if self._say and now >= float(self._say.get("expires_at", 0)):
            self._say = None
            changed = True
        if self._state_ttl_ms and self._state != DECAY_STATE and now - self._state_since >= self._state_ttl_ms / 1000.0:
            self._state = DECAY_STATE
            self._state_ttl_ms = 0
            self._state_since = now
            self._rationale = "ttl expired"
            self._record(self._state, "ttl")
            changed = True
        return changed

    # ------------------------------------------------------------------ reads
    def snapshot(self) -> dict[str, Any]:
        """Current state as a JSON-safe dict; expires stale pieces first."""
        with self._lock:
            if self._expire(self._clock()):
                self._touch()
            now = self._clock()
            return {
                "ok": True,
                "version": self._version,
                "state": self._state,
                "previous": self._history[-2]["state"] if len(self._history) > 1 else None,
                "transition_ms": transition_duration_ms(
                    self._history[-2]["state"] if len(self._history) > 1 else self._state, self._state
                ),
                "state_age_ms": int((now - self._state_since) * 1000),
                "state_ttl_ms": self._state_ttl_ms,
                "rationale": self._rationale,
                "visible": self._visible,
                "point": dict(self._point) if self._point else None,
                "say": dict(self._say) if self._say else None,
                "input_idle_ms": self._input_idle_ms,
                "displays": [dict(d) for d in self._displays],
                "canvas": dict(self._canvas),
                "clients": self._clients,
                "states": list(STATES),
                "history": list(self._history)[-8:],
            }

    def version(self) -> int:
        with self._lock:
            if self._expire(self._clock()):
                self._touch()
            return self._version

    @property
    def visible(self) -> bool:
        with self._lock:
            return self._visible

    # -------------------------------------------------------------- mutations
    def set_state(self, state: Any, ttl_ms: int | None = None, rationale: str = "", *, reveal: bool = True) -> dict[str, Any]:
        """Move the character to ``state``. Unknown names leave everything as-is."""
        with self._lock:
            try:
                target = normalize_state(state)
            except ValueError:
                return {**self.snapshot(), "ok": False, "error": f"unknown state {state!r}"}
            now = self._clock()
            dwell = (now - self._state_since) * 1000.0 if self._state != target else None
            painted, reason = resolve_transition(self._state, target, dwell)
            requested_ttl = int(ttl_ms or 0)
            ttl = 0 if requested_ttl <= 0 else max(MIN_TTL_MS, min(MAX_TTL_MS, requested_ttl))
            if painted == self._state and reason == "unchanged":
                # Same state: only a longer TTL or a new rationale is meaningful.
                self._state_ttl_ms = ttl or self._state_ttl_ms
                if rationale:
                    self._rationale = rationale[:200]
                if reveal:
                    self._visible = True
                return self.snapshot()
            previous = self._state
            self._state = painted
            self._state_since = now
            self._state_ttl_ms = ttl
            self._rationale = (rationale or "")[:200]
            if reveal:
                self._visible = True
            self._record(painted, reason)
            if previous != painted:
                self._touch()
            return self.snapshot()

    def point(
        self,
        rect: Any,
        label: str = "",
        ttl_ms: int | None = None,
        *,
        space: str = "dip",
        source: str = "api",
    ) -> dict[str, Any]:
        """Point at a screen rect. Raises :class:`PresencePayloadError` on bad input."""
        parsed = parse_rect(rect)
        with self._lock:
            displays = list(self._displays)
        dip, mapped = normalize_space(parsed, space, displays)
        now = self._clock()
        ttl = max(MIN_TTL_MS, min(MAX_TTL_MS, int(ttl_ms or DEFAULT_POINT_TTL_MS)))
        entry = {
            "id": uuid.uuid4().hex[:12],
            "x": round(dip["x"], 2),
            "y": round(dip["y"], 2),
            "w": round(dip["w"], 2),
            "h": round(dip["h"], 2),
            "label": str(label or "")[:MAX_LABEL_CHARS],
            "space": "dip",
            "mapped": bool(mapped),
            "source": str(source or "api")[:24],
            "created_at": round(now, 3),
            "expires_at": round(now + ttl / 1000.0, 3),
            "ttl_ms": ttl,
        }
        with self._lock:
            self._point = entry
            self._visible = True
            if self._state not in ("acting", "error"):
                # Pointing is acting: the character's attention is on the target
                # while the arrow is out, so the mood and the arrow agree.
                self._state_since = now
                self._state = "acting"
                self._record("acting", "point")
            self._touch()
            return self.snapshot()

    def say(self, text: Any, ttl_ms: int | None = None, *, source: str = "api") -> dict[str, Any]:
        """Show a subtitle (max two lines). Empty text clears the bubble."""
        if not isinstance(text, str):
            raise PresencePayloadError("text must be a string")
        clean = " ".join(text.split())[:MAX_SAY_CHARS]
        with self._lock:
            if not clean:
                self._say = None
                self._touch()
                return self.snapshot()
            now = self._clock()
            ttl = max(MIN_TTL_MS, min(MAX_TTL_MS, int(ttl_ms or DEFAULT_SAY_TTL_MS)))
            self._say = {
                "id": uuid.uuid4().hex[:12],
                "text": clean,
                "source": str(source or "api")[:24],
                "created_at": round(now, 3),
                "expires_at": round(now + ttl / 1000.0, 3),
                "ttl_ms": ttl,
            }
            self._visible = True
            if self._state == "sleeping":
                self._state_since = now
                self._state = "speaking"
                self._record("speaking", "wake:say")
            self._touch()
            return self.snapshot()

    def retract(self) -> dict[str, Any]:
        """Drop the current point (keeps the character and any subtitle)."""
        with self._lock:
            if self._point is not None:
                self._point = None
                self._touch()
            return self.snapshot()

    def hide(self) -> dict[str, Any]:
        with self._lock:
            self._visible = False
            self._point = None
            self._say = None
            self._state = "sleeping"
            self._state_since = self._clock()
            self._state_ttl_ms = 0
            self._record("sleeping", "hide")
            self._touch()
            return self.snapshot()

    def show(self) -> dict[str, Any]:
        with self._lock:
            self._visible = True
            if self._state == "sleeping":
                self._state = "idle"
                self._state_since = self._clock()
                self._record("idle", "show")
            self._touch()
            return self.snapshot()

    def note_input(self, idle_ms: Any) -> dict[str, Any]:
        """Record how long the person's keyboard has been quiet (from the shell)."""
        try:
            value = int(float(idle_ms))
        except (TypeError, ValueError):
            raise PresencePayloadError("idle_ms must be a number") from None
        with self._lock:
            clamped = max(0, min(86_400_000, value))
            if clamped != self._input_idle_ms:
                self._input_idle_ms = clamped
                self._touch()
            return self.snapshot()

    def set_displays(self, payload: Any) -> dict[str, Any]:
        """Register the shell's monitor layout so physical rects scale correctly."""
        displays = parse_displays(payload)
        with self._lock:
            self._displays = displays
            self._canvas = virtual_bounds(displays, "dip")
            self._touch()
            return self.snapshot()

    def enter(self) -> None:
        with self._lock:
            self._clients += 1

    def leave(self) -> None:
        with self._lock:
            self._clients = max(0, self._clients - 1)


_bridge = PresenceBridge()


def get_presence_bridge() -> PresenceBridge:
    """Process-wide bridge (the gateway and the controller share one instance)."""
    return _bridge


def reset_presence_bridge(clock: Callable[[], float] | None = None) -> PresenceBridge:
    """Replace the singleton — for tests, and for a clean demo run."""
    global _bridge
    _bridge = PresenceBridge(clock=clock)
    return _bridge


def iter_states() -> Iterable[str]:
    return STATES


__all__ = [
    "DECAY_STATE",
    "DEFAULT_POINT_TTL_MS",
    "DEFAULT_SAY_TTL_MS",
    "MAX_LABEL_CHARS",
    "MAX_SAY_CHARS",
    "PresenceBridge",
    "PresencePayloadError",
    "get_presence_bridge",
    "reset_presence_bridge",
]
