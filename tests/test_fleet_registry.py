"""Fleet Registry contract — SPEC_PERSISTENT_FLEET §3 + §5 (roadmap step 1).

Pins the lifecycle and durability contract:

* illegal §3 transitions are rejected (``IllegalTransition``), legal ones emit
  ``state_changed`` bus events;
* name uniqueness is case-insensitive; an explicit spawn rejects a taken name
  and auto-suffix (``Friday-2``) is opt-in for internal auto-provisioning;
* WAL-first spawn + boot rebuild: a fresh registry instance on the same bus
  directory restores the roster by snapshot + tail replay (roster cache is
  never authoritative) and a corrupt roster cache never breaks boot;
* idempotent assign (same ``idempotency_key`` → single execution);
* the BLOCKED HITL flow (approve/reject), pause freezing (assign → raise),
  dismiss requiring ``confirm=True``, sleep/wake as real teardown + restore,
  the LRU live cap (auto-sleep), injected-summarizer compaction, and legacy
  import as SLEEPING.

Offline: tmp_path file IO only, stub ``chat_fn``/summarizer injected — no
model backend, no network, no Vault access (the default FreeLLM adapter is
never exercised here).
"""

from __future__ import annotations

import json

import pytest

from core.fleet.bus import STATE_CHANGED, FleetBus
from core.fleet.registry import (
    BLOCKED,
    IDLE,
    SLEEPING,
    WORKING,
    FleetRegistry,
    IllegalTransition,
    RegistryError,
)


# --------------------------------------------------------------------------- #
# Stub work + fixtures (never any network)
# --------------------------------------------------------------------------- #


class StubChat:
    """Deterministic ``chat_fn(messages) -> {"content": str, "tokens": int}``."""

    def __init__(self, content: str = "ok", tokens: int = 5):
        self.calls: list[list[dict]] = []
        self.content = content
        self.tokens = tokens

    def __call__(self, messages):
        self.calls.append([dict(turn) for turn in messages])
        return {"content": f"{self.content}-{len(self.calls)}", "tokens": self.tokens}


class ApprovalChat:
    """Stub that always drafts a result needing human approval (§3 BLOCKED)."""

    def __init__(self, needs_approval: dict | None = None):
        self.calls: list[list[dict]] = []
        self.needs_approval = needs_approval or {"tool": "shell_execute", "reason": "destructive"}

    def __call__(self, messages):
        self.calls.append([dict(turn) for turn in messages])
        return {"content": "draft plan", "tokens": 3, "needs_approval": dict(self.needs_approval)}


def make_registry(tmp_path, chat=None, **kwargs) -> FleetRegistry:
    bus = FleetBus(base_dir=tmp_path / "fleet", fsync=False)
    return FleetRegistry(bus, chat_fn=chat if chat is not None else StubChat(), **kwargs)


def spawn(reg: FleetRegistry, name: str = "Friday", **extra):
    spec = {"name": name, "persona": "the butler", "provider": "groq", "model": "groq/llama-3.3-70b"}
    spec.update(extra)
    return reg.spawn(spec)


# --------------------------------------------------------------------------- #
# §3 state machine
# --------------------------------------------------------------------------- #


def test_illegal_transition_rejected(tmp_path):
    reg = make_registry(tmp_path)
    agent = spawn(reg)
    assert agent.state == IDLE

    # ERROR → IDLE is the only exit from ERROR; IDLE has no self-recovery.
    with pytest.raises(IllegalTransition):
        reg.recover(agent.agent_id)

    # SLEEPING → PAUSED is not in the §3 machine (must wake first).
    reg.sleep(agent.agent_id)
    with pytest.raises(IllegalTransition):
        reg.pause(agent.agent_id)

    # Legal transitions emitted state_changed bus events; the illegal ones did not.
    # spawn folds SPAWNING→IDLE into the single agent.spawned append (WAL-first
    # rule), so the only state_changed here is IDLE→SLEEPING.
    events = reg.bus.tail(kind=STATE_CHANGED)
    transitions = [(e.content["from"], e.content["to"]) for e in events]
    assert transitions == [("IDLE", "SLEEPING")]


def test_name_collision_rejects_unless_suffixing_is_requested(tmp_path):
    """An explicit spawn never hands back an agent under a different name.

    Auto-suffixing used to be the default, so asking for "Friday" silently
    produced "Friday-2" and the caller's claim about the roster stopped matching
    reality. Suffixing is now opt-in for internal auto-provisioning only.
    """
    reg = make_registry(tmp_path)
    first = spawn(reg, "Friday")
    assert first.name == "Friday"

    for taken in ("Friday", "FRIDAY", " friday "):
        with pytest.raises(RegistryError) as exc:
            spawn(reg, taken)
        assert "already exists" in str(exc.value)

    # Internal auto-provisioning still renames, case-folded against the roster.
    second = reg.spawn({"name": "Friday"}, allow_suffix=True)
    third = reg.spawn({"name": "FRIDAY"}, allow_suffix=True)
    assert (second.name, third.name) == ("Friday-2", "Friday-3")

    # update() re-checks uniqueness the same way: a rename onto a taken name is
    # refused rather than quietly suffixed.
    reg.update(first.agent_id, {"name": "winston"})
    with pytest.raises(RegistryError):
        reg.update(second.agent_id, {"name": "WINSTON"})
    assert second.name == "Friday-2", "a refused rename must leave the record alone"

    # Opt-in suffixing still exists for internal callers.
    assert reg.update(second.agent_id, {"name": "WINSTON"}, allow_suffix=True).name == "winston-2"

# --------------------------------------------------------------------------- #
# §3/§5 durability: WAL-first spawn, boot rebuild, corrupt cache
# --------------------------------------------------------------------------- #


def test_spawn_wal_and_boot_rebuild_from_log(tmp_path):
    chat = StubChat()
    reg = make_registry(tmp_path, chat=chat)
    a = spawn(reg, "Friday")
    b = spawn(reg, "Atlas")
    reg.assign(a.agent_id, "do a thing", idempotency_key="tk-1")

    # Fresh bus + registry on the same dir, *no snapshot taken* — the roster
    # must rebuild purely from the replayed log (WAL is the truth).
    reg2 = FleetRegistry(FleetBus(base_dir=tmp_path / "fleet", fsync=False), chat_fn=StubChat())
    reg2.boot()

    assert {agent.name for agent in reg2.list()} == {"Friday", "Atlas"}
    restored = reg2.get(a.agent_id)
    assert restored is not None and restored.state == IDLE
    assert "tk-1" in restored.executed_task_ids
    assert reg2.get(b.agent_id) is not None

    # The dedup survives boot: the same idempotency key does not run again.
    out = reg2.assign(a.agent_id, "do a thing", idempotency_key="tk-1")
    assert out["deduplicated"] is True and out["executed"] is False

    # Snapshot path: checkpoint_all writes state.agents (state_provider hookup),
    # and a third boot restores the same roster from snapshot + tail.
    reg2.checkpoint_all()
    snap = json.loads((tmp_path / "fleet" / "snapshot.json").read_text(encoding="utf-8"))
    assert set(snap["state"]["agents"]) == {a.agent_id, b.agent_id}
    reg3 = FleetRegistry(FleetBus(base_dir=tmp_path / "fleet", fsync=False))
    reg3.boot()
    assert {agent.name for agent in reg3.list()} == {"Friday", "Atlas"}
    assert "tk-1" in reg3.get(a.agent_id).executed_task_ids


def test_corrupt_roster_cache_does_not_break_boot(tmp_path):
    reg = make_registry(tmp_path)
    a = spawn(reg, "Friday")
    cache = tmp_path / "fleet" / "agents" / f"{a.agent_id}.json"
    cache.write_text("{this is not json!!", encoding="utf-8")

    # (a) With bus events present the cache is ignored entirely — boot works.
    reg2 = FleetRegistry(FleetBus(base_dir=tmp_path / "fleet", fsync=False))
    reg2.boot()
    restored = reg2.get(a.agent_id)
    assert restored is not None and restored.name == "Friday"

    # (b) A corrupt cache with an empty bus boots to an empty roster, no crash.
    reg3 = FleetRegistry(FleetBus(base_dir=tmp_path / "fresh", fsync=False), roster_dir=cache.parent)
    reg3.boot()
    assert reg3.list() == []


# --------------------------------------------------------------------------- #
# §5 ops: assign/idempotency, BLOCKED flow, pause, dismiss
# --------------------------------------------------------------------------- #


def test_assign_idempotent_same_key_single_execution(tmp_path):
    chat = StubChat()
    reg = make_registry(tmp_path, chat=chat)
    agent = spawn(reg)

    first = reg.assign(agent.agent_id, "task A", idempotency_key="k1")
    again = reg.assign(agent.agent_id, "task A phrased differently", idempotency_key="k1")

    assert first["executed"] is True
    assert again["deduplicated"] is True and again["executed"] is False
    assert len(chat.calls) == 1  # stub ran exactly once
    assert reg.get(agent.agent_id).stats.tasks_done == 1
    assert reg.get(agent.agent_id).state == IDLE


def test_blocked_flow_approve_and_reject(tmp_path):
    chat = ApprovalChat()
    reg = make_registry(tmp_path, chat=chat)
    agent = spawn(reg)

    out = reg.assign(agent.agent_id, "run the shell thing")
    assert out["blocked"] is True and out["executed"] is False
    blocked = reg.get(agent.agent_id)
    assert blocked.state == BLOCKED
    assert blocked.pending_approval["task_id"] == out["task_id"]

    approved = reg.approve(agent.agent_id)
    assert approved["approved"] is True and approved["executed"] is True
    done = reg.get(agent.agent_id)
    assert done.state == IDLE
    assert done.memory[-1]["role"] == "assistant" and done.memory[-1]["content"] == "draft plan"
    assert done.stats.tasks_done == 1
    assert out["task_id"] in done.executed_task_ids

    # Second BLOCKED cycle resolved by rejection → IDLE, not executed.
    reg.assign(agent.agent_id, "another risky thing")
    assert reg.get(agent.agent_id).state == BLOCKED
    rejected = reg.reject(agent.agent_id)
    assert rejected["rejected"] is True and rejected["executed"] is False
    assert reg.get(agent.agent_id).state == IDLE

    with pytest.raises(IllegalTransition):
        reg.reject(agent.agent_id)  # nothing BLOCKED anymore


def test_pause_freezes_cursor_and_rejects_assign(tmp_path):
    chat = StubChat()
    reg = make_registry(tmp_path, chat=chat)
    agent = spawn(reg)

    reg.pause(agent.agent_id)
    assert reg.get(agent.agent_id).state == "PAUSED"
    with pytest.raises(RegistryError):
        reg.assign(agent.agent_id, "should not run")
    assert len(chat.calls) == 0  # frozen: nothing consumed, nothing executed

    reg.resume(agent.agent_id)
    out = reg.assign(agent.agent_id, "runs now")
    assert out["executed"] is True
    assert len(chat.calls) == 1


def test_dismiss_requires_explicit_confirm(tmp_path):
    reg = make_registry(tmp_path)
    agent = spawn(reg)

    with pytest.raises(RegistryError):
        reg.dismiss(agent.agent_id)
    with pytest.raises(RegistryError):
        reg.dismiss(agent.agent_id, confirm=False)
    assert reg.get(agent.agent_id) is not None  # still here

    reg.dismiss(agent.agent_id, confirm=True)
    assert reg.get(agent.agent_id) is None
    assert reg.list() == []

# --------------------------------------------------------------------------- #
# Sleep = real teardown, LRU cap, compaction, migration
# --------------------------------------------------------------------------- #


def test_sleep_wake_is_real_teardown_and_restore(tmp_path):
    chat = StubChat()
    reg = make_registry(tmp_path, chat=chat)
    agent = spawn(reg)
    reg.assign(agent.agent_id, "t-1", idempotency_key="s1")
    reg.assign(agent.agent_id, "t-2", idempotency_key="s2")
    live = reg.get(agent.agent_id)
    assert live.stats.tasks_done == 2 and len(live.memory) == 4  # user+assistant ×2

    slept = reg.sleep(agent.agent_id)
    assert slept.state == SLEEPING
    assert slept.memory == [] and slept.pending_approval is None  # footprint dropped
    checkpoint = tmp_path / "fleet" / "agents" / f"{agent.agent_id}.checkpoint.json"
    assert checkpoint.exists()

    woken = reg.wake(agent.agent_id)
    assert woken.state == IDLE
    assert len(woken.memory) == 4
    assert woken.stats.tasks_done == 2
    assert {"s1", "s2"} <= woken.executed_task_ids

    # The dedup ledger survives the sleep cycle.
    out = reg.assign(agent.agent_id, "t-1", idempotency_key="s1")
    assert out["deduplicated"] is True


def test_lru_cap_auto_sleeps_beyond_max_live(tmp_path):
    reg = make_registry(tmp_path, max_live=3)
    a = spawn(reg, "A1")
    b = spawn(reg, "A2")
    c = spawn(reg, "A3")
    # Make the LRU order deterministic (same-second timestamps tie otherwise).
    a.last_activity = "2000-01-01T00:00:00+00:00"
    b.last_activity = "2001-01-01T00:00:00+00:00"

    d = spawn(reg, "A4")
    assert reg.get(a.agent_id).state == SLEEPING  # oldest idle agent auto-slept
    assert reg.get(b.agent_id).state == IDLE
    assert reg.get(c.agent_id).state == IDLE
    assert reg.get(d.agent_id).state == IDLE
    assert len(reg.list()) == 4  # slept, not destroyed


def test_compact_folds_old_turns_via_injected_summarizer(tmp_path):
    chat = StubChat()
    reg = make_registry(tmp_path, chat=chat)
    agent = spawn(reg)
    for i in range(5):  # 5 tasks → 10 memory turns
        reg.assign(agent.agent_id, f"t-{i}", idempotency_key=f"c{i}")

    seen: dict[str, list[str]] = {}

    def summarizer(texts: list[str]) -> str:
        seen["texts"] = list(texts)
        return "DIGEST"

    out = reg.compact(agent.agent_id, summarizer)
    assert out["compacted"] is True and out["folded_turns"] == 4
    compacted = reg.get(agent.agent_id)
    assert compacted.summary == "DIGEST"
    assert len(compacted.memory) == 6  # last 6 turns kept raw

    folded_texts = seen["texts"]
    assert any("t-0" in text for text in folded_texts)  # old user turns folded…
    assert all("t-4" not in text for text in folded_texts)  # …recent ones kept raw
    assert [turn["content"] for turn in compacted.memory][-1].startswith("ok-5")

    # Under the keep-limit the summarizer is never invoked.
    reg2 = make_registry(tmp_path / "other")
    fresh = spawn(reg2, "Small")
    reg2.assign(fresh.agent_id, "one turn")
    untouched: dict[str, list[str]] = {}

    def never(texts: list[str]) -> str:
        untouched["texts"] = list(texts)
        return "SHOULD NOT RUN"

    assert reg2.compact(fresh.agent_id, never)["compacted"] is False
    assert "texts" not in untouched


def test_import_legacy_agent_arrives_sleeping(tmp_path):
    reg = make_registry(tmp_path)
    legacy = reg.import_legacy_agent("OldGuard", "legacy persona", "groq/legacy-70b")
    assert legacy.state == SLEEPING  # never live (§13)

    # Illegal to hand work to a SLEEPING import; wake is the legal path.
    with pytest.raises(IllegalTransition):
        reg.assign(legacy.agent_id, "no work for sleepers")
    reg.wake(legacy.agent_id)
    assert reg.get(legacy.agent_id).state == IDLE

    # A second import on a fresh instance also restores as SLEEPING after boot.
    reg2 = make_registry(tmp_path / "other")
    imported2 = reg2.import_legacy_agent("Ancient", "p", "openai/gpt-x", state=WORKING)
    assert imported2.state == SLEEPING  # coerced: imports are never live


