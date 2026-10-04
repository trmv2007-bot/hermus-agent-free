"""Ambient HERMUS observation loop.

Tier 2 keeps the existing canonical systems continuously reconciled without
creating another executor. It periodically refreshes read-only world awareness,
projects the current attention set through PresenceKernel, and emits canonical
state-change events only when attention meaningfully changes.

No model calls, tools, missions, permissions or external actions originate here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

from .contracts import CommandStatus, EventEnvelope, EventType
from .events import get_bus
from .presence_kernel import PresenceKernel, presence_kernel
from .world_awareness import WorldAwareness, world_awareness


def _fingerprint(items: list[dict[str, Any]]) -> str:
    stable = [
        {
            "id": str(item.get("id") or ""),
            "severity": str(item.get("severity") or ""),
            "title": str(item.get("title") or ""),
            "detail": str(item.get("detail") or ""),
        }
        for item in items
    ]
    payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


class AmbientLoop:
    """Bounded background observer for one HERMUS process."""

    def __init__(
        self,
        *,
        kernel: PresenceKernel | None = None,
        awareness: WorldAwareness | None = None,
        bus=None,
        refresh: Callable[..., Any] | None = None,
    ) -> None:
        self.kernel = kernel or presence_kernel
        self.awareness = awareness or world_awareness
        self.bus = bus or get_bus()
        self.refresh = refresh
        self._last_fingerprint: str | None = None
        self._running = False
        self._lock = asyncio.Lock()

    @staticmethod
    def interval_seconds(default: int = 30) -> int:
        try:
            from .config import config

            return max(15, int(getattr(config, "presence_heartbeat_seconds", default) or default))
        except Exception:
            return max(15, int(default))

    def tick(self, *, user_id: str = "default") -> dict[str, Any]:
        """Perform one read-only observation pass and return the projection."""
        self.kernel.start()

        try:
            world = (
                self.refresh()
                if self.refresh is not None
                else self.awareness.refresh(include_processes=True)
            )
        except Exception as exc:
            self._emit(
                command="ambient.world_refresh_failed",
                args={"error": str(exc)[:300]},
                status=CommandStatus.FAILED.value,
            )
            world = {"error": f"{type(exc).__name__}: {exc}"}

        snapshot = self.kernel.snapshot(user_id=user_id, include_events=False)
        attention = snapshot.get("attention") or []
        current = _fingerprint(attention)

        previous = self._last_fingerprint
        self._last_fingerprint = current

        raised: list[dict[str, Any]] = []
        cleared: list[str] = []
        if previous is not None and previous != current:
            previous_items = getattr(self, "_last_items", [])
            previous_ids = {str(item.get("id") or "") for item in previous_items}
            current_ids = {str(item.get("id") or "") for item in attention}
            raised = [item for item in attention if str(item.get("id") or "") not in previous_ids]
            cleared = sorted(previous_ids - current_ids)

            if raised:
                self._emit(
                    command="attention.raised",
                    args={"items": raised[:8], "count": len(raised)},
                )
            if cleared:
                self._emit(
                    command="attention.cleared",
                    args={"ids": cleared[:20], "count": len(cleared)},
                    status=CommandStatus.SUCCEEDED.value,
                )

        self._last_items = [dict(item) for item in attention[:20]]
        return {
            "ok": "error" not in world,
            "attention": attention,
            "attention_fingerprint": current,
            "attention_changed": bool(previous is not None and previous != current),
            "raised": raised,
            "cleared": cleared,
            "world": world,
            "summary": snapshot.get("summary") or {},
        }

    def _emit(self, *, command: str, args: dict[str, Any], status: str = CommandStatus.PENDING.value) -> None:
        try:
            self.bus.publish(
                EventEnvelope(
                    type=EventType.STATE_CHANGED.value,
                    command=command,
                    source="ambient",
                    status=status,
                    args_redacted=args,
                )
            )
        except Exception:
            pass

    async def run(self, *, user_id: str = "default", interval_seconds: int | None = None) -> None:
        """Run until cancelled. Cancellation is graceful and never suppresses shutdown."""
        interval = max(15, int(interval_seconds or self.interval_seconds()))
        self._running = True
        try:
            while True:
                try:
                    await asyncio.to_thread(self.tick, user_id=user_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Observation failures are already represented as explicit
                    # ambient events where possible. Never terminate the gateway.
                    pass
                await asyncio.sleep(interval)
        finally:
            self._running = False

    @property
    def running(self) -> bool:
        return self._running


ambient_loop = AmbientLoop()

__all__ = ["AmbientLoop", "ambient_loop"]
