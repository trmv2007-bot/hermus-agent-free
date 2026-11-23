"""The visible plan: shown before acting, and advanced only by evidence."""

from __future__ import annotations

from core.thinking.plan import PlanProgress, build_turn_plan, needs_plan


def test_a_question_gets_no_plan():
    assert needs_plan("what is the capital of France?") is False
    assert needs_plan("explain how to fix my app") is False


def test_chat_mode_never_plans():
    assert needs_plan("create a folder and then write a file in it", mode="chat") is False


def test_a_sequence_of_actions_gets_a_plan():
    assert needs_plan("create out.txt with hello, then read it back and report what it says") is True


def test_a_file_change_gets_a_plan_even_with_one_clause():
    assert needs_plan("write the word hello into notes/hello.txt") is True


def test_numbered_bullets_become_the_steps_in_order():
    plan = build_turn_plan("1. Create out.txt\n2. Append a second line\n3. Read the file back")
    goals = [s.goal for s in plan.steps]
    assert goals[:3] == ["Create out.txt", "Append a second line", "Read the file back"]
    assert plan.steps[-1].action == "verify"
    assert plan.status == "drafted"


def test_a_single_change_is_scaffolded_read_change_verify():
    plan = build_turn_plan("write the word hello into notes/hello.txt")
    assert [s.action for s in plan.steps] == ["investigate", "edit", "verify"]


def test_a_shell_request_is_scaffolded_run_verify():
    plan = build_turn_plan("run the command python -c 'print(1)' and check the output")
    assert plan.steps[0].action == "shell"
    assert plan.steps[-1].action == "verify"


def test_a_successful_tool_marks_its_step_done_with_evidence():
    plan = build_turn_plan("write the word hello into notes/hello.txt")
    progress = PlanProgress(plan)
    event = progress.observe("file_write", True, "12B")
    assert event is not None
    assert event["status"] == "done"
    assert event["step"] == 2  # the edit step, not the read step
    assert "file_write ok" in event["evidence"]
    assert plan.steps[1].status == "done"
    assert plan.steps[0].status == "pending"


def test_a_failed_tool_marks_the_step_failed_not_done():
    plan = build_turn_plan("write the word hello into notes/hello.txt")
    progress = PlanProgress(plan)
    event = progress.observe("file_write", False, "permission denied")
    assert event["status"] == "failed"
    assert plan.steps[1].status == "failed"


def test_reading_before_writing_does_not_credit_the_change_step():
    plan = build_turn_plan("write the word hello into notes/hello.txt")
    progress = PlanProgress(plan)
    progress.observe("file_read", True, "3B")
    assert plan.steps[1].status == "pending"
    assert plan.steps[0].status == "done"


def test_an_unrelated_tool_advances_nothing():
    plan = build_turn_plan("write the word hello into notes/hello.txt")
    progress = PlanProgress(plan)
    assert progress.observe("memory_search", True) is None
    assert all(s.status == "pending" for s in plan.steps)


def test_settle_reports_unbacked_steps_instead_of_faking_them():
    plan = build_turn_plan("write the word hello into notes/hello.txt")
    progress = PlanProgress(plan)
    progress.observe("file_write", True, "12B")
    rendered = progress.settle()
    statuses = {row["n"]: row["status"] for row in rendered}
    assert statuses[2] == "done"
    assert statuses[1] == "skipped"
    assert statuses[3] == "skipped"
    assert "no tool result backed this step" in rendered[0]["evidence"]
    assert plan.status == "done"


def test_verified_ratio_counts_action_steps_only():
    plan = build_turn_plan("write the word hello into notes/hello.txt")
    progress = PlanProgress(plan)
    progress.observe("file_write", True, "12B")
    assert progress.verified_ratio() == 0.5  # investigate done? no: edit done, investigate skipped


def test_a_sequence_plan_advances_in_order():
    plan = build_turn_plan("create out.txt with hello, then read it back and report what it says")
    progress = PlanProgress(plan)
    progress.observe("file_write", True, "5B")
    progress.observe("file_read", True, "5B")
    assert [s.status for s in plan.steps[:2]] == ["done", "done"]
