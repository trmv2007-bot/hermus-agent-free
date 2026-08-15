"""Lightweight resource / performance telemetry for the computer agent.

Phase D performance layer.  A stdlib-first sampler reports CPU, memory and disk
usage for the current process plus the in-memory footprint of the event bus and
cache, so the dashboard and remote clients can watch whether Hermus is healthy
(or spinning in a repair loop) without pulling in heavy profiling tools.
``psutil`` is used when available; otherwise POSIX ``resource``/``os`` fallbacks
keep the module importable and testable anywhere.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _read_pid_stat() -> Optional[Dict[str, float]]:
    """Parse /proc/<pid>/stat + /proc/<pid>/status on Linux for CPU/mem."""
    try:
        pid = os.getpid()
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
            fields = fh.read().split()
        # Field indexes are fixed by the kernel ABI (see `man 5 proc`).
        utime = float(fields[13])
        stime = float(fields[14])
        starttime = float(fields[21])
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as fh:
            status = fh.read()
        rss = None
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    rss = float(parts[1]) * 1024.0  # kB -> bytes
        with open("/proc/stat", "r", encoding="utf-8") as fh:
            cpu_line = fh.readline().split()
        total_cpu = sum(float(v) for v in cpu_line[1:8])
        return {"utime": utime, "stime": stime, "starttime": starttime,
                "rss": rss, "total_cpu": total_cpu}
    except Exception:  # noqa: BLE001  (non-Linux or restricted)
        return None


class ResourceMonitor:
    """Sample process CPU/memory/disk usage and subsystem footprints."""

    def __init__(
        self,
        subsystem_readers: Optional[Dict[str, Callable[[], Dict[str, Any]]]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._subsystem_readers = dict(subsystem_readers or {})
        self._prev: Optional[Dict[str, float]] = None

    def register_subsystem(self, name: str, reader: Callable[[], Dict[str, Any]]) -> None:
        with self._lock:
            self._subsystem_readers[name] = reader

    @staticmethod
    def _disk_usage() -> Dict[str, Any]:
        try:
            import shutil

            total, used, free = shutil.disk_usage("/")
            return {
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "used_percent": round(used / max(1, total) * 100, 1),
            }
        except Exception:  # noqa: BLE001
            return {}

    def sample(self) -> Dict[str, Any]:
        """Return one point-in-time telemetry snapshot."""
        out: Dict[str, Any] = {"ts": _now(), "pid": os.getpid()}
        # CPU / memory
        psutil = self._import_psutil()
        if psutil is not None:
            try:
                proc = psutil.Process(os.getpid())
                out["cpu_percent"] = round(proc.cpu_percent(interval=0.05), 1)
                mem = proc.memory_info()
                out["memory_bytes"] = int(getattr(mem, "rss", 0))
                out["threads"] = proc.num_threads()
                vm = psutil.virtual_memory()
                out["system_memory"] = {
                    "total": vm.total,
                    "available": vm.available,
                    "used_percent": round(vm.percent, 1),
                }
                cpu = psutil.cpu_percent(interval=None)
                out["system_cpu_percent"] = round(cpu, 1)
            except Exception:  # noqa: BLE001
                self._fill_stdlib(out)
        else:
            self._fill_stdlib(out)
        out["disk"] = self._disk_usage()

        # Subsystem footprints (cache, event bus, memory stores).
        subsystems: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            readers = dict(self._subsystem_readers)
        for name, reader in readers.items():
            try:
                subsystems[name] = reader() or {}
            except Exception as exc:  # noqa: BLE001
                subsystems[name] = {"error": str(exc)}
        out["subsystems"] = subsystems
        return out

    def _fill_stdlib(self, out: Dict[str, Any]) -> None:
        """POSIX fallback for CPU/memory when psutil is unavailable."""
        out["cpu_percent"] = None
        out["memory_bytes"] = None
        out["threads"] = None
        stat = _read_pid_stat()
        if stat is not None:
            try:
                out["memory_bytes"] = int(stat.get("rss") or 0)
            except Exception:  # noqa: BLE001
                pass
            now_total = stat.get("total_cpu")
            proc_total = stat.get("utime", 0.0) + stat.get("stime", 0.0)
            now = time.monotonic()
            prev = self._prev
            if prev is not None and now_total and prev.get("now_total") is not None:
                dt = max(0.0001, now - prev["now"])
                dt_cpu = max(0.0, now_total - prev["now_total"])
                dt_proc = max(0.0, proc_total - prev.get("proc_total", 0.0))
                if dt_cpu > 0:
                    cores = max(1, os.cpu_count() or 1)
                    out["cpu_percent"] = round(dt_proc / dt_cpu / cores * 100.0, 1)
            self._prev = {
                "now": now,
                "now_total": now_total,
                "proc_total": proc_total,
            }
        try:
            out["threads"] = len(tuple(x for x in os.listdir(f"/proc/{os.getpid()}/task")))
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _import_psutil():
        try:
            import psutil  # type: ignore

            return psutil
        except Exception:  # noqa: BLE001
            return None

    def register_event_bus(self, bus) -> None:
        def reader() -> Dict[str, Any]:
            try:
                return {"recent_events": len(getattr(bus, "_recent", ()) or ())}
            except Exception:  # noqa: BLE001
                return {}
        self.register_subsystem("event_bus", reader)

    def register_cache(self, cache_stats: Callable[[], Dict[str, Any]]) -> None:
        self.register_subsystem("cache", cache_stats)


resource_monitor = ResourceMonitor()


def get_resource_monitor() -> ResourceMonitor:
    """Return the shared monitor (lazily wires subsystem readers)."""
    from .events import computer_event_bus

    resource_monitor.register_event_bus(computer_event_bus)
    try:
        from core.cache import get_cache_stats

        resource_monitor.register_cache(get_cache_stats)
    except Exception:  # noqa: BLE001
        pass
    return resource_monitor
