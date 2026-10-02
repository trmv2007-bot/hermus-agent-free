"""HERMUS reliability, recovery and failure-containment control plane.

This layer observes and hardens existing canonical runtimes; it does not create
a competing execution engine. It provides retry policy, circuit breakers,
idempotency receipts, durable checkpoints, incidents, integrity-checked
snapshots and resource/readiness signals.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import config
from .emergency_stop import get_emergency_stop
from .metrics import metrics


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
        spread = self.jitter * min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        return min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)) + spread)


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, name: str, *, threshold: int = 3, reset_after: float = 60.0):
        self.name = str(name)
        self.threshold = max(1, int(threshold))
        # Short reset windows are useful for tests and local providers. Do not
        # silently coerce sub-second configuration to one second.
        self.reset_after = max(0.0, float(reset_after))
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
                metrics.inc("idempotency.duplicates")
                return False, existing
            rec = IdempotencyReceipt(str(key), str(operation), "in_progress")
            self.receipts[rec.key] = rec
            self._save()
            metrics.inc("idempotency.started")
            return True, rec

    def finish(self, key: str, result: Any = None, *, status: str = "succeeded") -> IdempotencyReceipt:
        with self._lock:
            rec = self.receipts.get(str(key))
            if not rec:
                raise KeyError("idempotency key not started")
            rec.status, rec.result = str(status), result
            self._save()
            metrics.inc(f"idempotency.{status}")
            return rec

    def _load(self) -> None:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = {}
        if isinstance(rows, dict):
            for key, row in rows.items():
                if isinstance(row, dict):
                    self.receipts[key] = IdempotencyReceipt(key=key, operation=str(row.get("operation", "")), status=str(row.get("status", "unknown")), result=row.get("result"), created_at=float(row.get("created_at", time.time())))

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
            metrics.inc("checkpoints.created")
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
                self.items[key] = Checkpoint(**{k: row[k] for k in ("id", "run_id", "phase", "state", "created_at", "verified") if k in row})

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
            metrics.inc(f"incidents.{severity}")
            return asdict(inc)

    def resolve(self, incident_id: str) -> bool:
        with self._lock:
            for inc in self.items:
                if inc.id == str(incident_id) and inc.status == "open":
                    inc.status, inc.resolved_at = "resolved", time.time()
                    self._save()
                    metrics.inc("incidents.resolved")
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
                try:
                    self.items.append(Incident(**row))
                except TypeError:
                    continue

    def _save(self) -> None:
        _atomic_json(self.path, [asdict(x) for x in self.items[-5000:]])
