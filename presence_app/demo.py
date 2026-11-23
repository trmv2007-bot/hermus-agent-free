"""The documented demo: make the character speak and point at three rects.

``hermus presence demo`` runs this. It talks to the overlay through the same
public API a person would use — nothing here reaches into the bridge — so if the
demo works, the API works.

The three rects are real screen regions measured on the running machine (the
taskbar, the notification area, and the middle of the primary display). If the
screen cannot be measured, the demo says so and stops instead of guessing.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from .client import PresenceClient, PresenceUnavailableError

#: How long each stop of the tour stays on screen (the point TTL, in ms).
STEP_TTL_MS = 3_600
PAUSE_SECONDS = 4.0


def screen_size() -> tuple[int, int] | None:
    """Primary display size in physical pixels, or None when unmeasurable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001 - a demo must not fail on a probe
        return None


def tour_rects(width: int, height: int) -> list[dict[str, Any]]:
    """Three honest screen regions, in physical pixels, for a primary display."""
    taskbar_h = max(40, min(64, round(height * 0.045)))
    return [
        {
            "label": "Taskbar",
            "x": 0,
            "y": height - taskbar_h,
            "w": max(260, round(width * 0.28)),
            "h": taskbar_h,
            "say": "This is the taskbar. I can start anything from here.",
        },
        {
            "label": "Notification area",
            "x": width - max(300, round(width * 0.16)),
            "y": height - taskbar_h,
            "w": max(300, round(width * 0.16)),
            "h": taskbar_h,
            "say": "And this is where the system tells you things.",
        },
        {
            "label": "Your working area",
            "x": round(width / 2 - 210),
            "y": round(height / 2 - 70),
            "w": 420,
            "h": 140,
            "say": "When I click or type, I point at the exact spot first.",
        },
    ]


def run_demo(client: PresenceClient, *, steps: bool = True, pause: float = PAUSE_SECONDS) -> list[dict[str, Any]]:
    """Point at three rects, saying one line at each. Returns what was sent."""
    size = screen_size()
    if size is None:
        raise PresenceUnavailableError("cannot measure this display, so there is nothing honest to point at")
    width, height = size
    sent: list[dict[str, Any]] = []
    client.set_state("listening", rationale="demo")
    client.say("Watching this screen with you.", ttl_ms=2_600)
    time.sleep(1.2)
    for step in tour_rects(width, height):
        client.set_state("acting", ttl_ms=STEP_TTL_MS)
        client.point(step["x"], step["y"], step["w"], step["h"], label=step["label"], ttl_ms=STEP_TTL_MS)
        client.say(step["say"], ttl_ms=STEP_TTL_MS)
        sent.append({key: step[key] for key in ("label", "x", "y", "w", "h")})
        if steps:
            time.sleep(pause)
    client.retract()
    client.say("That is the whole gesture: point first, then act.", ttl_ms=3_000)
    client.set_state("idle", rationale="demo finished")
    return sent


__all__ = ["PAUSE_SECONDS", "STEP_TTL_MS", "run_demo", "screen_size", "tour_rects"]
