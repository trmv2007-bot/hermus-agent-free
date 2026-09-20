"""Fleet Bus — the event-sourced communication contract (SPEC §6).

``FleetBus`` is the **new canonical bus**: one writer, a monotonic ``seq``,
cursors instead of in-memory queues, durable JSONL segments, snapshot + replay
boot, and idempotency keys. It is built standalone in roadmap step 0b: the legacy
``core/harness/bus.py`` and ``core/agents/messaging.py`` are untouched and get
merged onto this contract in roadmap step 3 (§10), so no existing behaviour
changes here.

Envelope (SPEC_PERSISTENT_FLEET §6 — this is also the on-disk field order)::

    {"seq": 10432, "id": "uuid", "ts": "...", "sender": "...",
     "target": "agent_id|null", "kind": "...", "priority": 1-4,
     "content": "...", "reply_to": "event_id|null", "mission_id": "...",
     "trace_id": "...", "est_tokens": 123}

Guarantees
----------
**Single writer.** Every append goes through one writer guarded by a
``threading.RLock`` (sync path) and an ``asyncio.Lock`` (async path). ``seq`` is
assigned inside the lock, committed only after the line is fsynced, and never
reused: no two events share a seq, which is what makes CLAIM races decidable
(first claim wins by seq). A failed write does not burn a seq.

**Durable append.** Each event is one JSON line in
``data/fleet/bus-YYYYMMDD.jsonl``, written ``write`` → ``flush`` → ``os.fsync``
before :meth:`FleetBus.append` returns. Daily rotation opens the next segment on
the first append of a new day; a torn last line (crash mid-write) is terminated
on reopen so the next append starts on a clean line.

**Cursors, not queues.** An agent's mailbox is its cursor: *pending = events
where ``seq > cursor`` and addressed to me*. Reads scan the log, so
SLEEPING/PAUSED agents accumulate nothing in memory and lose nothing on restart.

**Snapshot + replay.** :meth:`FleetBus.snapshot` atomically writes
``data/fleet/snapshot.json`` (``last_seq`` + ``taken_at`` + ``cursors`` +
``executed`` + pluggable ``state``), automatically every
``SNAPSHOT_EVERY_EVENTS`` events or ``SNAPSHOT_INTERVAL_S`` seconds.
:meth:`FleetBus.load` returns ``(snapshot, tail)`` with tail = events after
``last_seq``, so boot is snapshot + replay and replay time is bounded by the
snapshot cadence, not by log age.

**Idempotency.** :meth:`FleetBus.append` dedups on the event ``id`` (a retry
returns the original event/seq and writes nothing) and
:meth:`FleetBus.mark_executed` / :meth:`FleetBus.is_executed` track
``executed_task_ids``, so a replayed side effect is never executed twice.

**Rotation & retention.** :meth:`FleetBus.prune_logs` archives (or deletes)
segments older than the newest snapshot plus a grace period (§6/§13).

Deliberate deviations from §6 (documented, not accidental)
---------------------------------------------------------
* The snapshot carries ``schema_version`` (§13 requires it on every persisted
  file) and ``executed`` (agent id → task ids) next to ``cursors``, because §6
  puts ``executed_task_ids`` on the agent and it must survive a restart together
  with the cursor.
* ``content`` accepts any JSON-serialisable payload rather than only a string,
  so ``claim`` / ``gate_*`` / ``state_changed`` events can carry structured data
  without inventing a second envelope field.
* Non-canonical kinds are accepted (logged at debug): §5/§3 emit dotted
  sub-kinds (``agent.spawned``, ``agent.updated``) alongside the §6 kind list.
* Reads skip a torn/unparseable line with a warning instead of failing to boot,
  and report the max parseable ``seq``; a duplicate seq in the log is warned
  about and the writer resumes above the highest one.
* ``id`` dedup is bounded by the retained log window: an id whose segment was
  already pruned (pre-snapshot) cannot be resolved and is re-appended with a
  warning instead of silently claiming a seq for a payload that is gone.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Any

from core.atomic_io import atomic_write_json, read_json
from core.config import config
from core.log import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Event kinds — SPEC §6, exact list and order.
# --------------------------------------------------------------------------- #

USER_TASK = "user_task"
DM = "dm"
BROADCAST = "broadcast"
PROPOSE = "propose"
CLAIM = "claim"
SUBTASK = "subtask"
RESULT = "result"
REVIEW = "review"
SYNTHESIS = "synthesis"
STATE_CHANGED = "state_changed"
GATE_PENDING = "gate_pending"
GATE_RESOLVED = "gate_resolved"
MISSION_OPENED = "mission_opened"
MISSION_TERMINATED = "mission_terminated"
BUDGET_WARNING = "budget_warning"
ALERT = "alert"
SYSTEM = "system"

#: Every canonical kind, in spec order.
KINDS: tuple[str, ...] = (
    USER_TASK,
    DM,
    BROADCAST,
    PROPOSE,
    CLAIM,
    SUBTASK,
    RESULT,
    REVIEW,
    SYNTHESIS,
    STATE_CHANGED,
    GATE_PENDING,
    GATE_RESOLVED,
    MISSION_OPENED,
    MISSION_TERMINATED,
    BUDGET_WARNING,
    ALERT,
    SYSTEM,
)

#: Kinds that are fleet-wide by definition: every agent (except the sender) sees
#: them even when ``target`` names a single agent — the orchestration and
#: observability control plane (§7 protocol, §9 dashboard projection).
BROADCAST_KINDS: frozenset[str] = frozenset(
    {
        BROADCAST,
        PROPOSE,
        ALERT,
        SYSTEM,
        STATE_CHANGED,
        MISSION_OPENED,
        MISSION_TERMINATED,
        BUDGET_WARNING,
        GATE_PENDING,
        GATE_RESOLVED,
    }
)

#: Priorities (spec §6: ``1-4``), aligned with ``agents.messaging.MessagePriority``.
PRIORITY_LOW = 1
PRIORITY_NORMAL = 2
PRIORITY_HIGH = 3
PRIORITY_URGENT = 4
DEFAULT_PRIORITY = PRIORITY_NORMAL

#: Default snapshot cadence (§6: "every ~10k events or 1h"). ``0`` disables a trigger.
SNAPSHOT_EVERY_EVENTS = 10_000
SNAPSHOT_INTERVAL_S = 3600.0

#: Retention: segments older than the newest snapshot minus this grace are prunable (§13).
RETENTION_GRACE_DAYS = 1.0

#: Schema version stamped into every snapshot (§13, migration-friendly).
SCHEMA_VERSION = 1

LOG_PREFIX = "bus-"
LOG_SUFFIX = ".jsonl"
SNAPSHOT_NAME = "snapshot.json"
ARCHIVE_DIR = "archive"
DAY_FORMAT = "%Y%m%d"

#: Pluggable snapshot payload (registry/agent state); must return a JSON-able dict.
StateProvider = Callable[[], dict[str, Any]]
Clock = Callable[[], datetime]
Monotonic = Callable[[], float]


@dataclass(slots=True)
class FleetEvent:
    """One bus event — exactly the SPEC §6 envelope."""

    seq: int = 0
    id: str = ""
    ts: str = ""
    sender: str = ""
    target: str | None = None
    kind: str = SYSTEM
    priority: int = DEFAULT_PRIORITY
    content: Any = ""
    reply_to: str | None = None
    mission_id: str | None = None
    trace_id: str | None = None
    est_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Envelope as a plain dict, in spec field order (JSONL + API shape)."""
        return {
            "seq": self.seq,
            "id": self.id,
            "ts": self.ts,
            "sender": self.sender,
            "target": self.target,
            "kind": self.kind,
            "priority": self.priority,
            "content": self.content,
            "reply_to": self.reply_to,
            "mission_id": self.mission_id,
            "trace_id": self.trace_id,
            "est_tokens": self.est_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FleetEvent:
        """Rebuild an envelope from a log line / snapshot payload."""
        return cls(
            seq=int(data.get("seq") or 0),
            id=str(data.get("id") or ""),
            ts=str(data.get("ts") or ""),
            sender=str(data.get("sender") or ""),
            target=_opt_str(data.get("target")),
            kind=str(data.get("kind") or SYSTEM),
            priority=int(data.get("priority") or DEFAULT_PRIORITY),
            content=data.get("content", ""),
            reply_to=_opt_str(data.get("reply_to")),
            mission_id=_opt_str(data.get("mission_id")),
            trace_id=_opt_str(data.get("trace_id")),
            est_tokens=int(data.get("est_tokens") or 0),
        )


def is_addressed_to(agent_id: str, event: FleetEvent) -> bool:
    """Is ``event`` part of ``agent_id``'s mailbox (pending set)?

    Addressed means ``target == agent_id``, a null target (spec: null =
    broadcast) or a fleet-wide kind (:data:`BROADCAST_KINDS`). An agent never
    re-reads a fleet-wide message it sent itself — it already knows it.
    """
    fleet_wide = event.target is None or event.kind in BROADCAST_KINDS
    if event.sender == agent_id and fleet_wide:
        return False
    return event.target == agent_id or fleet_wide


def _opt_str(value: Any) -> str | None:
    """Normalise an optional envelope string: blank / ``*`` / null → ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "*":
        return None
    return text


def _default_clock() -> datetime:
    return datetime.now().astimezone()


def _empty_state() -> dict[str, Any]:
    return {}


class FleetBus:
    """Durable event-sourced bus for the persistent fleet (SPEC §6).

    Sync core + async wrappers (repo style, mirroring ``core.multi_key``): every
    method works from sync code; the ``a*`` twins run the same core through
    :func:`asyncio.to_thread` under a per-loop :class:`asyncio.Lock`, so async
    callers get FIFO ordering on top of the single-writer guarantee.

    Parameters
    ----------
    base_dir:
        Directory holding ``bus-YYYYMMDD.jsonl`` segments and ``snapshot.json``.
        Defaults to ``data/fleet``.
    state_provider:
        Callable returning the pluggable snapshot payload (registry/agent state,
        §6 "full registry+agent state"). Defaults to ``{}``.
    snapshot_every_events / snapshot_interval_s:
        Auto-snapshot cadence (§6: ~10k events or 1h). ``0`` disables that trigger.
    fsync:
        fsync each appended line (default). Disable only for throwaway buses.
    clock / monotonic:
        Injectable time sources — wall clock for ``ts`` + daily rotation,
        monotonic for the interval cadence.
    """

    def __init__(
        self,
        base_dir: str | os.PathLike[str] | None = None,
        *,
        state_provider: StateProvider | None = None,
        snapshot_every_events: int = SNAPSHOT_EVERY_EVENTS,
        snapshot_interval_s: float = SNAPSHOT_INTERVAL_S,
        fsync: bool = True,
        clock: Clock | None = None,
        monotonic: Monotonic | None = None,
    ) -> None:
        resolved = Path(base_dir) if base_dir is not None else config.resolve_path("data/fleet")
        self.base_dir = resolved
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = self.base_dir / SNAPSHOT_NAME
        self.archive_dir = self.base_dir / ARCHIVE_DIR

        self._state_provider: StateProvider = state_provider or _empty_state
        self._snapshot_every_events = max(0, int(snapshot_every_events))
        self._snapshot_interval_s = max(0.0, float(snapshot_interval_s))
        self._fsync = bool(fsync)
        self._clock: Clock = clock or _default_clock
        self._monotonic: Monotonic = monotonic or time.monotonic

        # One writer: the RLock serialises the sync path (threads), the asyncio
        # lock just sequences async callers before they enter the sync core.
        self._lock = threading.RLock()
        self._alock: asyncio.Lock | None = None
        self._alock_loop: asyncio.AbstractEventLoop | None = None

        self._seq = 0
        self._id_index: dict[str, int] = {}
        self._cursors: dict[str, int] = {}
        self._executed: dict[str, set[str]] = {}
        self._snapshot: dict[str, Any] = {}
        self._last_snapshot_seq = 0
        self._last_snapshot_at = self._monotonic()

        self._handle: IO[str] | None = None
        self._handle_day: str | None = None

        self._restore()

    # ---------- introspection ----------

    @property
    def last_seq(self) -> int:
        """Highest committed ``seq`` (the writer's total order position)."""
        with self._lock:
            return self._seq

    def log_paths(self) -> list[Path]:
        """Log segments in chronological order (``bus-YYYYMMDD.jsonl``)."""
        return sorted(self.base_dir.glob(f"{LOG_PREFIX}*{LOG_SUFFIX}"))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FleetBus(base_dir={str(self.base_dir)!r}, last_seq={self.last_seq})"

    def __enter__(self) -> FleetBus:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------- boot: snapshot + log index ----------

    def _restore(self) -> None:
        """Rebuild durable state: snapshot (cursors/state) + id index from the log.

        Boot is *snapshot + replay*: cursors/executed/state come from the newest
        snapshot, and the writer resumes above the highest ``seq`` in the retained
        segments. The tail itself is not held in memory — ``read``/``load`` stream
        it from disk so SLEEPING agents cost nothing.
        """
        snapshot = self._read_snapshot_file()
        self._snapshot = snapshot
        self._last_snapshot_seq = int(snapshot.get("last_seq") or 0)
        for agent_id, cursor in (snapshot.get("cursors") or {}).items():
            self._cursors[str(agent_id)] = int(cursor or 0)
        for agent_id, task_ids in (snapshot.get("executed") or {}).items():
            self._executed[str(agent_id)] = {str(task) for task in (task_ids or [])}

        max_seq = self._last_snapshot_seq
        previous = 0
        for path in self.log_paths():
            for event in self._iter_file(path):
                if event.id:
                    self._id_index.setdefault(event.id, event.seq)
                if event.seq <= previous:
                    logger.warning(
                        "[FleetBus] duplicate/non-monotonic seq in %s: %s after %s",
                        path.name,
                        event.seq,
                        previous,
                    )
                    previous = max(previous, event.seq)
                else:
                    previous = event.seq
                max_seq = max(max_seq, event.seq)
        self._seq = max_seq

    def _read_snapshot_file(self) -> dict[str, Any]:
        """Snapshot document, or ``{}`` when missing/unreadable (log is the truth)."""
        data = read_json(self.snapshot_path, default=None)
        if not isinstance(data, dict):
            if self.snapshot_path.exists():
                logger.warning("[FleetBus] snapshot %s is unreadable — booting from the log alone", self.snapshot_path.name)
            return {}
        return data

    def _segment_path(self, day: str) -> Path:
        return self.base_dir / f"{LOG_PREFIX}{day}{LOG_SUFFIX}"

    def _iter_file(self, path: Path) -> Iterator[FleetEvent]:
        """Parse one segment; a torn/unreadable line is skipped, never fatal."""
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError:
            logger.warning("[FleetBus] cannot read segment %s", path.name)
            return
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = FleetEvent.from_dict(json.loads(line))
                except Exception:
                    logger.warning("[FleetBus] skipping unreadable line in %s", path.name)
                    continue
                yield event

    def _iter_events(self, after_seq: int) -> Iterator[FleetEvent]:
        """Stream committed events with ``seq > after_seq`` in seq order."""
        for path in self.log_paths():
            for event in self._iter_file(path):
                if event.seq > after_seq:
                    yield event

    # ---------- reads (cursors, not queues) ----------

    def tail(self, after_seq: int = 0, limit: int | None = None, *, kind: str | None = None) -> list[FleetEvent]:
        """Raw log read in seq order — the replay/projection primitive (§9)."""
        if limit is not None and limit <= 0:
            return []
        out: list[FleetEvent] = []
        for event in self._iter_events(after_seq):
            if kind is not None and event.kind != kind:
                continue
            out.append(event)
            if limit is not None and len(out) >= limit:
                break
        return out

    def read(self, agent_id: str, after_seq: int = 0, limit: int | None = None) -> list[FleetEvent]:
        """Cursor read: pending mail for ``agent_id`` after ``after_seq``.

        Addressed = ``target == agent_id``, null target (spec: null = broadcast)
        or a fleet-wide kind (:data:`BROADCAST_KINDS`); the agent's own
        fleet-wide messages are skipped. The caller decides when to
        :meth:`set_cursor` — reads never mutate the cursor implicitly.
        """
        if limit is not None and limit <= 0:
            return []
        out: list[FleetEvent] = []
        for event in self._iter_events(after_seq):
            if not is_addressed_to(agent_id, event):
                continue
            out.append(event)
            if limit is not None and len(out) >= limit:
                break
        return out

    def get(self, event_id: str) -> FleetEvent | None:
        """Fetch one event by ``id`` (idempotency lookups; log scan, rare path)."""
        wanted = str(event_id)
        seq = self._id_index.get(wanted)
        if seq is None:
            return None
        for event in self._iter_events(seq - 1):
            if event.id == wanted:
                return event
            if event.seq > seq:
                break
        return None

    # ---------- the single writer ----------

    def append(
        self,
        *,
        sender: str,
        kind: str,
        content: Any = "",
        target: str | None = None,
        priority: int = DEFAULT_PRIORITY,
        reply_to: str | None = None,
        mission_id: str | None = None,
        trace_id: str | None = None,
        est_tokens: int = 0,
        event_id: str | None = None,
        ts: str | None = None,
    ) -> FleetEvent:
        """Append one event and return the durable §6 envelope.

        ``seq`` is assigned by the single writer, the line is fsynced before this
        returns, and a repeated ``event_id`` returns the original event without
        writing a second line (idempotency, §6 A4).
        """
        sender_id = str(sender or "").strip()
        if not sender_id:
            raise ValueError("sender must be a non-empty agent id")
        kind_name = str(kind or "").strip()
        if not kind_name:
            raise ValueError("kind must be a non-empty string")
        if kind_name not in KINDS:
            # §5/§3 emit dotted sub-kinds (agent.spawned, agent.updated); accepted
            # so the registry can build on this contract without a second bus.
            logger.debug("[FleetBus] non-canonical kind %r", kind_name)
        prio = int(priority)
        if not PRIORITY_LOW <= prio <= PRIORITY_URGENT:
            raise ValueError(f"priority must be {PRIORITY_LOW}-{PRIORITY_URGENT}, got {prio}")
        tokens = int(est_tokens)
        if tokens < 0:
            raise ValueError("est_tokens must be >= 0")
        new_id = str(event_id or uuid.uuid4()).strip()
        if not new_id:
            raise ValueError("event_id must be a non-empty string")

        now = self._clock()
        event = FleetEvent(
            seq=0,
            id=new_id,
            ts=str(ts) if ts else now.isoformat(),
            sender=sender_id,
            target=_opt_str(target),
            kind=kind_name,
            priority=prio,
            content=content,
            reply_to=_opt_str(reply_to),
            mission_id=_opt_str(mission_id),
            trace_id=_opt_str(trace_id),
            est_tokens=tokens,
        )

        with self._lock:
            existing_seq = self._id_index.get(new_id)
            if existing_seq is not None:
                existing = self.get(new_id)
                if existing is not None:
                    if (existing.kind, existing.sender, existing.target) != (event.kind, event.sender, event.target):
                        logger.warning(
                            "[FleetBus] event id %s reused with a different payload — keeping the first append (seq %s)",
                            new_id,
                            existing_seq,
                        )
                    return existing
                logger.warning(
                    "[FleetBus] event id %s was indexed at seq %s but its segment is gone (pruned) — appending fresh",
                    new_id,
                    existing_seq,
                )
            self._ensure_handle_locked(now)
            event.seq = self._seq + 1
            try:
                self._write_locked(event)
            except BaseException:
                # Commit nothing on a failed write: the seq stays free, so the
                # total order is gapless and the caller can retry.
                event.seq = 0
                raise
            self._seq = event.seq
            self._id_index[new_id] = event.seq
            self._maybe_autosnapshot_locked()
            return event

    def _ensure_handle_locked(self, now: datetime) -> None:
        """Open (and rotate) the daily segment; heal a torn tail from a crash."""
        day = now.strftime(DAY_FORMAT)
        if self._handle is not None and self._handle_day == day:
            return
        if self._handle is not None:
            self._close_handle_locked()
        path = self._segment_path(day)
        self._handle = path.open("a", encoding="utf-8")
        self._handle_day = day
        self._repair_torn_tail_locked(path)
        logger.debug("[FleetBus] writing segment %s", path.name)

    def _repair_torn_tail_locked(self, path: Path) -> None:
        """Terminate a partial last line so the next append starts on its own line.

        A crash between ``write`` and ``fsync`` can leave a half-written line; the
        next append would otherwise be concatenated onto it and be lost inside an
        unparseable line.
        """
        if self._handle is None:
            return
        try:
            if path.stat().st_size == 0:
                return
            with path.open("rb") as reader:
                reader.seek(-1, os.SEEK_END)
                if reader.read(1) == b"\n":
                    return
        except OSError:  # pragma: no cover - unreadable segment
            return
        self._handle.write("\n")
        logger.warning("[FleetBus] repaired a torn log tail in %s", path.name)

    def _write_locked(self, event: FleetEvent) -> None:
        """One JSON line, flushed and fsynced — the durability point."""
        handle = self._handle
        if handle is None:  # pragma: no cover - defensive (opened by _ensure_handle_locked)
            raise RuntimeError("fleet bus log handle is not open")
        handle.write(json.dumps(event.to_dict(), default=str, ensure_ascii=False) + "\n")
        handle.flush()
        if self._fsync:
            os.fsync(handle.fileno())

    def _close_handle_locked(self) -> None:
        handle, self._handle, self._handle_day = self._handle, None, None
        if handle is None:
            return
        try:
            handle.flush()
            if self._fsync:
                os.fsync(handle.fileno())
        except OSError:
            logger.warning("[FleetBus] flush on close failed")
        try:
            handle.close()
        except OSError:  # pragma: no cover - close is best-effort
            pass

    def close(self) -> None:
        """Flush + close the active segment (idempotent; cursors stay in memory)."""
        with self._lock:
            self._close_handle_locked()

    # ---------- cursors (the mailbox) ----------

    def get_cursor(self, agent_id: str) -> int:
        """The agent's read position — pending mail is ``read(agent, cursor)``."""
        with self._lock:
            return self._cursors.get(str(agent_id), 0)

    def set_cursor(self, agent_id: str, seq: int, *, persist: bool = False) -> int:
        """Move the agent's cursor; ``persist=True`` snapshots it immediately.

        Rewinding is allowed (at-least-once redelivery is covered by idempotency)
        but warned about, since it is usually a bug. Moving past the log is
        rejected — a cursor can never point into the future.
        """
        key = str(agent_id)
        if not key:
            raise ValueError("agent_id must be a non-empty string")
        value = int(seq)
        if value < 0:
            raise ValueError("cursor must be >= 0")
        with self._lock:
            if value > self._seq:
                raise ValueError(f"cursor {value} is ahead of the log (last_seq={self._seq})")
            previous = self._cursors.get(key, 0)
            if value < previous:
                logger.warning("[FleetBus] cursor for %s moved backwards (%s → %s)", key, previous, value)
            self._cursors[key] = value
            self._maybe_autosnapshot_locked()
            if persist:
                self.snapshot()
            return value

    def cursors(self) -> dict[str, int]:
        """Snapshot of every persisted cursor (dashboard/registry introspection)."""
        with self._lock:
            return dict(self._cursors)

    # ---------- idempotency: executed task ids (§6 A4) ----------

    def mark_executed(self, agent_id: str, task_id: str) -> bool:
        """Record that ``agent_id`` executed ``task_id``.

        Returns ``True`` the first time, ``False`` when it was already recorded —
        the caller must not run the side effect again in that case.
        """
        agent, task = str(agent_id), str(task_id)
        if not agent or not task:
            raise ValueError("agent_id and task_id must be non-empty strings")
        with self._lock:
            bucket = self._executed.setdefault(agent, set())
            if task in bucket:
                return False
            bucket.add(task)
            return True

    def is_executed(self, agent_id: str, task_id: str) -> bool:
        """Has ``agent_id`` already executed ``task_id`` (per snapshot + memory)?"""
        with self._lock:
            return str(task_id) in self._executed.get(str(agent_id), set())

    # ---------- snapshot + replay ----------

    def snapshot(self) -> dict[str, Any]:
        """Atomically persist the current state at ``last_seq`` (§6).

        Shape: ``{schema_version, last_seq, taken_at, cursors, executed, state}``
        where ``state`` is the pluggable :class:`StateProvider` payload (registry
        + agent state in roadmap step 1). A failing ``state_provider`` propagates:
        writing a snapshot without the registry state would silently shrink the
        fleet on the next boot.
        """
        with self._lock:
            state = self._state_provider() or {}
            payload: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "last_seq": self._seq,
                "taken_at": self._clock().isoformat(),
                "cursors": dict(sorted(self._cursors.items())),
                "executed": {agent: sorted(tasks) for agent, tasks in sorted(self._executed.items())},
                "state": state,
            }
            atomic_write_json(self.snapshot_path, payload)
            self._snapshot = payload
            self._last_snapshot_seq = self._seq
            self._last_snapshot_at = self._monotonic()
            return payload

    def maybe_snapshot(self, *, force: bool = False) -> dict[str, Any] | None:
        """Snapshot when the cadence is due (``force=True`` always snapshots)."""
        with self._lock:
            if force:
                return self.snapshot()
            return self._maybe_autosnapshot_locked()

    def _maybe_autosnapshot_locked(self) -> dict[str, Any] | None:
        """Cadence trigger: every ``snapshot_every_events`` or ``snapshot_interval_s``."""
        due_events = self._snapshot_every_events > 0 and (self._seq - self._last_snapshot_seq) >= self._snapshot_every_events
        due_time = self._snapshot_interval_s > 0 and (self._monotonic() - self._last_snapshot_at) >= self._snapshot_interval_s
        if not (due_events or due_time):
            return None
        try:
            return self.snapshot()
        except Exception as exc:
            # The log stays the truth, so a lost snapshot only lengthens replay.
            logger.warning("[FleetBus] automatic snapshot failed: %s", exc)
            return None

    def load(self) -> tuple[dict[str, Any], list[FleetEvent]]:
        """Boot view: ``(snapshot, tail events with seq > snapshot.last_seq)``.

        Non-mutating — :meth:`__init__` performs the same read and adopts cursors,
        executed-task ids, the pluggable state and the writer's ``seq``. With no
        snapshot yet this returns the empty snapshot (``last_seq=0``,
        ``taken_at=None``) plus the whole log, so callers never handle ``None``.
        """
        snapshot = self._read_snapshot_file()
        if not snapshot:
            snapshot = {
                "schema_version": SCHEMA_VERSION,
                "last_seq": 0,
                "taken_at": None,
                "cursors": {},
                "executed": {},
                "state": {},
            }
        last_seq = int(snapshot.get("last_seq") or 0)
        return copy.deepcopy(snapshot), self.tail(after_seq=last_seq)

    # ---------- rotation & retention ----------

    def prune_logs(
        self,
        *,
        grace_days: float = RETENTION_GRACE_DAYS,
        delete: bool = False,
        dry_run: bool = False,
    ) -> list[Path]:
        """Archive (or delete) segments older than the newest snapshot + grace.

        A segment is eligible only when its date is *before* the snapshot's date
        minus ``grace_days``, so the replay tail (``seq > snapshot.last_seq``) and
        the active segment are never removed. Without a snapshot nothing is
        eligible: retention never guesses (``0`` grace keeps the snapshot's day).
        """
        snapshot = self._read_snapshot_file()
        taken_at = snapshot.get("taken_at")
        if not taken_at:
            logger.warning("[FleetBus] retention skipped: no snapshot in %s", self.base_dir)
            return []
        try:
            snapshot_at = datetime.fromisoformat(str(taken_at))
        except ValueError:
            logger.warning("[FleetBus] retention skipped: unreadable taken_at %r", taken_at)
            return []
        cutoff = (snapshot_at - timedelta(days=max(0.0, float(grace_days)))).strftime(DAY_FORMAT)

        affected: list[Path] = []
        for path in self.log_paths():
            day = path.name[len(LOG_PREFIX) : -len(LOG_SUFFIX)]
            if len(day) != 8 or not day.isdigit():
                continue
            if self._handle_day is not None and day == self._handle_day:
                continue
            if day >= cutoff:
                continue
            affected.append(path)
            if dry_run:
                continue
            if delete:
                path.unlink(missing_ok=True)
            else:
                self.archive_dir.mkdir(parents=True, exist_ok=True)
                target = self.archive_dir / path.name
                if target.exists():
                    target = self.archive_dir / f"{path.name}.{int(time.time())}"
                shutil.move(str(path), str(target))
        if affected and not dry_run:
            logger.info("[FleetBus] retention %s %s segment(s)", "deleted" if delete else "archived", len(affected))
        return affected

    # ---------- async wrappers (same sync core, per-loop lock) ----------

    def _async_lock(self) -> asyncio.Lock:
        """A lock bound to the running loop (the sync core is thread-safe itself)."""
        loop = asyncio.get_running_loop()
        if self._alock is None or self._alock_loop is not loop:
            self._alock = asyncio.Lock()
            self._alock_loop = loop
        return self._alock

    async def aappend(self, **kwargs: Any) -> FleetEvent:
        """Async :meth:`append` — FIFO per loop, one writer underneath."""
        async with self._async_lock():
            return await asyncio.to_thread(self.append, **kwargs)

    async def aread(self, agent_id: str, after_seq: int = 0, limit: int | None = None) -> list[FleetEvent]:
        """Async :meth:`read`."""
        async with self._async_lock():
            return await asyncio.to_thread(self.read, agent_id, after_seq, limit)

    async def atail(self, after_seq: int = 0, limit: int | None = None, *, kind: str | None = None) -> list[FleetEvent]:
        """Async :meth:`tail`."""
        async with self._async_lock():
            return await asyncio.to_thread(self.tail, after_seq, limit, kind=kind)

    async def aload(self) -> tuple[dict[str, Any], list[FleetEvent]]:
        """Async :meth:`load` (boot + projection consumers)."""
        async with self._async_lock():
            return await asyncio.to_thread(self.load)

    async def asnapshot(self) -> dict[str, Any]:
        """Async :meth:`snapshot`."""
        async with self._async_lock():
            return await asyncio.to_thread(self.snapshot)

    async def aget_cursor(self, agent_id: str) -> int:
        """Async :meth:`get_cursor`."""
        async with self._async_lock():
            return await asyncio.to_thread(self.get_cursor, agent_id)

    async def aset_cursor(self, agent_id: str, seq: int, *, persist: bool = False) -> int:
        """Async :meth:`set_cursor`."""
        async with self._async_lock():
            return await asyncio.to_thread(self.set_cursor, agent_id, seq, persist=persist)

    async def aclose(self) -> None:
        """Async :meth:`close` (shutdown ordering, §3A9)."""
        async with self._async_lock():
            await asyncio.to_thread(self.close)


# --------------------------------------------------------------------------- #
# Process-wide bus
# --------------------------------------------------------------------------- #

_bus: FleetBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> FleetBus:
    """The process-wide bus at ``data/fleet`` (lazily constructed)."""
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = FleetBus()
        return _bus


def reset_bus() -> None:
    """Close and drop the process-wide bus (shutdown wiring / test isolation)."""
    global _bus
    with _bus_lock:
        if _bus is not None:
            _bus.close()
        _bus = None


__all__ = [
    "ALERT",
    "ARCHIVE_DIR",
    "BROADCAST",
    "BROADCAST_KINDS",
    "BUDGET_WARNING",
    "CLAIM",
    "DEFAULT_PRIORITY",
    "DM",
    "FleetBus",
    "FleetEvent",
    "GATE_PENDING",
    "GATE_RESOLVED",
    "KINDS",
    "LOG_PREFIX",
    "LOG_SUFFIX",
    "MISSION_OPENED",
    "MISSION_TERMINATED",
    "PRIORITY_HIGH",
    "PRIORITY_LOW",
    "PRIORITY_NORMAL",
    "PRIORITY_URGENT",
    "PROPOSE",
    "RESULT",
    "RETENTION_GRACE_DAYS",
    "REVIEW",
    "SCHEMA_VERSION",
    "SNAPSHOT_EVERY_EVENTS",
    "SNAPSHOT_INTERVAL_S",
    "SNAPSHOT_NAME",
    "STATE_CHANGED",
    "SUBTASK",
    "SYNTHESIS",
    "SYSTEM",
    "USER_TASK",
    "get_bus",
    "is_addressed_to",
    "reset_bus",
]
