"""Universal mission runtime — the single execution core for Hermus.

Every surface that runs work goes through :func:`execute`:

    USER / SCHEDULE / CHANNEL / CLI / API
                    │
                    ▼
             Mission Runtime (this module)
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   goal classifier        chat path (single ReAct turn)
        │
        ▼
   MissionEngine (plan → DAG → execute → observe → verify → repair)
        │  (every DAG node runs an evidence-gated agent loop)
        ▼
   unified result contract (``response`` + chat fields or mission report)

Callers wired today: ``HermusAgent.autonomous`` (→ prefer="mission"),
``POST /command`` (inline + queued), ``POST /stream/command``, the gateway
queue job kinds ``agent.chat`` / ``agent.autonomous`` / ``runtime.turn`` /
``channel.reply``, background agents, the cron scheduler and the SWE
lifecycle's coder phase. The legacy ``AutonomousRunner`` remains available
only as an explicit offline fallback (``mission_runtime_enabled=0``).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .config import config
from .run_events import record_issue

# --------------------------------------------------------------------- inputs

#: phrases that explicitly request the full autonomous/mission treatment
MISSION_MARKERS = (
    "mission:", "/mission", "autonomous:", "autonomously",
    "keep going until", "until it works", "don't stop until",
    "do not stop until", "end to end", "end-to-end", "unsupervised",
)

#: imperative verbs that imply producing/changing something
ACTION_VERBS = (
    "build", "create", "write", "implement", "develop", "fix", "repair",
    "refactor", "deploy", "generate", "integrate", "migrate", "automate",
    "scaffold", "port", "make me", "set up", "set it up",
)

#: object keywords — the goal names a concrete artifact/system
DELIVERABLE_HINTS = (
    "app", "application", "website", "web app", "api", "script", "bot",
    "tool", "service", "project", "repo", "repository", "code", "program",
    "game", "dashboard", "cli", "server", "scraper", "pipeline", "tests",
    "test suite", "report", "documentation", "docs", "app that", "page",
)


def classify_request(text: str) -> str:
    """Decide whether a message is a goal for the mission runtime or a chat turn.

    Deliberately conservative: questions, short replies and instructions about
    the conversation stay plain chat; imperative, deliverable-shaped goals
    ("build a web app with auth and tests, then run them until they pass") get
    the full mission lifecycle. ``HERMUS_MISSION_AUTO_CLASSIFY=0`` disables
    auto-promotion (only explicit ``autonomous``/``mission`` requests run
    missions).
    """
    text = (text or "").strip()
    low = text.lower()
    if not low:
        return "chat"
    if any(m in low for m in MISSION_MARKERS):
        return "mission"
    if not getattr(config, "mission_auto_classify", True):
        return "chat"
    # strip leading politeness so verb detection sees the imperative
    stripped = re.sub(r"^(please|hey|hi|could you|can you|would you|i want you to|"
                      r"i need you to|help me)\s+", "", low).strip()
    has_action_verb = any(stripped.startswith(v) or f" {v} " in f" {stripped} " for v in ACTION_VERBS)
    has_deliverable = any(h in low for h in DELIVERABLE_HINTS)
    has_plan_signal = len(low) > 120 or low.count("\n") >= 2 or re.search(r"\n\s*(?:\d+\.|-|\*)", text)
    multi_step = any(w in low for w in (" and then ", " then ", "after that", "next,", "finally",
                                        "step by step", " and also ", " and keep "))
    if has_action_verb and (has_deliverable or has_plan_signal or multi_step):
        return "mission"
    return "chat"


# -------------------------------------------------------------------- helpers
def _resolve_agent(
    agent: Any = None,
    agent_getter: Optional[Callable[..., Any]] = None,
    *,
    platform: str = "api",
    user_id: str = "anonymous",
    model: Optional[str] = None,
    mode: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Any:
    if agent is not None:
        return agent
    if agent_getter is not None:
        resolved = agent_getter(
            platform, user_id, model=model, mode=mode or "agent",
            api_key=api_key, base_url=base_url,
        )
        if resolved is not None:
            return resolved
    from .agent import HermusAgent

    return HermusAgent(model=model, mode=mode or "agent", api_key=api_key, base_url=base_url)


def _chat_with_compat(agent: Any, text: str, *, on_event=None, stream: bool = False,
                      should_cancel=None, steer_source=None) -> dict[str, Any]:
    """Call ``agent.chat`` passing only the kwargs the agent supports."""
    import inspect

    kwargs: dict[str, Any] = {}
    try:
        params = inspect.signature(agent.chat).parameters
    except (TypeError, ValueError):
        params = {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        params = {name: None for name in ("on_event", "stream", "should_cancel", "steer_source")}
    if on_event is not None and "on_event" in params:
        kwargs["on_event"] = on_event
    if stream and "stream" in params:
        kwargs["stream"] = True
    if should_cancel is not None and "should_cancel" in params:
        kwargs["should_cancel"] = should_cancel
    if steer_source is not None and "steer_source" in params:
        kwargs["steer_source"] = steer_source
    try:
        return agent.chat(text, **kwargs) if kwargs else agent.chat(text)
    except TypeError:
        return agent.chat(text)


def mission_report_to_result(report: Any, *, legacy_task: Optional[str] = None) -> dict[str, Any]:
    """Adapt a MissionReport into the unified (and legacy) result contract.

    Keeps the fields ``HermusAgent.autonomous`` consumers have always read
    (``status``, ``phases``, ``steps``, ``verified``, ``repairs``,
    ``final_answer``) while adding the full mission report fields, so the
    mission runtime can be the universal path without breaking anyone.
    """
    data = report.to_dict()
    state = str(data.get("state") or "failed")
    completed = state == "completed"
    blocked = state == "blocked"
    cancelled = state == "cancelled"

    subgoals = data.get("subgoals") or []
    steps = [
        {
            "goal": str(sg.get("goal") or ""),
            "status": "done" if sg.get("status") == "completed" else str(sg.get("status") or "pending"),
            "attempts": 1,
        }
        for sg in subgoals
    ]
    # legacy phases contract: starts with understand, ends with finish
    phases = ["understand", "plan"]
    for st in ("executing", "observing", "verifying"):
        phases.append(st)
    if data.get("repair_history"):
        phases.extend(["diagnose", "repair"])
    if blocked:
        phases.append("blocked")
    if cancelled:
        phases.append("cancelled")
    phases.append("finish")

    answer = (
        data.get("final_proof")
        or data.get("response")
        or (f"Mission blocked: {data.get('blocker_reason')}" if blocked else "")
        or ""
    )
    if blocked and data.get("blocker_instructions"):
        answer = f"{answer}\n{data['blocker_instructions']}"

    out = dict(data)  # mission_id, state, requirements, artifacts, evidence, budget, ...
    out.update({
        # canonical answer contract (what /command, SSE and TTS read)
        "response": answer,
        # legacy autonomous-runner contract (CLI, delegation, old callers)
        "status": "done" if completed else ("failed" if not blocked else "blocked"),
        "phases": phases,
        "steps": steps,
        "verified": completed,
        "repairs": int((data.get("budget") or {}).get("repairs_used") or 0),
        "final_answer": answer,
        # runtime metadata
        "run_kind": "mission",
        "mission": data,
    })
    return out


# -------------------------------------------------------------------- execute
def execute(
    text: str,
    *,
    agent: Any = None,
    agent_getter: Optional[Callable[..., Any]] = None,
    platform: str = "api",
    user_id: str = "anonymous",
    model: Optional[str] = None,
    mode: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    prefer: str = "auto",
    on_event: Optional[Callable[..., None]] = None,
    stream: bool = False,
    should_cancel: Optional[Callable[[], bool]] = None,
    steer_source: Optional[Callable[[], list[str]]] = None,
    max_repairs: int = 2,
    budget_steps: Optional[int] = None,
    requirements: Optional[list[str]] = None,
    domain: Optional[str] = None,
    subgoals: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Run one request through the universal runtime.

    ``prefer``: ``"auto"`` classifies (chat vs mission), ``"chat"`` forces a
    single ReAct turn, ``"mission"`` forces the full mission lifecycle.
    Returns a dict whose canonical answer is ``response``; chat results carry
    the chat fields (steps/tool_results/...), mission results carry the mission
    report plus the legacy autonomous fields.
    """
    text = str(text or "").strip()
    prefer = (prefer or "auto").lower()
    if prefer not in ("auto", "chat", "mission"):
        prefer = "auto"

    runtime_on = bool(getattr(config, "mission_runtime_enabled", True))
    kind = "chat"
    if prefer == "mission":
        kind = "mission"
    elif prefer == "auto":
        kind = classify_request(text)

    # Feature flag off (or explicitly-legacy callers): behave exactly like the
    # pre-runtime code paths — chat stays chat, missions fall back to the
    # AutonomousRunner loop.
    if kind == "mission" and not runtime_on:
        return _legacy_autonomous(
            text, agent=agent, agent_getter=agent_getter, platform=platform,
            user_id=user_id, model=model, mode=mode, api_key=api_key,
            base_url=base_url, max_repairs=max_repairs, on_event=on_event,
            should_cancel=should_cancel,
        )

    resolved_agent = _resolve_agent(
        agent, agent_getter, platform=platform, user_id=user_id,
        model=model, mode=mode, api_key=api_key, base_url=base_url,
    )

    if kind == "chat" or resolved_agent is None:
        result = _chat_with_compat(
            resolved_agent, text, on_event=on_event, stream=stream,
            should_cancel=should_cancel, steer_source=steer_source,
        )
        if not isinstance(result, dict):
            result = {"response": str(result or "")}
        result.setdefault("response", "")
        result.setdefault("run_kind", "chat")
        if on_event is not None:
            try:
                on_event("agent_response", {
                    "text": str(result.get("response") or "")[:12000],
                    "steps": result.get("steps"),
                    "tool_calls": list(result.get("tool_calls") or [])[:30],
                    "run_kind": "chat",
                })
            except Exception:
                pass
        return result

    # ------------------------------------------------------- mission runtime
    from .mission import MissionEngine, mission_engine

    def _emit(event_type: str, data: Optional[dict] = None) -> None:
        if on_event is not None:
            try:
                on_event(event_type, data or {})
            except Exception:
                pass

    _emit("mission_runtime_started", {"goal": text[:200], "prefer": prefer})
    try:
        engine = MissionEngine(storage_dir=mission_engine.storage_dir)
        report = engine.start_mission(
            goal=text,
            requirements=requirements,
            domain=domain,
            subgoals=subgoals,
            budget_steps=int(budget_steps or getattr(config, "mission_budget_steps", 25)),
            max_repairs=max_repairs,
            agent=resolved_agent,
            on_event=on_event,
            should_cancel=should_cancel,
            steer_source=steer_source,
        )
    except Exception as exc:
        # a crash inside the mission runtime must surface as a structured
        # failure, never as a fabricated success
        record_issue("runtime", "mission", exc, retryable=False,
                     fallback="mission aborted; falling back to single chat turn")
        if on_event is not None:
            try:
                on_event("mission_error", {"error": str(exc)[:400]})
            except Exception:
                pass
        result = _chat_with_compat(
            resolved_agent, text, on_event=on_event, stream=stream,
            should_cancel=should_cancel, steer_source=steer_source,
        )
        if isinstance(result, dict):
            result.setdefault("run_kind", "chat")
            result["mission_error"] = str(exc)[:400]
            return result
        return {"response": str(result or ""), "run_kind": "chat",
                "mission_error": str(exc)[:400]}

    result = mission_report_to_result(report)
    _emit("agent_response", {"text": result["response"][:12000],
                             "run_kind": "mission",
                             "mission_id": result.get("mission_id"),
                             "state": result.get("state")})
    return result


def _legacy_autonomous(
    text: str,
    *,
    agent: Any = None,
    agent_getter: Optional[Callable[..., Any]] = None,
    platform: str = "api",
    user_id: str = "anonymous",
    model: Optional[str] = None,
    mode: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_repairs: int = 2,
    on_event=None,
    should_cancel=None,
) -> dict[str, Any]:
    """Pre-runtime behavior: plan→execute→verify→repair via AutonomousRunner."""
    from .autonomous import AutonomousRunner, Verifier

    resolved = _resolve_agent(
        agent, agent_getter, platform=platform, user_id=user_id,
        model=model, mode=mode, api_key=api_key, base_url=base_url,
    )

    def executor(goal: str) -> str:
        res = _chat_with_compat(
            resolved, goal, on_event=on_event,
            should_cancel=should_cancel,
        )
        return str(res.get("response") or "")

    runner = AutonomousRunner(executor=executor, verifier=Verifier(), max_repairs=max_repairs)
    report = runner.run(text)
    out = report.to_dict()
    out["run_kind"] = "mission-legacy"
    return out
