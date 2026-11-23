"""Turn a desktop snapshot into a short prompt block — or into nothing at all.

The block is deliberately small: a half-dozen lines, capped at 1200 characters,
with app names de-duplicated and no title repeated from the focused line. An
unchanged desktop renders as an empty string through :class:`DesktopContextRenderer`,
so a chat turn about something else pays no tokens for state that did not move.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

MAX_BLOCK_CHARS = 1200
WINDOW_NAMES = 6


def _power_line(power: dict[str, Any]) -> str | None:
    if not power.get("ok"):
        return None
    value = power.get("value") or {}
    percent = value.get("percent")
    seconds = value.get("seconds_left")
    if value.get("on_ac"):
        return f"Power: on AC{f', {percent}%' if percent is not None else ' (no battery reported)'}"
    detail = "on battery"
    if percent is not None:
        detail += f", {percent}%"
    if isinstance(seconds, (int, float)) and seconds > 0:
        detail += f", {int(seconds) // 3600}h {int(seconds) % 3600 // 60:02d}m left"
    return f"Power: {detail}"


def _idle_line(idle: dict[str, Any]) -> str | None:
    if not idle.get("ok"):
        return None
    seconds = float((idle.get("value") or {}).get("seconds") or 0.0)
    if seconds < 90:
        return f"Idle: {int(seconds)}s"
    return f"Idle: {int(seconds) // 60}m"


def _display_line(displays: dict[str, Any]) -> str | None:
    if not displays.get("ok"):
        return None
    parts = []
    for monitor in displays.get("value") or []:
        label = f"{monitor.get('width')}x{monitor.get('height')}"
        scale = monitor.get("scale")
        if isinstance(scale, (int, float)) and scale != 1.0:
            label += f"@{scale:g}x"
        if monitor.get("primary"):
            label += " (primary)"
        parts.append(label)
    return f"Displays: {', '.join(parts)}" if parts else None


def _window_line(windows: dict[str, Any]) -> str | None:
    if not windows.get("ok"):
        return None
    entries = windows.get("value") or []
    if not entries:
        return "Open: nothing readable"
    names: list[str] = []
    for entry in entries:
        name = entry.get("process") or "unknown"
        title = entry.get("title") or ""
        if title and title != "[private]":
            name = f"{name} ({title})"
        if name not in names:
            names.append(name)
    shown = names[:WINDOW_NAMES]
    extra = len(names) - len(shown)
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return f"Open: {', '.join(shown)}{suffix}"


def render_snapshot(snapshot: dict[str, Any], *, max_chars: int = MAX_BLOCK_CHARS) -> str:
    """A capped, de-duplicated human block for a prompt. Pure: no state kept."""
    if not snapshot or snapshot.get("paused"):
        return ""

    lines: list[str] = []
    focused = snapshot.get("focused") or {}
    if focused.get("ok"):
        value = focused.get("value") or {}
        if value.get("excluded"):
            lines.append("Focus: hidden (app excluded by the person)")
        else:
            who = value.get("process") or "unknown app"
            title = value.get("title") or ""
            lines.append(f"Focus: {who}" + (f" — {title}" if title and title != "[private]" else " (private)" if title else ""))
    else:
        lines.append(f"Focus: unavailable ({focused.get('reason') or 'unknown reason'})")

    for builder, reading in (
        (_window_line, snapshot.get("windows")),
        (_power_line, snapshot.get("power")),
        (_idle_line, snapshot.get("idle")),
        (_display_line, snapshot.get("displays")),
    ):
        line = builder(reading or {})
        if line:
            lines.append(line)

    if (snapshot.get("fullscreen") or {}).get("ok") and (snapshot["fullscreen"].get("value")):
        lines.append("Fullscreen: yes")
    if snapshot.get("in_call"):
        lines.append("In a call: yes")

    clipboard = snapshot.get("clipboard") or {}
    if clipboard.get("ok"):
        kind = (clipboard.get("value") or {}).get("kind")
        if kind and kind != "empty":
            lines.append(f"Clipboard: {kind} (contents never read)")

    stamp = datetime.now().astimezone().strftime("%H:%M")
    header = f"Desktop at {stamp} (read-only):"
    body = "\n".join(lines)
    text = f"{header}\n{body}"

    if len(text) > max_chars:
        kept: list[str] = []
        for line in [header, *lines]:
            candidate = "\n".join([*kept, line])
            if len(candidate) + 1 > max_chars:
                break
            kept.append(line)
        text = "\n".join(kept)[: max(0, max_chars - 1)] + "…"
    return text


class DesktopContextRenderer:
    """Renders the block once per change; an unchanged desktop costs nothing.

    The signature is the rendered body (the minute in the header is excluded), so
    a window that stays put for an hour injects a block exactly once.
    """

    def __init__(self, *, max_chars: int = MAX_BLOCK_CHARS) -> None:
        self.max_chars = max_chars
        self._last_signature: str | None = None

    def block(self, snapshot: dict[str, Any]) -> str:
        text = render_snapshot(snapshot, max_chars=self.max_chars)
        if not text:
            self._last_signature = None
            return ""
        signature = hashlib.sha256(text.split("\n", 1)[-1].encode("utf-8", "replace")).hexdigest()
        if signature == self._last_signature:
            return ""
        self._last_signature = signature
        return text

    def reset(self) -> None:
        self._last_signature = None


__all__ = ["DesktopContextRenderer", "MAX_BLOCK_CHARS", "render_snapshot"]
