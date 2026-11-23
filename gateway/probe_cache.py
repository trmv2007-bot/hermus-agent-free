"""Cache and single-flight the read-only probes the Control Room loads on first paint.

`/doctor/status`, `/api/v1/console/panels` and `/api/jarvis/status` each walk every
subsystem to answer. Measured on a live install they cost 8–12 s cold, and the
dashboard asks for all three while the page is still loading, so nothing else gets
served until they finish: with a Control Room open, `/healthz` went from 0.03 s to
6.2 s and `/api/v1/system/capabilities` to 21 s.

They are read-only status projections, so a short TTL is an honest trade, and one
caller should compute while the rest wait for that same answer instead of each
starting an identical probe. A caller that needs a fresh look asks for one
explicitly (`?fresh=1`) or hits the run/execute endpoint that does real work.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class ProbeCache(Generic[T]):
    """One cached, single-flight async probe.

    ``ttl`` seconds of staleness; concurrent misses share one computation. The
    cache never holds an exception: a failing probe leaves the last good value in
    place and re-raises, so an HTTP handler still reports the real error.
    """

    def __init__(self, name: str, ttl: float) -> None:
        self.name = name
        self.ttl = float(ttl)
        self._value: T | None = None
        self._computed_at = 0.0
        self._lock = asyncio.Lock()

    def peek(self) -> T | None:
        """Last computed value without touching the probe (None when cold)."""
        return self._value

    def age(self) -> float | None:
        """Seconds since the cached value was computed, or None when cold."""
        return None if self._value is None else round(time.monotonic() - self._computed_at, 3)

    async def get(self, compute: Callable[[], Awaitable[T]], *, fresh: bool = False) -> T:
        if not fresh and self._value is not None and self.age() is not None and self.age() < self.ttl:
            return self._value
        async with self._lock:
            # Someone else may have refreshed it while this caller waited.
            if not fresh and self._value is not None and self.age() is not None and self.age() < self.ttl:
                return self._value
            value = await compute()
            self._value = value
            self._computed_at = time.monotonic()
            return value

    async def warm(self, compute: Callable[[], Awaitable[T]]) -> bool:
        """Prime the cache without ever raising — used by the startup warm-up."""
        try:
            await self.get(compute)
        except Exception:  # noqa: BLE001 - a cold probe must not break startup
            return False
        return True


def fresh_requested(flag: Any) -> bool:
    """Parse a ``?fresh=1`` style query value."""
    return str(flag).strip().lower() in ("1", "true", "yes", "on")


__all__ = ["ProbeCache", "fresh_requested"]
