"""HERMUS reliability, recovery and failure-containment control plane.

This layer observes and hardens existing canonical runtimes; it does not create
a competing execution engine. It provides retry policy, circuit breakers,
idempotency receipts, durable checkpoints, incidents, integrity-checked
snapshots and resource/readiness signals.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import config
from .emergency_stop import get_emergency_stop


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.2

    def delay(self, attempt: int) -> float:
        # deterministic bounded jitter keeps tests/recovery reproducible enough
        # while avoiding synchronized retry storms.
        spread = self.jitter * min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        return min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)) + spread)


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, name: str, *, threshold: int = 3, reset_after: float = 60.0):
        self.name = str(name)
        self.threshold = max(1, int(threshold))
        self.reset_after = max(1.0, float(reset_after))
        self.state = self.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self._lock = threading.RLock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == self.OPEN and time.time() - self.opened_at >= self.reset_after:
                self.state = self.HALF_OPEN
            return self.state != self.OPEN

    def success(self) -> None:
        with self._lock:
            self.state, self.failures, self.opened_at = self.CLOSED, 0, 0.0

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.state, self.opened_at = self.OPEN, time.time()

    def snapshot(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state, "failures": self.failures,
                "threshold": self.threshold, "opened_at": self.opened_at}


@dataclass
class IdempotencyReceipt:
    key: str
    operation: str
    status: str
    result: Any = None
    created_at: float = field(default_factory=time.time)


class IdempotencyStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or config.resolve_path("data/reliability/idempotency.json"))
        self._lock = threading.RLock()
        self.receipts: dict[str, IdempotencyReceipt] = {}
        self._load()

    def get(self, key: str) -> IdempotencyReceipt | None:
        with self._lock:
            return self.receipts.get(str(key))

    def begin(self, key: str, operation: str) -> tuple[bool, IdempotencyReceipt]:
        with self._lock:
            existing = self.receipts.get(str(key))
            if existing:
                return False, existing
            rec = IdempotencyReceipt(str(key), str(operation), "in_progress")
            self.receipts[rec.key] = rec
            self._save()
            return True, rec

    def finish(self, key: str, result: Any = None, *, status: str = "succeeded") -> IdempotencyReceipt:
        with self._lock:
            rec = self.receipts.get(str(key))
            if not rec:
                raise KeyError("idempotency key not started")
            rec.status, rec.result = str(status), result
            self._save()
            return rec

    def _load(self) -> None:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = {}
        if isinstance(rows, dict):
            for key, row in rows.items():
                if isinstance(row, dict):
                    self.receipts[key] = IdempotencyReceipt(
                        key=key, operation=str(row.get("operation", "")),
                        status=str(row.get("status", "unknown")), result=row.get("result"),
                        created_at=float(row.get("created_at", time.time())))

    def _save(self) -> None:
        _atomic_json(self.path, {k: asdict(v) for k, v in list(self.receipts.items())[-5000:]})


@dataclass
class Checkpoint:
    id: str
    run_id: str
    phase: str
    state: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    verified: bool = False


class CheckpointStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or config.resolve_path("data/reliability/checkpoints.json"))
        self._lock = threading.RLock()
        self.items: dict[str, Checkpoint] = {}
        self._load()

    def save(self, run_id: str, phase: str, state: dict[str, Any] | None = None, *, verified: bool = False) -> dict[str, Any]:
        with self._lock:
            cp = Checkpoint(f"cp_{uuid.uuid4().hex[:12]}", str(run_id), str(phase), dict(state or {}), verified=bool(verified))
            self.items[cp.id] = cp
            self._save()
            return asdict(cp)

    def latest(self, run_id: str) -> dict[str, Any] | None:
        rows = [x for x in self.items.values() if x.run_id == str(run_id)]
        return asdict(max(rows, key=lambda x: x.created_at)) if rows else None

    def _load(self) -> None:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = {}
        for key, row in (rows.items() if isinstance(rows, dict) else []):
            if isinstance(row, dict):
                self.items[key] = Checkpoint(**{k: row[k] for k in ("id","run_id","phase","state","created_at","verified") if k in row})

    def _save(self) -> None:
        _atomic_json(self.path, {k: asdict(v) for k, v in list(self.items.items())[-5000:]})


@dataclass
class Incident:
    id: str
    kind: str
    severity: str
    message: str
    status: str = "open"
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None


class IncidentLedger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or config.resolve_path("data/reliability/incidents.json"))
        self._lock = threading.RLock()
        self.items: list[Incident] = []
        self._load()

    def create(self, kind: str, message: str, *, severity: str = "warning") -> dict[str, Any]:
        with self._lock:
            inc = Incident(f"inc_{uuid.uuid4().hex[:12]}", str(kind), str(severity), str(message)[:1000])
            self.items.append(inc)
            self._save()
            return asdict(inc)

    def resolve(self, incident_id: str) -> bool:
        with self._lock:
            for inc in self.items:
                if inc.id == str(incident_id) and inc.status == "open":
                    inc.status, inc.resolved_at = "resolved", time.time()
                    self._save()
                    return True
            return False

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(x) for x in self.items[-max(1, int(limit)):]][::-1]

    def _load(self) -> None:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                self.items.append(Incident(**{k: row[k] for k in ("id","kind","severity","message","status","created_at","resolved_at") if k in row}))

    def _save(self) -> None:
        _atomic_json(self.path, [asdict(x) for x in self.items[-2000:]])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class RecoverySnapshotStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or config.resolve_path("data/reliability/snapshots"))
        self.root.mkdir(parents=True, exist_ok=True)

    def snapshot_paths(self, paths: list[str | Path], *, label: str = "auto") -> dict[str, Any]:
        stamp = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        target = self.root / f"{label}_{stamp}"
        target.mkdir(parents=True, exist_ok=True)
        manifest = {"id": target.name, "created_at": time.time(), "files": []}
        for raw in paths:
            src = Path(raw).expanduser()
            if not src.exists() or not src.is_file():
                continue
            dst = target / src.name
            shutil.copy2(src, dst)
            manifest["files"].append({"name": src.name, "sha256": _sha256(dst), "size": dst.stat().st_size})
        _atomic_json(target / "manifest.json", manifest)
        return manifest

    def restore(self, snapshot_id: str, target_dir: str | Path) -> dict[str, Any]:
        check = self.verify(snapshot_id)
        if not check.get("valid"):
            return {"success": False, "error": "snapshot_integrity_failed", **check}
        target = Path(target_dir).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        source = (self.root / str(snapshot_id)).resolve()
        restored = []
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        for row in manifest.get("files", []):
            dst = (target / row["name"]).resolve()
            if target not in dst.parents and dst != target:
                return {"success": False, "error": "unsafe_restore_path"}
            shutil.copy2(source / row["name"], dst)
            restored.append(str(dst))
        return {"success": True, "snapshot_id": str(snapshot_id), "restored": restored}

    def verify(self, snapshot_id: str) -> dict[str, Any]:
        target = self.root / str(snapshot_id)
        manifest_path = target / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"valid": False, "error": "manifest_missing_or_invalid"}
        bad = []
        for row in manifest.get("files", []):
            p = target / row["name"]
            if not p.exists() or _sha256(p) != row["sha256"]:
                bad.append(row["name"])
        return {"valid": not bad, "snapshot_id": str(snapshot_id), "corrupt": bad}


class ResourceGuard:
    def snapshot(self) -> dict[str, Any]:
        usage = shutil.disk_usage(Path.cwd())
        result: dict[str, Any] = {
            "disk_free_bytes": usage.free,
            "disk_total_bytes": usage.total,
            "disk_free_ratio": usage.free / max(1, usage.total),
        }
        try:
            import psutil
            result["memory_percent"] = float(psutil.virtual_memory().percent)
            result["cpu_percent"] = float(psutil.cpu_percent(interval=None))
        except Exception:
            result["memory_percent"] = None
            result["cpu_percent"] = None
        result["degraded"] = result["disk_free_ratio"] < 0.05 or (result["memory_percent"] is not None and result["memory_percent"] > 95)
        return result


class ReliabilitySupervisor:
    def __init__(self):
        self.idempotency = IdempotencyStore()
        self.checkpoints = CheckpointStore()
        self.incidents = IncidentLedger()
        self.snapshots = RecoverySnapshotStore()
        self.resources = ResourceGuard()
        self.circuits: dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()

    def circuit(self, name: str) -> CircuitBreaker:
        with self._lock:
            return self.circuits.setdefault(str(name), CircuitBreaker(str(name)))

    def health(self) -> dict[str, Any]:
        queue = {}
        distributed = {}
        try:
            from gateway.queue import job_queue
            queue = job_queue.status()
        except Exception as exc:
            queue = {"error": str(exc)}
        try:
            from .distributed import distributed as coordinator
            distributed = coordinator.status()
        except Exception as exc:
            distributed = {"error": str(exc)}
        resources = self.resources.snapshot()
        state = "degraded" if resources.get("degraded") or get_emergency_stop().active() else "healthy"
        return {
            "status": state,
            "emergency_stop": get_emergency_stop().active(),
            "queue": queue,
            "distributed": {"node_count": distributed.get("node_count", 0), "online": distributed.get("online", 0), "stale": distributed.get("stale", 0)},
            "resources": resources,
            "circuits": [x.snapshot() for x in self.circuits.values()],
            "open_incidents": sum(x.status == "open" for x in self.incidents.items),
            "checkpoint_count": len(self.checkpoints.items),
        }

    def rollback_after_verification_failure(self, checkpoint_id: str, *, reason: str = "verification_failed") -> dict[str, Any]:
        if get_emergency_stop().active():
            return {"success": False, "error": "emergency_stop_active"}
        try:
            from .rollback import rollback_manager
            result = rollback_manager.restore(str(checkpoint_id))
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if result.get("success"):
            self.incidents.create("automatic_rollback", f"{reason}: restored {checkpoint_id}", severity="warning")
        else:
            self.incidents.create("rollback_failed", f"{reason}: {result.get('error', 'unknown')}", severity="critical")
        return result

    def status(self) -> dict[str, Any]:
        return self.health() | {"incidents": self.incidents.list(25), "latest_checkpoints": [asdict(x) for x in list(self.checkpoints.items.values())[-25:]]}


reliability = ReliabilitySupervisor()

__all__ = [
    "RetryPolicy", "CircuitBreaker", "IdempotencyReceipt", "IdempotencyStore",
    "Checkpoint", "CheckpointStore", "Incident", "IncidentLedger",
    "RecoverySnapshotStore", "ResourceGuard", "ReliabilitySupervisor", "reliability",
]
