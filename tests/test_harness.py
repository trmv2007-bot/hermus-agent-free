"""jcode-inspired harness primitives."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

from core.harness import bus, files, sessions
from core.harness.compaction import compact_messages
from core.harness.swarm import spawn


def test_compaction_triggers_at_90_percent():
    messages = [{"role": "system", "content": "sys"}]
    for _ in range(20):
        messages.append({"role": "user", "content": "Tool results (step 1):\n" + ("x" * 2000)})
    out, report = compact_messages(messages, budget_chars=8_000, keep_recent=4, tool_limit=200)
    assert report["compacted"] is True
    assert report["chars_after"] < report["chars_before"]
    assert any("compacted" in str(m.get("content")) for m in out)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr("core.harness.sessions._dir", lambda: tmp_path / "sessions")
    monkeypatch.setattr("core.harness.bus._path", lambda: tmp_path / "bus.json")
    monkeypatch.setattr("core.harness.files._path", lambda: tmp_path / "filewatch.json")
    (tmp_path / "sessions").mkdir(exist_ok=True)


def test_sessions_are_server_owned(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    rec = sessions.create(task="refactor", role="coordinator")
    assert rec["id"]
    got = sessions.get(rec["id"])
    assert got["task"] == "refactor"
    attached = sessions.attach(rec["id"])
    assert attached["attachments"] >= 1
    listed = sessions.list_sessions()
    assert any(s["id"] == rec["id"] for s in listed)


def test_bus_dm_and_broadcast(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    bus.send("hello worker", "coord", to="w1", kind="dm")
    bus.send("all hands", "coord", kind="broadcast")
    inbox = bus.inbox("w1")
    kinds = {m["kind"] for m in inbox}
    assert "dm" in kinds and "broadcast" in kinds
    n = bus.mark_read("w1")
    assert n >= 1
    assert bus.inbox("w1", unread_only=True) == []


def test_file_changed_under_you(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    target = tmp_path / "shared.py"
    target.write_text("v1\n")
    files.note_read("agent-a", str(target))
    target.write_text("v2\n")
    evs = files.note_write(str(target), writer="agent-b")
    assert evs and evs[0]["type"] == "file_changed_under_you"
    pending = files.pending("agent-a")
    assert pending
    files.ack("agent-a")
    assert files.pending("agent-a") == []


def test_swarm_spawn_registers_workers(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    out = spawn("write tests", "coord-1", count=2)
    assert out["count"] == 2
    assert len(out["workers"]) == 2
    assert sessions.get("coord-1")["role"] == "coordinator"
    mail = bus.inbox(out["workers"][0]["id"])
    assert mail


# ---------------------------------------------------------------------------
# Compaction: budget is real, structure is preserved, input is not mutated
# ---------------------------------------------------------------------------
def _long_turn(final_user: str = "FINAL QUESTION") -> list[dict]:
    messages = [{"role": "system", "content": "SYS " + "s" * 3000}]
    for step in range(8):
        messages.append(
            {
                "role": "assistant",
                "content": f"step {step} reasoning " + "r" * 400,
                "tool_calls": [{"id": f"call_{step}", "type": "function"}],
            }
        )
        messages.append({"role": "tool", "content": f"OBS {step} " + "o" * 3000, "tool_call_id": f"call_{step}"})
    messages.append({"role": "user", "content": final_user})
    return messages


def test_compaction_preserves_tool_pairing_system_and_final_user():
    messages = _long_turn()
    out, report = compact_messages(messages, budget_tokens=1500, keep_recent=3, tool_limit=300)
    assert report["compacted"] is True
    assert report["tokens_after"] < report["tokens_before"]
    # system preamble and the newest user instruction are verbatim
    assert out[0]["content"] == messages[0]["content"]
    assert out[-1]["content"] == "FINAL QUESTION"
    # no orphaned tool result: every tool message still has its assistant call
    for idx, msg in enumerate(out):
        if msg.get("role") == "tool":
            assert idx > 0
            assert out[idx - 1].get("role") in ("assistant", "tool")
            assert out[idx - 1].get("tool_calls") or out[idx - 1].get("role") == "tool"


def test_compaction_is_idempotent_and_does_not_mutate_input():
    messages = _long_turn()
    original = json.dumps(messages, sort_keys=True)
    once, first = compact_messages(messages, budget_tokens=1500, keep_recent=3, tool_limit=300)
    assert first["compacted"] is True
    assert json.dumps(messages, sort_keys=True) == original, "caller trajectory must not be mutated"
    twice, second = compact_messages(once, budget_tokens=1500, keep_recent=3, tool_limit=300)
    assert second["compacted"] is False, "a compacted prompt is already under budget"
    assert [m["content"] for m in twice] == [m["content"] for m in once]


# ---------------------------------------------------------------------------
# Durable store: atomic writes survive concurrent writers
# ---------------------------------------------------------------------------
def test_bus_survives_concurrent_writers(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)

    def writer(n: int) -> None:
        for i in range(5):
            bus.send(f"m{n}-{i}", sender=f"s{n}", to="reader", kind="dm")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw = (tmp_path / "bus.json").read_text(encoding="utf-8")
    data = json.loads(raw)  # never truncated JSON
    assert len(data["messages"]) == 30
    assert len(bus.inbox("reader")) == 30


def test_bus_retention_keeps_unread_mail(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    # Small caps keep the test fast; retention logic is what is under test.
    monkeypatch.setattr(bus, "MAX_MESSAGES", 4)
    monkeypatch.setattr(bus, "MAX_UNREAD", 6)

    for i in range(5):
        bus.send(f"note {i}", sender="writer", to=f"s{i}", kind="dm")
    data = json.loads((tmp_path / "bus.json").read_text(encoding="utf-8"))
    assert len(data["messages"]) == 5, "unread mail is never trimmed below the target"

    for i in range(4):
        bus.send(f"flood {i}", sender="writer", to=f"f{i}", kind="dm")
    data = json.loads((tmp_path / "bus.json").read_text(encoding="utf-8"))
    assert len(data["messages"]) <= bus.MAX_UNREAD
    assert data["messages"], "retention must never empty the bus"

    # Read history is the first thing trimmed once the bus is over target.
    bus.mark_read("s0", [data["messages"][0]["id"]])
    bus.send("after read", sender="writer", to="s9", kind="dm")
    data = json.loads((tmp_path / "bus.json").read_text(encoding="utf-8"))
    assert len(data["messages"]) <= bus.MAX_UNREAD


def test_blanket_mark_read_cannot_delete_another_sessions_mail(tmp_path, monkeypatch):
    """A read by one session must not make another session's DM look consumed."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(bus, "MAX_MESSAGES", 4)
    monkeypatch.setattr(bus, "MAX_UNREAD", 6)

    for i in range(3):
        bus.send(f"for b {i}", sender="A", to="B", kind="dm")
    assert bus.mark_read("C") == 0, "an unrelated session may not mark anyone's mail"

    for i in range(3):
        bus.send(f"for d {i}", sender="A", to="D", kind="dm")
    assert len(bus.inbox("B")) == 3, "B's unread mail survived the trim"
    assert bus.stats()["unread"] >= 6


def test_channel_delivery_requires_subscription(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    bus.send("build is red", sender="worker-1", channel="build")
    assert bus.inbox("listener") == [], "unsubscribed sessions get no channel chatter"

    bus.subscribe("listener", "build")
    assert len(bus.inbox("listener")) == 1
    assert bus.stats()["channels"] == {"build": 2}

    bus.unsubscribe("listener", "build")
    assert bus.inbox("listener") == []


# ---------------------------------------------------------------------------
# Session lifecycle + pruning
# ---------------------------------------------------------------------------
def test_session_lifecycle_records_real_stats(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    sessions.begin("sess_life", task="do work", model="mock", project="p")
    sessions.record("sess_life", compactions=1, tokens_in=120, recall_hits=2)
    rec = sessions.get("sess_life")
    assert rec["status"] == "running"
    assert rec["turns"] == 1
    assert rec["stats"]["compactions"] == 1
    assert rec["stats"]["recall_hits"] == 2
    sessions.finish("sess_life", status="done")
    assert sessions.get("sess_life")["status"] == "done"


def _age(path, days: int) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["updated"] = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")


def test_prune_removes_expired_but_keeps_live_workers(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    sessions.create("old_idle", task="old")
    sessions.create("old_running", task="working")
    sessions.touch("old_running", status="running")
    sessions.create("fresh", task="new")
    _age(sessions._file("old_idle"), 30)

    out = sessions.prune(max_age_hours=7, max_sessions=50)
    assert out["removed"] >= 1
    assert sessions.get("old_idle") is None
    # running + fresh heartbeat is live and survives
    assert sessions.get("old_running") is not None
    assert sessions.get("fresh") is not None


# ---------------------------------------------------------------------------
# prepare_turn: reports what it did and never mutates the caller's list
# ---------------------------------------------------------------------------
def test_prepare_turn_compacts_full_prompt_and_reports(tmp_path, monkeypatch):
    from core.harness import runtime as runtime_mod

    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        runtime_mod,
        "cascade_recall",
        lambda *a, **k: {"hits": [{"content": "fact"}], "count": 1, "mode": "fts", "summary": "- fact", "elapsed_ms": 3},
    )
    messages = _long_turn("what changed?")
    before = [dict(m) for m in messages]
    out = runtime_mod.harness.prepare_turn("sess_prep", "what changed?", messages, budget_tokens=1200)
    assert out["compaction"]["compacted"] is True
    assert [m["content"] for m in messages] == [m["content"] for m in before]
    assert "Cascade memory" in out["messages"][0]["content"]
    assert out["memory"]["count"] == 1
    assert out["session"]["status"] == "running"
