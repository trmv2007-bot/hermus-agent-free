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

# ---------------------------------------------------------------------------
# Intent detection: QUESTION / EXPLANATION / ANALYSIS / ACTION
# ---------------------------------------------------------------------------
# A keyword match on an action verb + a deliverable word used to promote
# "Can you explain how to fix my app?" (fix + app) and "What is the best way to
# build an API?" (build + API) to full autonomous missions. The user asked to
# *understand* something; launching a mission there makes Hermus unpredictable.
# The intent is therefore decided first, and only an ACTION intent can be
# promoted to a mission.

INTENT_QUESTION = "question"        # "what is the capital of France?"
INTENT_EXPLANATION = "explanation"  # "explain how to fix my app", "best way to …"
INTENT_ANALYSIS = "analysis"        # "review this diff", "summarize this"
INTENT_ACTION = "action"            # "build me a web app and test it"
INTENT_CONVERSATION = "conversation"  # greetings, chit-chat, meta instructions

#: leading filler/politeness stripped before the verb is inspected
_FILLER_RE = re.compile(
    r"^(?:please|hey|hi|hello|yo|ok|okay|so|now|then|and|could you|can you|"
    r"would you|will you|i want you to|i need you to|i'd like you to|"
    r"let's|lets|help me|go ahead and|try to)\s+",
    re.I,
)

#: interrogative openings (after filler removal)
QUESTION_STARTERS = (
    "what", "why", "when", "who", "whom", "whose", "which", "where", "how",
    "is ", "are ", "was ", "were ", "do ", "does ", "did ", "can ", "could ",
    "would ", "should ", "will ", "am ", "has ", "have ", "had ", "anyone",
    "anybody", "isn't", "aren't", "don't", "doesn't", "shouldn't",
)

#: phrases that mean "teach me / describe it", even with an action verb inside
# NOTE: deliberately no bare "what is/are" — those are ordinary factual
# questions ("what is the capital of France?"), and the *how* phrases below
# already cover "what is the best way to build an API?".
EXPLANATION_HINTS = (
    "explain", "how does", "how do i", "how do you", "how can i",
    "how should i", "how would i", "how to", "why does", "why is", "why are",
    "difference between", "pros and cons", "teach me", "walk me through",
    "meaning of", "best way to", "best practice", "best approach",
    "i don't understand", "help me understand", "clarify", "describe how",
    "overview of", "eli5", "in layman", "tutorial on", "guide to",
    "introduction to", "tell me about",
)

#: phrases whose product is an assessment, not a change in the world
ANALYSIS_HINTS = (
    "analyze", "analyse", "review", "audit", "compare", "evaluate", "assess",
    "summarize", "summarise", "investigate", "diagnose", "inspect", "critique",
    "check whether", "look into", "findings", "pros and cons", "trade-offs",
    "tradeoffs", "report on", "list the", "identify the",
)


def detect_intent(text: str) -> str:
    """Classify the *intent* of a message before any mission promotion.

    Returns one of :data:`INTENT_QUESTION`, :data:`INTENT_EXPLANATION`,
    :data:`INTENT_ANALYSIS`, :data:`INTENT_ACTION`, :data:`INTENT_CONVERSATION`.
    """
    low = (text or "").strip().lower()
    if not low:
        return INTENT_CONVERSATION

    stripped = _FILLER_RE.sub("", low).strip() or low
    # repeated filler ("can you please build …")
    stripped = _FILLER_RE.sub("", stripped).strip() or stripped

    # 1. explanations win over everything: "explain how to fix my app" and
    #    "what is the best way to build an API" are requests to understand.
    if any(h in low for h in EXPLANATION_HINTS):
        return INTENT_EXPLANATION

    has_action_verb = any(
        stripped.startswith(v) or f" {v} " in f" {stripped} " for v in ACTION_VERBS
    )
    has_deliverable = any(h in low for h in DELIVERABLE_HINTS)
    has_plan_signal = len(low) > 120 or low.count("\n") >= 2 or bool(
        re.search(r"\n\s*(?:\d+\.|-|\*)", text or "")
    )
    multi_step = any(w in low for w in (
        " and then ", " then ", "after that", "next,", "finally",
        "step by step", " and also ", " and keep ",
    ))

    # 2. an imperative that names a deliverable is a real action request,
    #    even when phrased as a question ("can you build me a website?").
    if has_action_verb and (has_deliverable or has_plan_signal or multi_step):
        return INTENT_ACTION

    # 3. everything else that asks something is a question
    if stripped.endswith("?") or stripped.startswith(QUESTION_STARTERS):
        return INTENT_QUESTION

    # 4. assessments/reports: the deliverable is the answer itself
    if any(h in low for h in ANALYSIS_HINTS):
        return INTENT_ANALYSIS

    # 5. a bare imperative ("fix it", "run tests") with no object is still work
    if has_action_verb:
        return INTENT_ACTION

    return INTENT_CONVERSATION


def classify_request(text: str, *, with_intent: bool = False):
    """Decide whether a message is a goal for the mission runtime or a chat turn.

    Deliberately conservative and intent-first:

    * explicit markers (``mission:``, ``autonomously``, ``keep going until``…)
      always promote;
    * questions, explanations and analysis requests stay plain chat — they ask
      Hermus to *understand* or *report*, not to execute;
    * imperative, deliverable-shaped goals ("build a web app with auth and
      tests, then run them until they pass") get the full mission lifecycle.

    ``HERMUS_MISSION_AUTO_CLASSIFY=0`` disables auto-promotion (only explicit
    ``autonomous``/``mission`` requests run missions). With ``with_intent=True``
    a ``(kind, intent)`` tuple is returned.
    """
    text = (text or "").strip()
    low = text.lower()
    intent = detect_intent(text)
    if not low:
        return ("chat", intent) if with_intent else "chat"
    if any(m in low for m in MISSION_MARKERS):
        return ("mission", intent) if with_intent else "mission"
    if not getattr(config, "mission_auto_classify", True):
        return ("chat", intent) if with_intent else "chat"

    if intent == INTENT_ACTION:
        return ("mission", intent) if with_intent else "mission"
    return ("chat", intent) if with_intent else "chat"


# -------------------------------------------------------------------- helpers
def _model_id(agent: Any) -> str:
    """Best-effort ``provider/model`` identifier for a bound agent."""
    try:
        provider = str(getattr(getattr(agent, "llm", None), "provider", "") or "")
        name = str(getattr(agent, "model_name", "") or getattr(agent, "model", "") or "")
        if provider and name:
            return f"{provider}/{name}"
        return name or provider
    except Exception:
        return ""


def _chat_fallback_allowed() -> bool:
    """Is the (discouraged) mission→chat downgrade explicitly enabled?

    Off by default and for a good reason: a mission that crashes and quietly
    answers with advice hides the failure from the user. Flip
    ``HERMUS_MISSION_FALLBACK_TO_CHAT=1`` only for interactive demos.
    """
    return bool(getattr(config, "mission_fallback_to_chat", False))


def _emit_model_capability_warning(
    agent: Any, prefer: str, emit: Callable[[str, dict], None]
) -> Optional[dict]:
    """Pre-flight capability check (tools/vision/context/…).

    Never blocks by itself: it publishes ``model_capability_warning`` (and a
    recommendation when ``HERMUS_AUTO_SELECT_MODEL=1``) so the dashboard, the
    CLI and the SSE stream can tell the user *why* a mission is struggling
    before it starts — instead of discovering it mid-run.
    """
    if not getattr(config, "model_capability_check", True):
        return None
    model = _model_id(agent)
    if not model:
        return None
    try:
        from .model_capabilities import negotiate, select_compatible_model

        report = negotiate(model)
        payload = {"model": model, "report": report.to_dict(),
                   "warnings": report.warnings(["tools"])}
        if report.missing(["tools"]) and getattr(config, "auto_select_model", False):
            pick, info = select_compatible_model(["tools"])
            payload["recommended_model"] = pick
            payload["selection"] = info
        if payload["warnings"] or payload.get("recommended_model"):
            emit("model_capability_warning", payload)
        return payload
    except Exception:
        return None


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


def mission_failure_result(
    goal: str,
    *,
    exc: Any = None,
    mission_id: Optional[str] = None,
    stage: Optional[str] = None,
    reason: Optional[str] = None,
    recoverable: bool = True,
    report: Any = None,
    error_type: Optional[str] = None,
) -> dict[str, Any]:
    """Build the structured **mission failure** contract.

    A crash inside the mission runtime is a *mission outcome*, never an
    invitation to answer the user with prose instead. The returned dict is
    deliberately shaped like a failed mission (``run_kind='mission_failed'``,
    ``status='failed'``) and carries everything a caller needs to recover::

        {
          "response":      "MISSION FAILED …",   # never advice-shaped prose
          "run_kind":      "mission_failed",
          "mission_id":    "msn_…",
          "failure": {
              "stage":        which lifecycle stage died,
              "reason":       human-readable cause,
              "error_type":   exception class,
              "message":      exception text,
              "recoverable":  can this be fixed and retried?
              "resumable":    can resume_mission() pick it up?
              "resume_command": "hermus mission resume <id> --restart-failed",
              "resume_api":    "POST /missions/<id>/resume?restart_failed=true",
          },
          ...legacy autonomous fields (status/phases/steps/verified/repairs)
        }
    """
    error_type = (error_type or (type(exc).__name__ if exc is not None else "")
                  or reason or "mission_error")
    message = str(exc)[:500] if exc is not None else (reason or "mission failed")
    stage = stage or "unknown"
    mid = mission_id or ""
    resume_flag = " --restart-failed" if (recoverable and stage != "completed") else ""
    failure = {
        "stage": stage,
        "reason": reason or message,
        "error_type": error_type,
        "message": message,
        "recoverable": bool(recoverable),
        "resumable": bool(recoverable and bool(mid)),
        "resume_command": (f"hermus mission resume {mid}{resume_flag}" if mid else ""),
        "resume_api": (f"POST /missions/{mid}/resume?restart_failed=true" if mid else ""),
        "mission_id": mid or None,
    }
    headline = (
        f"MISSION FAILED — the mission runtime stopped before completing the goal.\n"
        f"stage: {stage}\nreason: {failure['reason']}\n"
        f"recoverable: {'yes' if recoverable else 'no'}"
        + (f"\nresume: {failure['resume_command']}" if failure["resume_command"] else "")
    )
    out: dict[str, Any] = {
        # canonical answer contract — explicit, never a chat-style explanation
        "response": headline,
        "run_kind": "mission_failed",
        "mission_failed": True,
        "state": "failed",
        # legacy autonomous-runner contract
        "status": "failed",
        "phases": ["understand", "plan", "execute", "failed"],
        "steps": [],
        "verified": False,
        "repairs": 0,
        "final_answer": headline,
        "failure": failure,
    }
    if mission_id:
        out["mission_id"] = mission_id
    if report is not None:
        try:
            data = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        except Exception:
            data = {}
        if data:
            out["mission"] = data
            out["mission_id"] = data.get("mission_id") or mission_id
            for key in ("requirements", "subgoals", "evidence", "artifacts", "progress_pct"):
                if key in data:
                    out[key] = data[key]
            if isinstance(data.get("failure"), dict):
                out["failure"].update(data["failure"])
            # a recorded failure is resumable through the explicit restart path
            # (``resume_mission(..., restart_failed=True)``), which is what the
            # resume command above already carries.
            if str(data.get("state") or "") == "failed":
                out["failure"]["resumable"] = bool(recoverable and mission_id)
                out["failure"]["resume_with_restart"] = True
            out["response"] = data.get("final_proof") or headline
            out["final_answer"] = out["response"]
            out["phases"] = ["understand", "plan", "execute", "failed"]
    return out


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
    # A non-completed mission carries structured diagnostics so every surface
    # (dashboard, CLI, channels, queue result) can explain *why* and *how to
    # recover* instead of showing an empty answer.
    failure: Optional[dict[str, Any]] = None
    if not completed:
        summary = (
            report.failure_summary()
            if hasattr(report, "failure_summary")
            else {}
        )
        failure = {
            "stage": summary.get("stage") or state,
            "reason": summary.get("reason") or (data.get("blocker_reason") or "mission did not complete"),
            "error_type": summary.get("error_type") or state,
            "message": summary.get("reason") or "",
            "recoverable": bool(summary.get("recoverable", state != "completed")),
            "resumable": bool(summary.get("resumable", state in ("blocked", "paused"))),
            "resume_command": summary.get("resume_command", ""),
            "resume_api": (f"POST /missions/{data.get('mission_id')}/resume"
                           if data.get("mission_id") else ""),
            "mission_id": data.get("mission_id"),
        }
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
    if failure:
        out["failure"] = failure
        out["mission_failed"] = not completed and not blocked
        if not answer:
            out["response"] = out["final_answer"] = (
                f"MISSION FAILED — {failure['reason']}"
                + (f"\nresume: {failure['resume_command']}" if failure["resume_command"] else "")
            )
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

    # ---- pre-flight: does the selected model support what a mission needs? ----
    _emit_model_capability_warning(resolved_agent, prefer, _emit)

    try:
        engine = MissionEngine(storage_dir=mission_engine.storage_dir)
        report = engine.start_mission(
            goal=text,
            requirements=requirements,
            domain=domain,
            subgoals=subgoals,
            budget_steps=budget_steps,
            max_repairs=max_repairs,
            agent=resolved_agent,
            on_event=on_event,
            should_cancel=should_cancel,
            steer_source=steer_source,
        )
    except Exception as exc:
        # ------------------------------------------------------------------
        # A crash inside the mission runtime is a MISSION FAILURE.
        # It must never be silently downgraded to a chat answer: the user
        # asked for execution, and an "here's how you could build it" reply
        # hides the failure while looking intelligent.
        # ------------------------------------------------------------------
        record_issue(
            "runtime", "mission", exc, retryable=True,
            fallback="structured MISSION_FAILED result (no chat downgrade)",
        )
        failure = mission_failure_result(
            text, exc=exc, stage="mission_start",
            reason=f"mission runtime error: {exc}",
            error_type=type(exc).__name__,
            recoverable=True,
        )
        _emit("mission_error", {"error": str(exc)[:400],
                                "stage": "mission_start",
                                "recoverable": True})
        _emit("mission_finished", {"state": "failed", "failure": failure["failure"]})
        _emit("agent_response", {"text": failure["response"][:12000],
                                 "run_kind": "mission_failed"})
        if _chat_fallback_allowed():
            # Opt-in escape hatch only (HERMUS_MISSION_FALLBACK_TO_CHAT=1).
            # The result is still labelled as a degraded chat turn so no caller
            # can mistake it for mission output.
            degraded = _chat_with_compat(
                resolved_agent, text, on_event=on_event, stream=stream,
                should_cancel=should_cancel, steer_source=steer_source,
            )
            if isinstance(degraded, dict):
                degraded["run_kind"] = "chat_fallback"
                degraded["degraded_from"] = "mission"
                degraded["mission_failed"] = True
                degraded["mission_error"] = str(exc)[:400]
                degraded["failure"] = failure["failure"]
                return degraded
        return failure

    if str(getattr(report, "state", "") or "") == "failed":
        # The engine records crashes and exhausted budgets as failed reports
        # (rather than raising) so the mission stays resumable; surface it as
        # the same structured failure contract.
        recorded_error = getattr(report, "error", None) or {}
        failure = mission_failure_result(
            text, mission_id=getattr(report, "mission_id", None), report=report,
            stage=str(recorded_error.get("stage") or "lifecycle"),
            reason=str(recorded_error.get("message") or ""),
            error_type=str(recorded_error.get("type") or ""),
            recoverable=bool(getattr(report, "recoverable", True)),
        )
        _emit("mission_finished", {"state": "failed",
                                   "mission_id": getattr(report, "mission_id", None),
                                   "failure": failure["failure"]})
        _emit("agent_response", {"text": failure["response"][:12000],
                                 "run_kind": "mission_failed",
                                 "mission_id": getattr(report, "mission_id", None)})
        return failure

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
