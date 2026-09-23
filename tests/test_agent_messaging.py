"""Agent messaging mirrored onto the canonical event bus.

The MessageBus used to keep its history in process memory only, so agent
conversations could not be replayed after a restart and were invisible to the
harness and the workspace.
"""

from __future__ import annotations

import pytest

from core.agents.messaging import AgentMessage, MessageBus, MessageType


@pytest.fixture()
def bus(tmp_path, monkeypatch):
    """A MessageBus whose traffic lands on a fresh, file-backed EventBus."""
    import core.events as events
    from core.events.bus import EventBus

    sink = EventBus(log_path=tmp_path / "events.jsonl")
    monkeypatch.setattr(events, "get_bus", lambda: sink)
    return MessageBus(), sink


async def _send(bus):
    message_bus, _ = bus
    return await message_bus.send("alice", "bob", "the build is green", MessageType.TEXT)


@pytest.mark.asyncio
async def test_a_sent_message_appears_on_the_canonical_bus(bus):
    message = await _send(bus)
    _, sink = bus

    published = [env for env in sink.recent(limit=20) if env.command == "agent.message"]
    assert published, "agent traffic must reach the one durable event log"
    envelope = published[-1]
    assert envelope.source == "agents.messaging"
    assert envelope.args_redacted["sender_id"] == "alice"
    assert envelope.args_redacted["target_id"] == "bob"
    assert envelope.args_redacted["message_id"] == message.message_id
    assert envelope.args_redacted["preview"] == "the build is green"


@pytest.mark.asyncio
async def test_a_broadcast_is_labelled_as_one(bus):
    message_bus, sink = bus
    await message_bus.broadcast("alice", "stand down")

    kinds = [env.command for env in sink.recent(limit=20)]
    assert "agent.broadcast" in kinds
    assert "agent.message" not in kinds


@pytest.mark.asyncio
async def test_a_long_message_is_previewed_not_transcribed(bus):
    message_bus, sink = bus
    await message_bus.send("a", "b", "x" * 5000)

    envelope = [env for env in sink.recent(limit=20) if env.command == "agent.message"][-1]
    assert len(envelope.args_redacted["preview"]) <= 200


@pytest.mark.asyncio
async def test_a_failed_mirror_never_eats_the_delivery(bus, monkeypatch):
    """The event log is a projection. If it is broken, messages still move."""
    message_bus, sink = bus

    def explode(_envelope):
        raise RuntimeError("event log unavailable")

    monkeypatch.setattr(sink, "publish", explode)
    message = await message_bus.send("alice", "bob", "still delivered")

    assert message.content == "still delivered"
    assert message in message_bus._message_history


@pytest.mark.asyncio
async def test_a_topic_publish_reaches_the_subscriber_inbox(bus):
    """publish() used to return a subscriber count after building each copy and
    discarding it, so an ignored broadcast was indistinguishable from a read one."""
    message_bus, _ = bus
    await message_bus.subscribe("carol", "builds")
    note = AgentMessage(sender_id="alice", target_id=None, content="build 42 passed", message_type=MessageType.SYSTEM)

    count = await message_bus.publish("builds", note)

    assert count == 1
    inbox = message_bus.get_history(agent_id="carol")
    assert any("build 42" in m.content for m in inbox), "the subscriber was counted but received nothing"


@pytest.mark.asyncio
async def test_publishing_to_a_topic_nobody_watches_reports_zero(bus):
    message_bus, _ = bus
    note = AgentMessage(sender_id="alice", target_id=None, content="into the void")

    assert await message_bus.publish("unwatched", note) == 0
    assert not [m for m in message_bus.get_history() if m.content == "into the void"]
