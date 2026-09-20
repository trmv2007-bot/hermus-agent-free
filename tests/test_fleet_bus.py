"""Fleet Bus contract — SPEC_PERSISTENT_FLEET §6 (roadmap step 0b).

Pins the contract everything else is built on:

* the §6 envelope shape and the exact §6 kind list;
* single-writer appends → monotonic, gapless ``seq`` (threads *and* asyncio);
* idempotency: ``id`` dedup (also across a restart) and ``executed_task_ids``;
* cursors, not queues: ``read(agent_id, after_seq, limit)`` filters by target and
  broadcast kinds, cursors persist in the snapshot;
* durability: fsync'd JSONL segments, daily rotation, torn-line tolerance, and
  crash recovery via ``load() == (snapshot, tail)`` on a fresh instance;
* snapshot cadence (every N events / T seconds) and retention (archive vs
  delete) measured against the newest snapshot + grace period.

Offline: tmp_path file IO only, no model backend, no network.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.fleet.bus import (
    BROADCAST,
    BROADCAST_KINDS,
    CLAIM,
    DM,
    KINDS,
    PRIORITY_URGENT,
    PROPOSE,
    RESULT,
    SCHEMA_VERSION,
    STATE_CHANGED,
    SUBTASK,
    SYSTEM,
    USER_TASK,
    FleetBus,
    FleetEvent,
    is_addressed_to,
)

#: The §6 kind list, verbatim (spec order).
SPEC_KINDS = (
    "user_task",
    "dm",
    "broadcast",
    "propose",
    "claim",
    "subtask",
    "result",
    "review",
    "synthesis",
    "state_changed",
    "gate_pending",
    "gate_resolved",
    "mission_opened",
    "mission_terminated",
    "budget_warning",
    "alert",
    "system",
)

SPEC_FIELDS = (
    "seq",
    "id",
    "ts",
    "sender",
    "target",
    "kind",
    "priority",
    "content",
    "reply_to",
    "mission_id",
    "trace_id",
    "est_tokens",
)


class FakeClock:
    """Injectable wall clock — tests move days without monkeypatching ``datetime``."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def set(self, **kwargs: object) -> None:
        self.now = self.now + timedelta(**kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def fleet_dir(tmp_path: Path) -> Path:
    return tmp_path / "fleet"


def _new_bus(fleet_dir: Path, **kwargs: object) -> FleetBus:
    """A bus whose snapshot cadence is parked unless the test asks for one."""
    kwargs.setdefault("snapshot_every_events", 10_000)
    kwargs.setdefault("snapshot_interval_s", 10_000.0)
    return FleetBus(base_dir=fleet_dir, **kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def bus(fleet_dir: Path):
    instance = _new_bus(fleet_dir)
    yield instance
    instance.close()


# --------------------------------------------------------------------------- #
# Contract shape
# --------------------------------------------------------------------------- #


def test_kind_constants_match_spec_section_6():
    assert KINDS == SPEC_KINDS
    assert set(KINDS) >= BROADCAST_KINDS, "fleet-wide kinds must be canonical kinds"
    assert {BROADCAST, PROPOSE, STATE_CHANGED, SYSTEM} <= BROADCAST_KINDS
    assert CLAIM not in BROADCAST_KINDS, "claims are fan-in events, not fleet-wide"


def test_envelope_matches_spec_shape(bus: FleetBus):
    event = bus.append(
        sender="coord",
        kind=USER_TASK,
        target="a1",
        content="build the report",
        priority=PRIORITY_URGENT,
        mission_id="msn_1",
        trace_id="tr_1",
        est_tokens=42,
    )
    assert list(event.to_dict()) == list(SPEC_FIELDS)
    assert (event.seq, event.sender, event.target, event.kind) == (1, "coord", "a1", USER_TASK)
    assert (event.priority, event.content, event.est_tokens) == (4, "build the report", 42)
    assert event.reply_to is None and event.id and event.ts
    assert datetime.fromisoformat(event.ts).tzinfo is not None

    line = bus.log_paths()[0].read_text(encoding="utf-8").strip()
    assert json.loads(line) == event.to_dict()
    assert FleetEvent.from_dict(json.loads(line)) == event


def test_broadcast_targets_are_normalised_to_null(bus: FleetBus):
    event = bus.append(sender="coord", kind=BROADCAST, target="*", content="all hands")
    assert event.target is None  # spec §6: target "agent_id|null"; null = broadcast


def test_non_canonical_kinds_are_accepted_for_the_registry(bus: FleetBus):
    # §5/§3 emit dotted sub-kinds (agent.spawned / agent.updated) on this bus.
    event = bus.append(sender="registry", kind="agent.spawned", content={"agent_id": "a1"})
    assert (event.kind, event.seq) == ("agent.spawned", 1)
    assert event.content == {"agent_id": "a1"}, "structured payloads survive the JSONL round-trip"


def test_invalid_envelopes_are_rejected_without_burning_a_seq(bus: FleetBus):
    with pytest.raises(ValueError):
        bus.append(sender="", kind=SYSTEM, content="no sender")
    with pytest.raises(ValueError):
        bus.append(sender="coord", kind=SYSTEM, priority=5)
    with pytest.raises(ValueError):
        bus.append(sender="coord", kind=SYSTEM, est_tokens=-1)
    with pytest.raises(ValueError):
        bus.set_cursor("a1", 3)  # cursor ahead of the log
    assert bus.last_seq == 0
    assert bus.log_paths() == []
    assert bus.append(sender="coord", kind=SYSTEM, content="ok").seq == 1


def test_addressing_rule_is_shared_with_read(bus: FleetBus):
    directed = bus.append(sender="coord", kind=DM, target="a1", content="for a1")
    other = bus.append(sender="coord", kind=DM, target="a2", content="for a2")
    fleet_wide = bus.append(sender="coord", kind=STATE_CHANGED, target="a2", content="a2 is working")
    assert is_addressed_to("a1", directed) is True
    assert is_addressed_to("a1", other) is False
    assert is_addressed_to("a1", fleet_wide) is True
    assert [e.content for e in bus.read("a1")] == ["for a1", "a2 is working"]


# --------------------------------------------------------------------------- #
# Single writer: monotonic, gapless seq
# --------------------------------------------------------------------------- #


def test_seq_is_monotonic_and_gapless(bus: FleetBus):
    events = [bus.append(sender="coord", kind=SYSTEM, content=i) for i in range(25)]
    assert [e.seq for e in events] == list(range(1, 26))
    assert bus.last_seq == 25
    lines = bus.log_paths()[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 25
    assert [json.loads(line)["seq"] for line in lines] == list(range(1, 26))


def test_concurrent_thread_appends_stay_gapless(bus: FleetBus):
    """The threading lock is the single writer: unique, gapless seq under races."""
    per_thread, threads = 30, 6
    errors: list[BaseException] = []
    barrier = threading.Barrier(threads)

    def worker(worker_id: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(per_thread):
                bus.append(sender=f"w{worker_id}", kind=DM, target="coord", content=i)
        except BaseException as exc:  # pragma: no cover - failure surface
            errors.append(exc)

    workers = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join(timeout=60)

    assert errors == []
    events = bus.tail()
    assert len(events) == per_thread * threads
    assert [e.seq for e in events] == list(range(1, per_thread * threads + 1))
    assert len({e.id for e in events}) == per_thread * threads
    assert len(bus.log_paths()[0].read_text(encoding="utf-8").splitlines()) == per_thread * threads


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


def test_append_dedups_on_event_id(bus: FleetBus):
    first = bus.append(sender="coord", kind=SUBTASK, target="a1", content="build", event_id="evt-fixed")
    replay = bus.append(sender="coord", kind=SUBTASK, target="a1", content="build", event_id="evt-fixed")
    assert replay.to_dict() == first.to_dict()
    assert replay.seq == first.seq == 1
    assert bus.last_seq == 1, "a replayed event must not consume a seq"
    assert len(bus.log_paths()[0].read_text(encoding="utf-8").splitlines()) == 1
    assert bus.get("evt-fixed") == first
    assert bus.get("never-seen") is None


def test_id_dedup_survives_restart(fleet_dir: Path):
    bus = _new_bus(fleet_dir)
    bus.append(sender="coord", kind=SUBTASK, target="a1", content="once", event_id="evt-1")
    bus.close()

    resumed = _new_bus(fleet_dir)
    repeat = resumed.append(sender="coord", kind=SUBTASK, target="a1", content="once", event_id="evt-1")
    assert repeat.seq == 1
    assert resumed.last_seq == 1
    assert len(resumed.log_paths()[0].read_text(encoding="utf-8").splitlines()) == 1
    resumed.close()


def test_mark_executed_is_idempotent(bus: FleetBus):
    assert bus.mark_executed("a1", "task-1") is True
    assert bus.mark_executed("a1", "task-1") is False
    assert bus.is_executed("a1", "task-1") is True
    assert bus.is_executed("a1", "task-2") is False
    assert bus.is_executed("a2", "task-1") is False
    with pytest.raises(ValueError):
        bus.mark_executed("", "task-1")


# --------------------------------------------------------------------------- #
# Cursors, not queues
# --------------------------------------------------------------------------- #


def test_read_filters_by_target_and_broadcast_kinds(bus: FleetBus):
    bus.append(sender="coord", kind=DM, target="a1", content="a1-dm")
    bus.append(sender="coord", kind=DM, target="a2", content="a2-dm")
    bus.append(sender="coord", kind=BROADCAST, content="all-hands")
    bus.append(sender="a2", kind=PROPOSE, target="a3", content="pool")
    bus.append(sender="a1", kind=BROADCAST, content="own-broadcast")
    bus.append(sender="coord", kind=CLAIM, target="coord", content="claim")
    bus.append(sender="a2", kind=STATE_CHANGED, target="a2", content="state-a2")
    bus.append(sender="a1", kind=RESULT, target="coord", content="result")

    # addressed = target==me | null target | fleet-wide kind; own fleet-wide skipped
    assert [e.content for e in bus.read("a1")] == ["a1-dm", "all-hands", "pool", "state-a2"]
    assert [e.content for e in bus.read("a2")] == ["a2-dm", "all-hands", "own-broadcast"]
    assert [e.content for e in bus.read("a3")] == ["all-hands", "pool", "own-broadcast", "state-a2"]
    assert [e.content for e in bus.read("coord")] == [
        "pool",
        "own-broadcast",
        "claim",
        "state-a2",
        "result",
    ], "the sender never re-reads its own fleet-wide messages"
    assert [e.content for e in bus.read("nobody")] == ["all-hands", "pool", "own-broadcast", "state-a2"]
    assert [e.content for e in bus.read("a1", limit=2)] == ["a1-dm", "all-hands"]


def test_read_honours_after_seq_and_limit(bus: FleetBus):
    for i in range(10):
        bus.append(sender="coord", kind=DM, target="a1", content=i)
    assert [e.content for e in bus.read("a1", after_seq=7)] == [7, 8, 9]
    assert bus.read("a1", after_seq=10) == []
    assert [e.content for e in bus.read("a1", limit=3)] == [0, 1, 2]
    assert [e.content for e in bus.read("a1", after_seq=4, limit=2)] == [4, 5]
    assert bus.read("a1", limit=0) == []
    assert [e.content for e in bus.tail(after_seq=8, kind=DM)] == [8, 9]
    assert [e.seq for e in bus.tail(after_seq=9)] == [10]


def test_cursor_roundtrip_and_snapshot_persistence(fleet_dir: Path):
    bus = _new_bus(fleet_dir)
    for i in range(5):
        bus.append(sender="coord", kind=DM, target="a1", content=i)
    assert bus.get_cursor("a1") == 0
    assert [e.content for e in bus.read("a1", bus.get_cursor("a1"))] == [0, 1, 2, 3, 4]

    bus.set_cursor("a1", 3)
    assert [e.content for e in bus.read("a1", bus.get_cursor("a1"))] == [3, 4]
    bus.set_cursor("a1", 4, persist=True)
    assert bus.cursors() == {"a1": 4}
    bus.close()

    resumed = _new_bus(fleet_dir)
    assert resumed.get_cursor("a1") == 4, "cursors live in the snapshot, not in a queue"
    assert [e.content for e in resumed.read("a1", resumed.get_cursor("a1"))] == [4]
    resumed.close()


def test_cursor_rewind_is_allowed_but_ahead_is_rejected(bus: FleetBus):
    bus.append(sender="coord", kind=DM, target="a1", content="x")
    bus.set_cursor("a1", 1)
    bus.set_cursor("a1", 0)  # rewind = at-least-once redelivery, covered by idempotency
    assert bus.get_cursor("a1") == 0
    with pytest.raises(ValueError):
        bus.set_cursor("a1", 2)


# --------------------------------------------------------------------------- #
# Snapshot + replay (crash recovery)
# --------------------------------------------------------------------------- #


def test_crash_recovery_rebuilds_cursors_state_and_replays_the_tail(fleet_dir: Path):
    state = {"roster": {"a1": {"state": "WORKING", "cursor": 5}}}
    first = _new_bus(fleet_dir, state_provider=lambda: state)
    for i in range(5):
        first.append(sender="coord", kind=USER_TASK, target="a1", content=f"job-{i}", trace_id="tr-1")
    first.set_cursor("a1", 5, persist=True)
    assert first.mark_executed("a1", "job-0") is True
    snapshot = first.snapshot()
    assert snapshot["last_seq"] == 5
    assert snapshot["schema_version"] == SCHEMA_VERSION
    assert snapshot["cursors"] == {"a1": 5}
    assert snapshot["executed"] == {"a1": ["job-0"]}
    assert snapshot["state"] == state
    assert json.loads(first.snapshot_path.read_text(encoding="utf-8")) == snapshot

    # Crash window: two events committed after the snapshot, never snapshotted.
    first.append(sender="coord", kind=USER_TASK, target="a1", content="job-5")
    first.append(sender="a1", kind=RESULT, target="coord", content="done")
    first.close()

    resumed = _new_bus(fleet_dir, state_provider=lambda: state)
    loaded_snapshot, tail = resumed.load()
    assert loaded_snapshot["last_seq"] == 5
    assert loaded_snapshot["taken_at"] == snapshot["taken_at"]
    assert loaded_snapshot["cursors"] == {"a1": 5}
    assert loaded_snapshot["state"] == state
    assert [e.content for e in tail] == ["job-5", "done"], "boot = snapshot + replay tail"

    assert resumed.get_cursor("a1") == 5
    assert resumed.is_executed("a1", "job-0") is True
    assert resumed.last_seq == 7, "the writer resumes above the highest replayed seq"
    assert [e.content for e in resumed.read("a1", resumed.get_cursor("a1"))] == ["job-5"]
    assert resumed.append(sender="coord", kind=SYSTEM, content="next").seq == 8
    resumed.close()


def test_load_without_snapshot_returns_the_whole_log(fleet_dir: Path):
    bus = _new_bus(fleet_dir)
    bus.append(sender="coord", kind=SYSTEM, content="a")
    bus.append(sender="coord", kind=SYSTEM, content="b")
    snapshot, tail = bus.load()
    assert snapshot == {
        "schema_version": SCHEMA_VERSION,
        "last_seq": 0,
        "taken_at": None,
        "cursors": {},
        "executed": {},
        "state": {},
    }
    assert [e.content for e in tail] == ["a", "b"]
    assert bus.load()[0] is not snapshot, "load returns a copy; callers may not mutate bus state"
    bus.close()


def test_torn_line_is_skipped_and_the_writer_recovers(fleet_dir: Path):
    bus = _new_bus(fleet_dir)
    bus.append(sender="coord", kind=SYSTEM, content="one")
    bus.append(sender="coord", kind=SYSTEM, content="two")
    segment = bus.log_paths()[0]
    bus.close()
    with segment.open("a", encoding="utf-8") as handle:  # crash caught mid-write
        handle.write('{"seq": 3, "id": "torn", "ts": "2026-01-01T00:00:00')

    resumed = _new_bus(fleet_dir)
    assert [e.content for e in resumed.tail()] == ["one", "two"], "a torn tail line never fails boot"
    assert resumed.last_seq == 2
    assert resumed.append(sender="coord", kind=SYSTEM, content="three").seq == 3
    assert [e.content for e in resumed.tail()] == ["one", "two", "three"]
    assert len(segment.read_text(encoding="utf-8").splitlines()) == 4  # torn line healed, not lost
    resumed.close()


# --------------------------------------------------------------------------- #
# Rotation, retention, cadence
# --------------------------------------------------------------------------- #


def test_daily_rotation_creates_a_new_segment(fleet_dir: Path):
    clock = FakeClock(datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc))
    bus = _new_bus(fleet_dir, clock=clock)
    bus.append(sender="coord", kind=SYSTEM, content="day-1")
    clock.set(minutes=2)
    bus.append(sender="coord", kind=SYSTEM, content="day-2a")
    bus.append(sender="coord", kind=SYSTEM, content="day-2b")

    assert [p.name for p in bus.log_paths()] == ["bus-20260101.jsonl", "bus-20260102.jsonl"]
    assert len(bus.log_paths()[0].read_text(encoding="utf-8").splitlines()) == 1
    assert [e.seq for e in bus.tail()] == [1, 2, 3], "seq keeps counting across segments"
    assert [e.content for e in bus.read("a1")] == ["day-1", "day-2a", "day-2b"], "reads span segments"
    bus.set_cursor("a1", 3, persist=True)
    bus.close()

    resumed = _new_bus(fleet_dir, clock=clock)
    assert resumed.last_seq == 3
    assert [e.content for e in resumed.tail()] == ["day-1", "day-2a", "day-2b"]
    assert resumed.get_cursor("a1") == 3
    resumed.close()


def test_retention_archives_then_deletes_old_segments(fleet_dir: Path):
    clock = FakeClock(datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
    bus = _new_bus(fleet_dir, clock=clock)
    for day in range(1, 6):
        clock.now = datetime(2026, 1, day, 12, tzinfo=timezone.utc)
        bus.append(sender="coord", kind=SYSTEM, content=f"day-{day}")
    assert [p.name for p in bus.log_paths()] == [f"bus-2026010{day}.jsonl" for day in range(1, 6)]
    bus.snapshot()

    aged_out = ["bus-20260101.jsonl", "bus-20260102.jsonl", "bus-20260103.jsonl"]
    assert [p.name for p in bus.prune_logs(grace_days=1, dry_run=True)] == aged_out
    assert [p.name for p in bus.log_paths()] == [f"bus-2026010{day}.jsonl" for day in range(1, 6)], "dry run is inert"

    assert [p.name for p in bus.prune_logs(grace_days=1)] == aged_out
    assert [p.name for p in bus.log_paths()] == ["bus-20260104.jsonl", "bus-20260105.jsonl"]
    assert sorted(p.name for p in bus.archive_dir.iterdir()) == aged_out, "archive is the default, not deletion"
    assert [e.content for e in bus.tail()] == ["day-4", "day-5"], "the snapshot covers the archived prefix"

    deleted = bus.prune_logs(grace_days=0, delete=True)
    assert [p.name for p in deleted] == ["bus-20260104.jsonl"]
    assert not (bus.archive_dir / "bus-20260104.jsonl").exists()
    assert [p.name for p in bus.log_paths()] == ["bus-20260105.jsonl"], "the active segment is never pruned"
    bus.close()


def test_retention_never_guesses_without_a_snapshot(fleet_dir: Path):
    clock = FakeClock(datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
    first = _new_bus(fleet_dir, clock=clock)
    first.append(sender="coord", kind=SYSTEM, content="old")
    first.close()

    clock.now = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
    bus = _new_bus(fleet_dir, clock=clock)
    bus.append(sender="coord", kind=SYSTEM, content="new")
    assert not bus.snapshot_path.exists()
    assert bus.prune_logs(grace_days=0) == []
    assert [p.name for p in bus.log_paths()] == ["bus-20260101.jsonl", "bus-20260301.jsonl"]
    bus.close()


def test_snapshot_cadence_triggers_every_n_events(fleet_dir: Path):
    bus = _new_bus(fleet_dir, snapshot_every_events=5)
    for i in range(4):
        bus.append(sender="coord", kind=SYSTEM, content=i)
    assert not bus.snapshot_path.exists()

    bus.append(sender="coord", kind=SYSTEM, content=4)
    assert json.loads(bus.snapshot_path.read_text(encoding="utf-8"))["last_seq"] == 5

    for i in range(5, 9):
        bus.append(sender="coord", kind=SYSTEM, content=i)
    assert json.loads(bus.snapshot_path.read_text(encoding="utf-8"))["last_seq"] == 5, "cadence restarts"
    bus.append(sender="coord", kind=SYSTEM, content=9)
    assert json.loads(bus.snapshot_path.read_text(encoding="utf-8"))["last_seq"] == 10
    bus.close()


def test_snapshot_cadence_triggers_on_the_interval(fleet_dir: Path):
    tick = {"t": 1_000.0}
    bus = _new_bus(fleet_dir, snapshot_interval_s=3600.0, monotonic=lambda: tick["t"])
    bus.append(sender="coord", kind=SYSTEM, content="first")
    assert not bus.snapshot_path.exists(), "the 1h interval has not elapsed"
    assert bus.maybe_snapshot() is None

    tick["t"] += 3_600.0
    bus.append(sender="coord", kind=SYSTEM, content="second")
    assert json.loads(bus.snapshot_path.read_text(encoding="utf-8"))["last_seq"] == 2
    assert bus.maybe_snapshot(force=True)["last_seq"] == 2
    bus.close()


# --------------------------------------------------------------------------- #
# Async wrappers
# --------------------------------------------------------------------------- #


def test_async_wrappers_share_the_single_writer(fleet_dir: Path):
    bus = _new_bus(fleet_dir)

    async def scenario() -> dict[str, object]:
        appended = await asyncio.gather(*(bus.aappend(sender=f"w{i % 4}", kind=DM, target="coord", content=i) for i in range(40)))
        return {
            "seqs": sorted(event.seq for event in appended),
            "mail": [event.content for event in await bus.aread("coord")],
            "cursor": await bus.aset_cursor("coord", 40, persist=True),
            "snapshot": await bus.asnapshot(),
            "tail": (await bus.aload())[1],
            "read_tail": await bus.atail(after_seq=39),
            "cursor_now": await bus.aget_cursor("coord"),
        }

    result = asyncio.run(scenario())
    assert result["seqs"] == list(range(1, 41)), "concurrent aappend still yields one gapless seq space"
    assert sorted(result["mail"]) == list(range(40))
    assert result["cursor"] == 40 and result["cursor_now"] == 40
    assert result["snapshot"]["last_seq"] == 40
    assert result["tail"] == []
    assert [event.seq for event in result["read_tail"]] == [40]

    bus.close()
    resumed = _new_bus(fleet_dir)
    assert resumed.get_cursor("coord") == 40, "the async cursor persisted through the snapshot"
    resumed.close()
