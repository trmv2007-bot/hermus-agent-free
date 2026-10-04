from core.long_horizon import LongHorizonPlanner
from core.specialist_registry import SpecialistContract, SpecialistRegistry


def test_long_horizon_plan_is_dependency_ordered_and_verifiable():
    planner = LongHorizonPlanner()
    plan = planner.build("release the project", success_criteria=["tests pass"], subgoals=["inspect", "implement", "verify"])
    assert [s.id for s in plan.steps] == ["plan_step_1", "plan_step_2", "plan_step_3"]
    assert plan.steps[1].dependencies == ["plan_step_1"]
    assert plan.steps[-1].success_criteria == ["tests pass"]
    assert planner.to_dag(plan).validate()


def test_long_horizon_replan_inserts_recovery_checkpoint():
    planner = LongHorizonPlanner()
    plan = planner.build("ship", subgoals=["build", "verify"])
    replanned = planner.replan(plan, failed_step="plan_step_1", reason="build failed")
    assert replanned.steps[0].id == "plan_step_1_recovery"
    assert replanned.steps[1].status == "needs_replan"


def test_specialist_registry_enforces_capability_contracts():
    registry = SpecialistRegistry((SpecialistContract("coder", ("coding",), ("task",), ("changes",), max_steps=8),), max_active=1)
    assert registry.validate_selection(["coder"])["ok"] is True
    assert registry.get("coder").requires_verification is True
    assert registry.validate_selection(["unknown"])["ok"] is False
    assert registry.validate_selection(["coder", "coder"])["ok"] is False
