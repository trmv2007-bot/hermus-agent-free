"""The computer-use seam: make the floating character point before HERMUS acts.

Every desktop action in HERMUS flows through
:meth:`core.computer.controller.ComputerActionController._perform`, which is the
one place that knows an action is *about* to land and where. This module turns
that moment into a presence point, and holds three hard rules:

* **Never raise.** The controller calls :func:`announce` on the agent's worker
  thread; an exception here would fail a real action for a cosmetic reason, so
  everything is caught and logged once at debug level.
* **Never block by default.** The call is a dict write into the in-process
  bridge: tens of microseconds headless. A *lead* time (``HERMUS_PRESENCE_LEAD_MS``,
  default 0) is available for anyone who wants the arrow to land before the
  click — that is an explicit, opt-in latency cost, never a surprise one.
* **Never lie about the target.** A click has a coordinate, so the point is the
  cursor. Typing has no coordinate, so the point is the focused window — and if
  that cannot be measured, nothing is pointed at, because a guessed rectangle is
  worse than no rectangle.

Set ``HERMUS_PRESENCE_POINTER=0`` to unhook the controller entirely.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from core.log import get_logger

logger = get_logger(__name__)

CLICK_ACTIONS = frozenset({"click", "double_click", "right_click", "move_mouse"})
TEXT_ACTIONS = frozenset({"type_text", "press_key", "hotkey"})

_VERBS = {
    "click": "Click",
    "double_click": "Double-click",
    "right_click": "Right-click",
    "move_mouse": "Move here",
    "scroll": "Scroll here",
    "type_text": "Type here",
    "press_key": "Keyboard",
    "hotkey": "Keyboard",
}

#: The ring around a click point, in screen pixels (physical on Windows).
CLICK_RECT_W = 96.0
CLICK_RECT_H = 56.0
#: A point outlives a normal action by a couple of seconds so it can be seen.
POINT_TTL_MS = 4_000
#: Below this, the person is typing: a subtitle must wait for them to stop.
TYPING_HOLD_MS = 700


def enabled() -> bool:
    """Global kill switch, honoured on every call (env may change at runtime)."""
    return str(os.getenv("HERMUS_PRESENCE_POINTER", "1")).strip().lower() not in ("0", "false", "off", "no")


def lead_ms() -> int:
    """Optional wait before a pointed action, so the arrow beats the click."""
    try:
        value = int(float(os.getenv("HERMUS_PRESENCE_LEAD_MS", "0")))
    except (TypeError, ValueError):
        return 0
    return max(0, min(2_000, value))


def _label_for(action: str, args: dict[str, Any]) -> str:
    verb = _VERBS.get(action, action.replace("_", " ").title())
    if action == "click" and isinstance(args.get("button"), str) and args["button"] != "left":
        return f"{args['button'].title()}-click"
    return verb


def focused_window_rect() -> dict[str, float] | None:
    """Bounding box of the foreground window, in screen pixels, on Windows.

    Returns None anywhere else, and on any failure — a missing rectangle is
    reported as "no target to point at", not as a zero-size rect.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width = float(rect.right - rect.left)
        height = float(rect.bottom - rect.top)
        if width < 8 or height < 8:
            return None
        return {"x": float(rect.left), "y": float(rect.top), "w": width, "h": height}
    except Exception:  # noqa: BLE001 - a cosmetic probe must never break control
        return None


def target_for(action: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Pure mapping from a controller action to a presence point (or None)."""
    if not isinstance(args, dict):
        return None
    if action in CLICK_ACTIONS or (action == "scroll" and args.get("x") is not None and args.get("y") is not None):
        if args.get("x") is None or args.get("y") is None:
            return None
        label = _label_for(action, args)
        try:
            x, y = float(args["x"]), float(args["y"])
        except (TypeError, ValueError):
            return None
        return {
            "rect": {"x": x - CLICK_RECT_W / 2, "y": y - CLICK_RECT_H / 2, "w": CLICK_RECT_W, "h": CLICK_RECT_H},
            "label": label,
            "action": action,
            "point": True,
        }
    if action in TEXT_ACTIONS:
        rect = focused_window_rect()
        if rect is None:
            return None
        rect = {**rect, "w": min(rect["w"], 520.0), "h": min(rect["h"], 120.0)}
        return {"rect": rect, "label": _label_for(action, args), "action": action, "point": False}
    return None


def announce(action: str, args: dict[str, Any] | None = None, *, source: str = "computer") -> dict[str, Any] | None:
    """Emit a presence point for an action that is about to land.

    Returns the point entry when something was pointed at (useful for tests and
    for the demo), else None. Never raises, never blocks unless a lead time is
    explicitly configured.
    """
    if not enabled():
        return None
    try:
        target = target_for(str(action), args or {})
        if target is None or not target.get("rect"):
            return None
        from presence_app.bridge import get_presence_bridge
        from presence_app.geometry import PresencePayloadError

        bridge = get_presence_bridge()
        try:
            snapshot = bridge.point(target["rect"], label=target["label"], ttl_ms=POINT_TTL_MS, space="physical", source=source)
        except PresencePayloadError as exc:
            logger.debug(f"[presence] dropped point for {action}: {exc}")
            return None
        bridge.set_state("acting", ttl_ms=POINT_TTL_MS, rationale=target["action"], reveal=True)
        lead = lead_ms()
        if lead:
            time.sleep(lead / 1000.0)
        return snapshot.get("point")
    except Exception as exc:  # noqa: BLE001 - telemetry may never break an action
        logger.debug(f"[presence] announce failed for {action}: {exc}")
        return None


def announce_result(action: str, result: dict[str, Any] | None = None, source: str = "computer") -> None:
    """Tell the character how the action ended (error state or back to idle)."""
    if not enabled():
        return
    try:
        from presence_app.bridge import get_presence_bridge

        bridge = get_presence_bridge()
        if isinstance(result, dict) and result.get("ok") is False:
            bridge.set_state("error", ttl_ms=1_600, rationale=str(result.get("detail") or result.get("error") or action)[:80])
        elif bridge.snapshot().get("state") == "acting":
            bridge.set_state("thinking", ttl_ms=1_200, rationale="action complete")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[presence] result announce failed: {exc}")


def is_typing(idle_ms: int | None) -> bool:
    """True while the person's keyboard has been used within the hold window."""
    return idle_ms is not None and idle_ms < TYPING_HOLD_MS


__all__ = [
    "CLICK_ACTIONS",
    "CLICK_RECT_H",
    "CLICK_RECT_W",
    "POINT_TTL_MS",
    "TEXT_ACTIONS",
    "TYPING_HOLD_MS",
    "announce",
    "announce_result",
    "enabled",
    "focused_window_rect",
    "is_typing",
    "lead_ms",
    "target_for",
]
