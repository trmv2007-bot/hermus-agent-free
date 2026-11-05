from core.conversation import ConversationManager
from core.run_events import RunBus


def test_conversation_context_and_steering():
    bus = RunBus()
    manager = ConversationManager()
    # Use the manager's global bus by monkeypatching through its module dependency.
    import core.conversation as conversation

    old = conversation.run_bus
    conversation.run_bus = bus
    try:
        manager._sink_remove()
        manager._sink_remove = bus.add_sink(manager._on_run_event)
        session = manager.get_or_create("s1", user_id="u1", platform="voice")
        manager.add_turn("s1", "user", "build the dashboard")
        bus.start("r1")
        manager.attach_run("s1", "r1")
        steered = manager.steer("s1", "Use the existing design system.")
        assert steered["ok"] is True
        assert bus.pending_steers("r1") == ["Use the existing design system."]
        assert manager.context("s1")[0]["text"] == "build the dashboard"
    finally:
        conversation.run_bus = old


def test_conversation_interrupt_cancels_active_run():
    bus = RunBus()
    manager = ConversationManager()
    import core.conversation as conversation

    old = conversation.run_bus
    conversation.run_bus = bus
    try:
        manager._sink_remove()
        manager._sink_remove = bus.add_sink(manager._on_run_event)
        manager.get_or_create("s2")
        bus.start("r2")
        manager.attach_run("s2", "r2")
        result = manager.interrupt("s2", reason="barge_in")
        assert result["ok"] is True
        assert bus.is_cancelled("r2") is True
        assert manager.snapshot("s2")["interrupted"] is True
    finally:
        conversation.run_bus = old


def test_run_notifications_clear_when_consumed():
    bus = RunBus()
    manager = ConversationManager()
    import core.conversation as conversation

    old = conversation.run_bus
    conversation.run_bus = bus
    try:
        manager._sink_remove()
        manager._sink_remove = bus.add_sink(manager._on_run_event)
        manager.get_or_create("s3")
        bus.start("r3")
        manager.attach_run("s3", "r3")
        bus.finish("r3", status="cancelled")
        items = manager.notifications("s3")
        assert items
        assert manager.notifications("s3", consume=True)
        assert manager.notifications("s3") == []
    finally:
        conversation.run_bus = old
