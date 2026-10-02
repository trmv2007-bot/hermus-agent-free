"""Executive-to-runtime bridge for HERMUS."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .executive import ExecutiveBrain, executive_brain


def execute_with_executive(
    text: str,
    *,
    brain: ExecutiveBrain | None = None,
    agent: Any = None,
    agent_getter: Callable[..., Any] | None = None,
    platform: str = "api",
    user_id: str = "anonymous",
    model: str | None = None,
    mode: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    prefer: str = "auto",
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    stream: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    steer_source: Callable[[], list[str]] | None = None,
    max_repairs: int = 2,
    budget_steps: int | None = None,
    requirements: list[str] | None = None,
    domain: str | None = None,
    subgoals: list[str] | None = None,
    delegation: dict[str, Any] | None = None,
    preflight: bool | None = None,
    allow_preflight_planning: bool = False,
    read_only: bool = False,
) -> dict[str, Any]:
    """Run a request through the executive control plane and canonical runtime."""
    brain = brain or executive_brain
    text = str(text or "").strip()
    if not text:
        raise ValueError("text must not be empty")

    def emit(kind: str, data: dict[str, Any] | None = None) -> None:
        payload = dict(data or {})
        brain.observe(kind, payload)
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:
                pass

    roles = list((delegation or {}).get("selected_roles", []))
    brain.observe("request_received", {
        "platform": platform,
        "user_id": user_id,
        "prefer": prefer,
        "model": model,
        "read_only": read_only,
        "delegated_roles": roles,
    })

    kind = str(prefer or "auto").lower()
    if kind == "auto":
        from .runtime import classify_request
        kind = classify_request(text)

    goal_id: str | None = None
    executive_plan = None
    effective_requirements = requirements
    effective_subgoals = list(subgoals) if subgoals is not None else None

    if kind == "mission" and not read_only:
        goal_id = brain.create_goal(
            text,
            priority=50,
            metadata={
                "platform": platform,
                "user_id": user_id,
                "domain": domain,
                "delegated_roles": roles,
            },
        )
        executive_plan = brain.plan_goal(
            text,
            goal_id=goal_id,
            priority=50,
            success_criteria=requirements,
            context={"platform": platform, "user_id": user_id, "model": model, "domain": domain},
        )
        handoff = brain.handoff(executive_plan)
        if effective_requirements is None:
            effective_requirements = list(executive_plan.success_criteria)
        if effective_subgoals is None:
            effective_subgoals = [
                step.objective
                for step in executive_plan.steps
                if step.id in {"execute", "verify", "repair"}
            ]
        # The canonical MissionEngine consumes subgoals. Add delegation intent
        # as explicit objectives so the runtime can account for the specialist
        # team without giving this planning layer direct execution authority.
        for role in roles:
            objective = f"Specialist {role}: contribute to the mission and return evidence"
            if objective not in effective_subgoals:
                effective_subgoals.append(objective)
        brain.observe("executive_handoff", handoff, goal_id=goal_id)
        emit("executive_plan_ready", {
            "goal_id": goal_id,
            "steps": [step.to_dict() for step in executive_plan.steps],
            "success_criteria": list(executive_plan.success_criteria),
            "delegated_roles": roles,
            "subgoals": effective_subgoals,
        })

    from .runtime import execute

    try:
        result = execute(
            text,
            agent=agent,
            agent_getter=agent_getter,
            platform=platform,
            user_id=user_id,
            model=model,
            mode=mode,
            api_key=api_key,
            base_url=base_url,
            prefer=prefer,
            on_event=on_event,
            stream=stream,
            should_cancel=should_cancel,
            steer_source=steer_source,
            max_repairs=max_repairs,
            budget_steps=budget_steps,
            requirements=effective_requirements,
            domain=domain,
            subgoals=effective_subgoals,
            preflight=preflight,
            allow_preflight_planning=allow_preflight_planning,
            read_only=read_only,
        )
    except Exception as exc:
        if goal_id:
            brain.update_goal(goal_id, status="failed")
            brain.observe("mission_failed", {"error": str(exc)[:500]}, goal_id=goal_id)
        raise

    if goal_id:
        state = str(result.get("state") or result.get("status") or "")
        if state in {"completed", "done"}:
            brain.update_goal(goal_id, status="completed")
            brain.observe("mission_completed", {"mission_id": result.get("mission_id"), "verified": result.get("verified")}, goal_id=goal_id)
        elif state in {"failed", "cancelled", "blocked"}:
            brain.update_goal(goal_id, status="cancelled" if state == "cancelled" else "failed")
            brain.observe("mission_finished", {"state": state, "mission_id": result.get("mission_id"), "failure": result.get("failure")}, goal_id=goal_id)
        else:
            brain.observe("mission_result", {"state": state, "mission_id": result.get("mission_id")}, goal_id=goal_id)
        result = dict(result)
        result["executive"] = {
            "goal_id": goal_id,
            "planned": bool(executive_plan),
            "success_criteria": list(executive_plan.success_criteria) if executive_plan else [],
            "delegated_roles": roles,
            "subgoals": effective_subgoals or [],
        }

    return result


__all__ = ["execute_with_executive"]
