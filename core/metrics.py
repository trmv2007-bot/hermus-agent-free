"""Small dependency-free observability primitives for HERMUS.

The registry is intentionally local and bounded: it gives the Control Room and
health endpoints stable counters without requiring a Prometheus deployment.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricRegistry:
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    timings_ms: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=500)))
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def inc(self, name: str, value: int = 1) -> int:
        with self._lock:
            self.counters[str(name)] += int(value)
            return self.counters[str(name)]

    def observe(self, name: str, duration_ms: float) -> None:
        with self._lock:
            self.timings_ms[str(name)].append(float(duration_ms))

    def timer(self, name: str):
        registry = self

        class _Timer:
            def __enter__(self):
                self.started = time.perf_counter()
                return self

            def __exit__(self, exc_type, exc, tb):
                registry.observe(name, (time.perf_counter() - self.started) * 1000.0)
                registry.inc(f"{name}.errors" if exc else f"{name}.success")

        return _Timer()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            timings = {}
            for name, values in self.timings_ms.items():
                xs = sorted(values)
                if not xs:
                    continue
                timings[name] = {
                    "count": len(xs),
                    "p50_ms": xs[len(xs) // 2],
                    "p95_ms": xs[min(len(xs) - 1, int(len(xs) * 0.95))],
                    "max_ms": xs[-1],
                }
            return {"counters": dict(self.counters), "timings": timings}


metrics = MetricRegistry()

__all__ = ["MetricRegistry", "metrics"]
