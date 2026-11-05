from core.agent_delegation import AgentDelegator


def test_delegator_builds_bounded_coding_team():
    plan = AgentDelegator(max_agents=5).build_plan("Build a secure API and verify it")
    assert plan.selected_roles
    assert len(plan.selected_roles) <= 5
    assert "coder" in plan.selected_roles
    assert "verifier" in plan.selected_roles
    assert plan.dag.validate()


def test_delegator_deduplicates_roles():
    plan = AgentDelegator().build_plan("review and verify code")
    assert len(plan.selected_roles) == len(set(plan.selected_roles))


def test_empty_task_rejected():
    try:
        AgentDelegator().build_plan("   ")
    except ValueError:
        return
    raise AssertionError("empty task should be rejected")
