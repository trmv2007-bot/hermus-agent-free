"""Awareness settings: the person's controls, and the only free-standing file.

Everything the awareness layer may change lives in one JSON file
(``data/awareness/settings.json`` by default). Nothing else is written — no
titles, no clipboard, no window history; the 24h rolling summary in
``core/awareness/journal.py`` is the one other on-disk artefact and the person
can delete it.

Unknown keys are rejected rather than stored, so a typo in a PUT cannot silently
become a setting that does nothing.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_json

DEFAULT_PATH = "data/awareness/settings.json"

# Keys a client may write. ``muted_patterns`` is managed by the attention gate
# (a "not now" choice), never by hand.
WRITABLE_KEYS = frozenset(
    {
        "enabled",
        "paused",
        "context_injection",
        "excluded_apps",
        "clipboard",
        "quiet_hours",
        "max_per_hour",
        "min_gap_minutes",
        "typing_seconds",
        "respect_fullscreen",
        "respect_calls",
        "proactive",
        "battery_warn_percent",
        "long_session_minutes",
    }
)


def _clock_minutes(value: str | None) -> int | None:
    """``"22:30"`` -> 1350; anything unparsable is rejected, not guessed."""
    if value is None:
        return None
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"time '{text}' must look like HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time '{text}' is out of range")
    return hour * 60 + minute


@dataclass
class AwarenessSettings:
    """What the person allows the awareness layer to do."""

    enabled: bool = True
    paused: bool = False
    context_injection: bool = True
    excluded_apps: list[str] = field(default_factory=list)
    clipboard: bool = True
    quiet_hours: dict[str, str] | None = field(default_factory=lambda: {"start": "22:00", "end": "07:00"})
    max_per_hour: int = 2
    min_gap_minutes: int = 20
    typing_seconds: float = 3.0
    respect_fullscreen: bool = True
    respect_calls: bool = True
    proactive: bool = True
    battery_warn_percent: int = 20
    long_session_minutes: int = 90
    muted_patterns: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def quiet_window(self) -> tuple[int, int] | None:
        """Quiet hours as (start_minute, end_minute) in local time, or None."""
        if not self.quiet_hours:
            return None
        try:
            start = _clock_minutes(self.quiet_hours.get("start"))
            end = _clock_minutes(self.quiet_hours.get("end"))
        except (ValueError, TypeError, AttributeError):
            return None
        if start is None or end is None or start == end:
            return None
        return start, end

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> AwarenessSettings:
        settings = cls()
        for key, value in (payload or {}).items():
            if not hasattr(settings, key):
                continue
            try:
                setattr(settings, key, value)
            except Exception:
                continue
        settings.validate()
        return settings

    def validate(self) -> None:
        """Coerce every field into its legal range; never raise on bad input."""
        self.enabled = bool(self.enabled)
        self.paused = bool(self.paused)
        self.context_injection = bool(self.context_injection)
        self.clipboard = bool(self.clipboard)
        self.respect_fullscreen = bool(self.respect_fullscreen)
        self.respect_calls = bool(self.respect_calls)
        self.proactive = bool(self.proactive)
        if not isinstance(self.excluded_apps, list):
            self.excluded_apps = []
        self.excluded_apps = [str(item).strip().lower() for item in self.excluded_apps if str(item).strip()][:100]
        self.max_per_hour = max(0, min(20, int(self.max_per_hour or 0)))
        self.min_gap_minutes = max(0, min(240, int(self.min_gap_minutes or 0)))
        self.typing_seconds = max(0.0, min(60.0, float(self.typing_seconds or 0)))
        self.battery_warn_percent = max(1, min(90, int(self.battery_warn_percent or 20)))
        self.long_session_minutes = max(15, min(600, int(self.long_session_minutes or 90)))
        try:
            _clock_minutes((self.quiet_hours or {}).get("start"))
            _clock_minutes((self.quiet_hours or {}).get("end"))
        except (ValueError, TypeError, AttributeError):
            self.quiet_hours = None
        if not isinstance(self.muted_patterns, dict):
            self.muted_patterns = {}


class SettingsStore:
    """Thread-safe read/modify/write of the awareness settings file."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("HERMUS_AWARENESS_SETTINGS") or DEFAULT_PATH
        self.path = Path(configured)
        self._lock = threading.RLock()
        self._cache: AwarenessSettings | None = None

    def load(self) -> AwarenessSettings:
        with self._lock:
            if self._cache is not None:
                return self._cache
            payload: dict[str, Any] = {}
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, ValueError):
                payload = {}
            self._cache = AwarenessSettings.from_dict(payload)
            return self._cache

    def save(self, settings: AwarenessSettings) -> AwarenessSettings:
        with self._lock:
            settings.validate()
            settings.updated_at = _now()
            try:
                atomic_write_json(self.path, settings.to_dict())
            except OSError:
                # A read-only data directory must not break a turn or a route.
                pass
            self._cache = settings
            return settings

    def update(self, patch: dict[str, Any]) -> AwarenessSettings:
        """Apply a client patch; unknown keys are refused, not silently kept."""
        with self._lock:
            settings = self.load()
            unknown = sorted(set(patch or {}) - WRITABLE_KEYS)
            if unknown:
                raise ValueError(f"unknown awareness setting(s): {', '.join(unknown)}")
            payload = settings.to_dict()
            payload.update({key: value for key, value in (patch or {}).items()})
            merged = AwarenessSettings.from_dict(payload)
            merged.validate()
            return self.save(merged)

    def mute_pattern(self, pattern: str, until_iso: str) -> AwarenessSettings:
        with self._lock:
            settings = self.load()
            settings.muted_patterns[str(pattern)] = until_iso
            return self.save(settings)

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["AwarenessSettings", "SettingsStore", "WRITABLE_KEYS", "DEFAULT_PATH"]
