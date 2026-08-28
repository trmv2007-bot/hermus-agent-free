"""Unified world model for Hermus.

This is the shared situational-awareness layer above individual integrations.
Sources publish observations; planners query a time-stamped model instead of
asking each integration for an unrelated guess.  The model is intentionally
boring and deterministic: LLMs may interpret facts, but they do not define or
silently rewrite observation provenance.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

_SECRET_WORDS = ("password", "token", "secret", "private_key", "api_key", "credential")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    """Remove obvious credential values before they enter the model or log."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(word in str(key).lower() for word in _SECRET_WORDS) else _safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


@dataclass
class WorldFact:
    subject: str
    predicate: str
    value: Any
    source: str
    confidence: float = 1.0
    observed_at: str = field(default_factory=_now)
    expires_at: Optional[str] = None
    permission_scope: Optional[str] = None
    fact_id: str = field(default_factory=lambda: f"fact_{uuid.uuid4().hex[:12]}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorldEvent:
    event_type: str
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorldModel:
    """Thread-safe, provenance-aware store for current facts and recent events."""

    def __init__(self, path: Optional[Path] = None, max_events: int = 500):
        self.path = Path(path) if path else None
        self._facts: dict[tuple[str, str], WorldFact] = {}
        self._events: deque[WorldEvent] = deque(maxlen=max(50, int(max_events)))
        self._listeners: list[Callable[[WorldEvent], None]] = []
        self._lock = threading.RLock()
        if self.path:
            self._load()

    def _load(self) -> None:
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                kind = item.get("kind")
                if kind == "fact":
                    fact = WorldFact(**item["value"])
                    self._facts[(fact.subject, fact.predicate)] = fact
                elif kind == "event":
                    self._events.append(WorldEvent(**item["value"]))
        except (OSError, ValueError, TypeError, KeyError):
            # A damaged observation journal must not stop Hermus from starting.
            self._facts.clear()
            self._events.clear()

    def _append(self, kind: str, value: dict[str, Any]) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"kind": kind, "value": value, "recorded_at": _now()}, sort_keys=True) + "\n")
        except OSError:
            pass

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def observe(
        self,
        subject: str,
        predicate: str,
        value: Any,
        *,
        source: str,
        confidence: float = 1.0,
        permission_scope: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> WorldFact:
        """Record or replace one fact, preserving source and permission metadata."""
        fact = WorldFact(
            subject=str(subject),
            predicate=str(predicate),
            value=_safe(value),
            source=str(source),
            confidence=self._confidence(confidence),
            permission_scope=permission_scope,
            expires_at=expires_at,
        )
        with self._lock:
            self._facts[(fact.subject, fact.predicate)] = fact
            self._append("fact", fact.to_dict())
        return fact

    def emit(self, event_type: str, data: Optional[dict[str, Any]] = None, *, source: str = "system") -> WorldEvent:
        event = WorldEvent(str(event_type), str(source), _safe(data or {}))
        with self._lock:
            self._events.append(event)
            self._append("event", event.to_dict())
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                pass
        return event

    def subscribe(self, listener: Callable[[WorldEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return unsubscribe

    def get(self, subject: str, predicate: str) -> Optional[WorldFact]:
        with self._lock:
            return self._facts.get((subject, predicate))

    def query(self, subject: Optional[str] = None, predicate: Optional[str] = None) -> list[WorldFact]:
        with self._lock:
            return [fact for fact in self._facts.values()
                    if (subject is None or fact.subject == subject)
                    and (predicate is None or fact.predicate == predicate)]

    def recent_events(self, limit: int = 50) -> list[WorldEvent]:
        with self._lock:
            return list(self._events)[-max(0, int(limit)):]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "generated_at": _now(),
                "facts": [fact.to_dict() for fact in self._facts.values()],
                "recent_events": [event.to_dict() for event in self._events],
            }

    def ingest(self, source: str, facts: Iterable[dict[str, Any]], *, permission_scope: Optional[str] = None) -> int:
        count = 0
        for item in facts:
            if not item.get("subject") or not item.get("predicate"):
                continue
            self.observe(
                item["subject"], item["predicate"], item.get("value"), source=source,
                confidence=item.get("confidence", 1.0),
                permission_scope=item.get("permission_scope", permission_scope),
                expires_at=item.get("expires_at"),
            )
            count += 1
        return count

    def refresh_runtime(self, *, source: str = "runtime", permission_scope: str = "system.read") -> dict[str, Any]:
        """Observe the host Hermus is running on and return the profile."""
        total, used, free = shutil.disk_usage(os.getcwd())
        profile: dict[str, Any] = {
            "platform": platform.platform(),
            "os": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "cpu_cores": os.cpu_count() or 1,
            "disk": {"total_bytes": total, "used_bytes": used, "free_bytes": free},
        }
        try:
            import psutil  # type: ignore
            memory = psutil.virtual_memory()
            profile["memory"] = {"total_bytes": memory.total, "available_bytes": memory.available}
            profile["cpu_percent"] = psutil.cpu_percent(interval=None)
            profile["battery"] = _safe(psutil.sensors_battery()._asdict()) if psutil.sensors_battery() else None
        except Exception:
            profile["memory"] = {"total_bytes": None, "available_bytes": None}
            profile["cpu_percent"] = None
            profile["battery"] = None
        self.ingest(source, [{"subject": "runtime", "predicate": key, "value": value} for key, value in profile.items()], permission_scope=permission_scope)
        self.emit("runtime_refreshed", {"profile_keys": sorted(profile)}, source=source)
        return profile


world_model = WorldModel()

__all__ = ["WorldEvent", "WorldFact", "WorldModel", "world_model"]
