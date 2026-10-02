from core.proactive import ProactiveAutomation


def test_disabled_rule_never_queues():
    queued = []
    automation = ProactiveAutomation(
        path="/tmp/hermus-test-automation-disabled.json",
        enqueue=lambda kind, payload: queued.append((kind, payload)),
    )
    rule = automation.add_rule(
        name="test",
        event_type="mission.completed",
        action_type="runtime.turn",
        task="summarize",
    )

    assert automation.handle_event({"type": "mission.completed", "payload": {}}) == []
    assert queued == []
    assert rule.fire_count == 0


def test_enabled_rule_queues_once_until_cooldown():
    queued = []
    now = [100.0]
    automation = ProactiveAutomation(
        path="/tmp/hermus-test-automation-enabled.json",
        enqueue=lambda kind, payload: queued.append((kind, payload)) or "job-1",
        clock=lambda: now[0],
    )
    rule = automation.add_rule(
        name="test",
        event_type="mission.completed",
        action_type="runtime.turn",
        task="summarize",
        enabled=True,
        cooldown_seconds=30,
    )

    first = automation.handle_event({"type": "mission.completed", "payload": {}})
    second = automation.handle_event({"type": "mission.completed", "payload": {}})
    now[0] += 31
    third = automation.handle_event({"type": "mission.completed", "payload": {}})

    assert first[0]["queued"] is True
    assert second == []
    assert third[0]["queued"] is True
    assert len(queued) == 2
    assert rule.fire_count == 2


def test_unsafe_action_type_is_rejected():
    automation = ProactiveAutomation(path="/tmp/hermus-test-automation-policy.json")
    try:
        automation.add_rule(
            name="unsafe",
            event_type="anything",
            action_type="shell.execute",
            task="do it",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe proactive action was accepted")
