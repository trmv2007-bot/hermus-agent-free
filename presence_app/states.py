"""The character's state vocabulary and the rules that keep it coherent.

Seven states, one of which the renderer is always in. The renderer cross-fades
between them over ~700ms (and instantly under ``prefers-reduced-motion``), so a
transition is never visible as a cut.

Two behaviours live here rather than in the renderer because both are policy,
not paint:

* **Waking.** ``sleeping`` is a real sleep: pointing at something or saying
  something wakes the character first, then performs the action. A sleeping
  avatar silently doing work would be a lie about what the person's screen is
  showing.
* **Dwell.** ``acting`` ↔ ``thinking`` flip-flopping on every tool step makes a
  strobe. ``MIN_DWELL_MS`` collapses transitions that arrive faster than the eye
  can follow into the state already on screen.
"""

from __future__ import annotations

STATES: tuple[str, ...] = ("idle", "listening", "thinking", "acting", "speaking", "error", "sleeping")

#: The state a request implies while the character is asleep.
WAKE_STATE: dict[str, str] = {"point": "acting", "say": "speaking", "listen": "listening"}

#: Transitions the renderer is allowed to compress into one frame. Everything
#: else gets the full cross-fade. ``error`` is deliberately not in any list: it
#: must be visible long enough to be read, so leaving it always takes the slow
#: path (see :func:`transition_duration_ms`).
QUICK_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("idle", "listening"),
        ("listening", "idle"),
        ("thinking", "acting"),
        ("acting", "speaking"),
        ("speaking", "idle"),
        ("speaking", "listening"),
        ("sleeping", "idle"),
    }
)

#: Below this, a state change is folded into the current one instead of painted.
MIN_DWELL_MS = 180

_CROSS_FADE_MS = 700
_QUICK_FADE_MS = 380


def normalize_state(value: object) -> str:
    """Return a canonical state name or raise ``ValueError`` for anything else.

    Accepts only the exact vocabulary (case/whitespace tolerant) so a typo in a
    caller surfaces as a rejected payload instead of a character frozen in an
    unknown mood.
    """
    text = str(value or "").strip().lower()
    if text not in STATES:
        raise ValueError(f"unknown presence state {value!r}; expected one of {', '.join(STATES)}")
    return text


def transition_duration_ms(previous: str, current: str) -> int:
    """How long the renderer should take to cross-fade ``previous`` → ``current``."""
    if previous == current:
        return 0
    if previous == "error":
        return _CROSS_FADE_MS + 250
    return _QUICK_FADE_MS if (previous, current) in QUICK_TRANSITIONS else _CROSS_FADE_MS


def resolve_transition(previous: str, current: str, dwell_ms: float | None) -> tuple[str, str]:
    """Decide which state actually gets painted for a requested ``current``.

    Returns ``(state_to_paint, reason)``. ``dwell_ms`` is how long the previous
    state has been on screen; ``None`` means "unknown, do not filter".
    """
    if previous == current:
        return current, "unchanged"
    if previous == "acting" and current == "thinking" and dwell_ms is not None and dwell_ms < MIN_DWELL_MS:
        return previous, "dwell"
    return current, "ok"


def is_awake(state: str) -> bool:
    return state != "sleeping"


__all__ = [
    "MIN_DWELL_MS",
    "QUICK_TRANSITIONS",
    "STATES",
    "WAKE_STATE",
    "is_awake",
    "normalize_state",
    "resolve_transition",
    "transition_duration_ms",
]
