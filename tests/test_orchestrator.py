from core.orchestrator import HERMUSOrchestrator


def test_orchestrator_state_has_unified_sections():
    state = HERMUSOrchestrator().state()
    assert {"presence", "runs", "queue", "active_jobs", "recent_jobs", "agents", "health"} <= set(state)


def test_empty_command_is_rejected():
    result = HERMUSOrchestrator().submit("   ")
    assert result.accepted is False
    assert result.status == "rejected"
