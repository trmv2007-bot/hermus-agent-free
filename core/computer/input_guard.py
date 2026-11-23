"""Real input is off unless this process says it is the interactive product.

``pyautogui`` drives the *host*: a keystroke or a click goes to whatever window
has focus, and nothing in the call can know which window that is. A test, a CI
job, or a library import has no business doing that — RIS-12 was exactly this:
a unit test that typed "hello world" into whichever application happened to be
focused on the developer's desktop.

So the guard is default-deny. A real backend may only exist inside a process
that called :func:`declare_interactive` — the gateway that serves the desktop
app, or the agent CLI. Everything else (tests, CI, imports, notebooks, scripts)
gets a dry-run backend whose ``fallback_reason`` is :func:`disabled_reason`, and
constructing a real backend there, or calling one directly, raises
:class:`RealInputDisabled` instead of silently doing nothing.

Two further locks sit on top of the declaration so a declaration can never be
accidental:

* ``HERMUS_REAL_INPUT=off`` in the environment refuses real input outright.
* a process with a running pytest in it never counts as interactive, so an
  in-process test can call the product entry point without gaining the mouse.
"""

from __future__ import annotations

import os
import sys
import threading

ENV_FLAG = "HERMUS_REAL_INPUT"
_OFF_VALUES = frozenset({"0", "off", "false", "no", "disabled"})
#: The one value that lets a deliberate, hand-run host test opt in.
_ON_VALUES = frozenset({"1", "on", "true", "yes"})

_lock = threading.RLock()
_declared_by: str | None = None


class RealInputDisabledError(RuntimeError):
    """Raised when real pointer/keyboard control is attempted without consent."""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "real_input_disabled"


def _env_forbids() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in _OFF_VALUES


def _test_runner_present() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def declare_interactive(source: str = "hermus", *, allow_in_tests: bool = False) -> bool:
    """Declare this process the interactive product. Returns whether it took.

    ``allow_in_tests`` exists for the one deliberate, hand-run host test
    (``HERMUS_REAL_INPUT=on pytest -k host_e2e``); it still needs the environment
    flag to say "on" out loud, so no test can gain the mouse by accident.
    """
    global _declared_by
    if _env_forbids():
        return False
    if _test_runner_present():
        if not allow_in_tests:
            return False
        if str(os.environ.get(ENV_FLAG, "")).strip().lower() not in _ON_VALUES:
            return False
    with _lock:
        _declared_by = str(source or "hermus")
    return True


def release_interactive() -> None:
    """Return the process to the safe default (used by tests and shutdown)."""
    global _declared_by
    with _lock:
        _declared_by = None


def real_input_enabled() -> bool:
    """True only when this process declared itself the interactive product."""
    if _env_forbids():
        return False
    with _lock:
        return _declared_by is not None


def disabled_reason() -> str:
    """The honest error a refused real-input call carries."""
    if _env_forbids():
        return f"real input disabled: {ENV_FLAG} is set to off"
    if _test_runner_present():
        return "real input disabled: a test runner never controls the host"
    return "real input disabled: this process has not declared itself the interactive product"


def require_real_input(feature: str = "computer control") -> None:
    """Raise :class:`RealInputDisabledError` unless real input is allowed here."""
    if not real_input_enabled():
        raise RealInputDisabledError(f"{disabled_reason()} ({feature})")


def guard_state() -> dict[str, object]:
    """What the guard currently allows, for capability reports and diagnostics."""
    return {
        "real_input_enabled": real_input_enabled(),
        "declared_by": _declared_by,
        "reason": "" if real_input_enabled() else disabled_reason(),
        "env_flag": ENV_FLAG,
    }


__all__ = [
    "ENV_FLAG",
    "RealInputDisabledError",
    "declare_interactive",
    "disabled_reason",
    "guard_state",
    "real_input_enabled",
    "release_interactive",
    "require_real_input",
]
