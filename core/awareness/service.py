"""The awareness service: one snapshot, one block, one poll loop.

The service owns the provider, the settings file, the rolling journal and the
renderer. It exists so the gateway, the CLI and the attention gate all see *the
same* picture of the desktop at the same instant instead of three reads that
disagree.
"""

from __future__ import annotations

import os
import platform
import threading
import time
from datetime import datetime
from typing import Any

from ..connectors.desktop import CALL_HINTS, DesktopProvider, PrivacyPolicy, default_desktop_provider, read_desktop
from .journal import AwarenessJournal
from .render import DesktopContextRenderer
from .settings import AwarenessSettings, SettingsStore

CONTEXT_ENV = "HERMUS_AWARENESS_CONTEXT"
POLLER_ENV = "HERMUS_AWARENESS_POLLER"
CONTEXT_MODES = ("chat",)
POLL_INTERVAL_SECONDS = 1.0


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _detect_call(snapshot: dict[str, Any]) -> bool:
    focused = snapshot.get("focused") or {}
    titles: list[str] = []
    if focused.get("ok"):
        titles.append((focused.get("value") or {}).get("title") or "")
    windows = snapshot.get("windows") or {}
    if windows.get("ok"):
        titles.extend((entry.get("title") or "") for entry in windows.get("value") or [])
    haystack = " ".join(title.lower() for title in titles if title and title != "[private]")
    return any(hint in haystack for hint in CALL_HINTS)


class AwarenessService:
    """Read-only desktop perception plus the person's privacy controls."""

    def __init__(
        self,
        *,
        provider: DesktopProvider | None = None,
        store: SettingsStore | None = None,
        journal: AwarenessJournal | None = None,
        renderer: DesktopContextRenderer | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        autostart: bool = False,
    ) -> None:
        self.provider = provider
        self.store = store or SettingsStore()
        self.journal = journal or AwarenessJournal()
        self.renderer = renderer or DesktopContextRenderer()
        self.poll_interval = float(poll_interval)
        self.autostart = bool(autostart)
        self._lock = threading.RLock()
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reads = 0

    # -- settings -----------------------------------------------------------

    def settings(self) -> AwarenessSettings:
        return self.store.load()

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        settings = self.store.update(patch)
        self.renderer.reset()
        self._snapshot = None
        if settings.enabled and not settings.paused:
            self.ensure_poller()
        return settings.to_dict()

    def pause(self, paused: bool = True) -> dict[str, Any]:
        settings = self.store.update({"paused": bool(paused)})
        if paused:
            self.renderer.reset()
        return settings.to_dict()

    # -- perception ---------------------------------------------------------

    def policy(self) -> PrivacyPolicy:
        settings = self.settings()
        return PrivacyPolicy(excluded_apps=tuple(settings.excluded_apps), clipboard=settings.clipboard)

    def _provider(self) -> DesktopProvider:
        return self.provider or default_desktop_provider()

    def _read(self) -> dict[str, Any]:
        settings = self.settings()
        snapshot = read_desktop(self._provider(), policy=self.policy())
        snapshot["paused"] = bool(settings.paused or not settings.enabled)
        snapshot["in_call"] = _detect_call(snapshot)
        with self._lock:
            self._snapshot = snapshot
            self._snapshot_at = time.monotonic()
            self._reads += 1
        return snapshot

    def snapshot(self, *, fresh: bool = False) -> dict[str, Any]:
        """The current picture; cached for one poll interval unless ``fresh``."""
        settings = self.settings()
        if settings.paused or not settings.enabled:
            return self._paused_snapshot(settings)
        with self._lock:
            cached = self._snapshot
            age = time.monotonic() - self._snapshot_at
        if cached is not None and not fresh and age < self.poll_interval * 2:
            return cached
        if self.autostart:
            self.ensure_poller()
        return self._read()

    @staticmethod
    def _paused_snapshot(settings: AwarenessSettings) -> dict[str, Any]:
        reason = "awareness is paused by the person" if settings.paused else "awareness is turned off"
        return {
            "read_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "platform": platform.system(),
            "provider": "paused",
            "paused": True,
            "reason": reason,
            "focused": {"ok": False, "reason": reason},
            "windows": {"ok": False, "reason": reason},
            "power": {"ok": False, "reason": reason},
            "idle": {"ok": False, "reason": reason},
            "displays": {"ok": False, "reason": reason},
            "clipboard": {"ok": False, "reason": reason},
            "fullscreen": {"ok": False, "reason": reason},
            "in_call": False,
            "timings_ms": {},
        }

    def public_snapshot(self, *, fresh: bool = False) -> dict[str, Any]:
        """The snapshot plus what the person is allowed to know about the layer."""
        settings = self.settings()
        snapshot = self.snapshot(fresh=fresh)
        readable = [key for key in ("focused", "windows", "power", "idle", "displays", "clipboard") if snapshot.get(key, {}).get("ok")]
        unavailable = {
            key: snapshot[key]["reason"]
            for key in ("focused", "windows", "power", "idle", "displays", "clipboard")
            if snapshot.get(key) and not snapshot[key].get("ok")
        }
        return {
            **snapshot,
            "settings": {
                "enabled": settings.enabled,
                "paused": settings.paused,
                "context_injection": settings.context_injection,
                "excluded_apps": list(settings.excluded_apps),
                "clipboard": settings.clipboard,
                "quiet_hours": settings.quiet_hours,
            },
            "readable": readable,
            "unavailable": unavailable,
            "polling": self.poller_running,
            "reads": self._reads,
        }

    # -- prompt context -----------------------------------------------------

    def context_block(self, *, mode: str = "chat") -> str:
        """The chat-turn block, or ``""`` when the flag, mode or pause says no."""
        if not _env_enabled(CONTEXT_ENV, True):
            return ""
        if mode not in CONTEXT_MODES:
            return ""
        settings = self.settings()
        if not settings.enabled or not settings.context_injection or settings.paused:
            return ""
        try:
            return self.renderer.block(self.snapshot())
        except Exception:
            return ""

    # -- poll loop ----------------------------------------------------------

    @property
    def poller_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def ensure_poller(self) -> None:
        if not self.autostart and self._thread is None:
            return
        if not _env_enabled(POLLER_ENV, True):
            return
        if self.poller_running:
            return
        with self._lock:
            if self.poller_running:
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._poll_loop, name="hermus-awareness", daemon=True)
            self._thread.start()

    def stop_poller(self) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
            self._stop.set()
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def poll_once(self) -> dict[str, Any]:
        """One tick of the loop, callable directly by tests and the CLI."""
        settings = self.settings()
        if settings.paused or not settings.enabled:
            return {"skipped": "paused"}
        snapshot = self._read()
        self.journal.sample(snapshot)
        if settings.proactive:
            self.gate().tick(snapshot=snapshot)
        return {"snapshot": snapshot}

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                # A perception failure must never kill the loop or the gateway.
                pass
            self._stop.wait(self.poll_interval)

    # -- attention ----------------------------------------------------------

    def gate(self):
        """The shared attention gate (imported lazily to keep this module small)."""
        from ..attention import attention_gate

        return attention_gate(self)


awareness = AwarenessService(autostart=True)


def get_awareness() -> AwarenessService:
    return awareness


__all__ = ["AwarenessService", "awareness", "get_awareness", "CONTEXT_ENV", "CONTEXT_MODES"]
