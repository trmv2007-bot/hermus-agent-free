"""jcode-inspired harness primitives."""
from __future__ import annotations

from core.harness.compaction import compact_messages
from core.harness import bus, files, sessions
from core.harness.swarm import spawn


def test_compaction_triggers_at_90_percent():
    messages = [{"role": "system", "content": "sys"}]
    for i in range(20):
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
