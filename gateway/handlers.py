"""Job handlers registered on the gateway queue (intake ⇄ execution contract).

Everything the gateway used to do inline inside an HTTP request moves here, so a
request handler only has to ``submit(kind, payload)`` and return a job id. Keep
handlers idempotent-ish and *never* raise for expected failures — return
``{"error": ...}`` so the job finishes as `succeeded` with a structured error the
caller can render, instead of burning a retry.

Every work kind funnels through the universal mission runtime
(``core.runtime.execute``): ``runtime.turn`` is the canonical kind (chat or
mission, auto-classified), while the legacy ``agent.chat`` /
``agent.autonomous`` kinds are kept for API compatibility and simply pin
``prefer``. As a result a queued job, an inline /command, the CLI and
channels all execute on the same core with the same streaming, cancellation
and steering hooks.

``agent_getter`` is injected by ``gateway.gateway`` to avoid a circular import:
the queue module must not import the app, and the app owns the per-user agents.
"""
from __future__ import annotations

import time
from typing import Any, Optional
from collections.abc import Callable

from core.config import config


def _agent_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": payload.get("model"),
        "mode": payload.get("mode", "agent"),
        "api_key": payload.get("api_key"),
        "base_url": payload.get("base_url"),
    }


def _resolve_key_from_store(payload: dict[str, Any]) -> dict[str, Any]:
    """Swap a stored (provider, key_name) pair for the real key server-side."""
    if payload.get("key_name") and payload.get("provider"):
        try:
            from core.multi_key import multi_key_manager

            entry = multi_key_manager.get_entry(payload["provider"], payload["key_name"]) or {}
            if entry:
                payload = dict(payload)
                payload.setdefault("api_key", entry.get("key"))
                payload.setdefault("base_url", entry.get("base_url"))
                if not payload.get("model") and entry.get("default_model"):
                    payload["model"] = f"{payload['provider']}/{entry['default_model']}"
            else:
                # Surface a missing selected key instead of silently running
                # with no usable model (which produced "key looks configured but
                # the agent doesn't respond" failures).
                print(f"[Gateway] key resolution: no stored key for "
                      f"provider={payload['provider']} name={payload['key_name']!r}")
                payload = dict(payload)
                payload["_key_warning"] = (
                    f"No stored API key named {payload['key_name']!r} for provider "
                    f"{payload['provider']}; add it in the API Keys tab."
                )
        except Exception as exc:
            # Log instead of swallowing: a broken key-store lookup used to turn
            # into a request with no usable key and no diagnostic.
            print(f"[Gateway] key resolution error for {payload.get('provider')}/"
                  f"{payload.get('key_name')}: {type(exc).__name__}: {exc}")
    return payload


def _runtime_execute(
    ctx,
    *,
    prefer: str,
    agent_getter: Callable[..., Any],
) -> dict[str, Any]:
    """Run one request through the universal runtime on behalf of a queued job."""
    from core.runtime import execute as runtime_execute

    payload = _resolve_key_from_store(dict(ctx.payload))
    text = str(payload.get("text") or payload.get("task") or payload.get("message") or "")
    if not text.strip():
        return {"error": "text required"}

    def _emit(event_type: str, data: Optional[dict[str, Any]] = None) -> None:
        try:
            ctx.emit(event_type, dict(data or {}))
        except Exception:
            pass

    max_repairs_raw = payload.get("max_repairs")
    max_repairs = int(max_repairs_raw) if max_repairs_raw not in (None, "") else 2
    budget_raw = payload.get("budget_steps")
    budget_steps = int(budget_raw) if budget_raw not in (None, "") else None

    started = time.time()
    result = runtime_execute(
        text,
        agent_getter=agent_getter,
        platform=payload.get("platform", "api"),
        user_id=payload.get("user_id", "anonymous"),
        model=payload.get("model"),
        mode=payload.get("mode", "agent"),
        api_key=payload.get("api_key"),
        base_url=payload.get("base_url"),
        prefer=prefer,
        on_event=_emit,
        stream=bool(payload.get("stream", getattr(config, "gateway_stream_enabled", True))),
        should_cancel=ctx.should_cancel,
        steer_source=ctx.pending_steers,
        max_repairs=max_repairs,
        budget_steps=budget_steps,
        requirements=payload.get("requirements"),
        domain=payload.get("domain"),
        subgoals=payload.get("subgoals"),
    )
    if not isinstance(result, dict):
        result = {"response": str(result or "")}
    result.setdefault("run_id", ctx.run_id)
    result["job_id"] = ctx.id
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    return result


def make_chat_handler(agent_getter: Callable[..., Any]):
    def chat(ctx) -> dict[str, Any]:
        payload = _resolve_key_from_store(dict(ctx.payload))
        text = str(payload.get("text") or payload.get("message") or "")
        if not text.strip():
            return {"error": "text required"}
        agent = agent_getter(
            payload.get("platform", "api"),
            payload.get("user_id", "anonymous"),
            **_agent_kwargs(payload),
        )
        if payload.get("profile"):
            agent.profile = payload["profile"]
        want_stream = bool(payload.get("stream", getattr(config, "gateway_stream_enabled", True)))
        started = time.time()
        result = _chat_with_optional_kwargs(
            agent, text,
            on_event=ctx.emit,
            stream=want_stream and bool(getattr(config, "gateway_stream_tokens", True)),
            should_cancel=ctx.should_cancel,
            steer_source=ctx.pending_steers,
        )
        result.setdefault("run_id", ctx.run_id)
        result["job_id"] = ctx.id
        result["elapsed_ms"] = int((time.time() - started) * 1000)
        # Full final answer on the run stream: queued (dashboard) clients read
        # the authoritative response from SSE instead of only the HTTP reply.
        ctx.emit("agent_response", {
            "text": str(result.get("response") or "")[:12000],
            "steps": result.get("steps"),
            "tool_calls": list(result.get("tool_calls") or [])[:30],
            "run_kind": result.get("run_kind", "chat"),
        })
        try:
            from core.integrations import maybe_self_heal

            result = maybe_self_heal(result)
        except Exception:
            pass
        if payload.get("talking") or payload.get("speak"):
            try:
                from core.speech import speech_engine

                answer = str(result.get("response") or "")
                if answer:
                    speech = speech_engine.synthesize(
                        answer, payload.get("voice"), int(payload.get("speech_rate") or 165)
                    )
                    speech.pop("path", None)
                    if speech.get("success"):
                        speech["audio_url"] = f"/speech/audio/{speech['audio_id']}"
                        ctx.emit("speech_ready", {"audio_url": speech["audio_url"],
                                                   "backend": speech.get("backend")})
                    result["speech"] = speech
            except Exception as e:
                result["speech"] = {"success": False, "error": str(e)[:200]}
        return result

    return chat


def _chat_with_optional_kwargs(agent, text: str, *, on_event=None, stream: bool = False,
                               should_cancel=None, steer_source=None) -> dict[str, Any]:
    """agent.chat with only the kwargs this agent actually supports."""
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


def make_autonomous_handler(agent_getter: Callable[..., Any]):
    def autonomous(ctx) -> dict[str, Any]:
        """Autonomous goals run on the universal mission runtime (same core as
        ``/command?autonomous``, the CLI and the mission API)."""
        payload = _resolve_key_from_store(dict(ctx.payload))
        text = str(payload.get("text") or payload.get("task") or "")
        if not text.strip():
            return {"error": "text required"}
        ctx.emit("autonomous_started", {"task": text[:200]})
        result = _runtime_execute(ctx, prefer="mission", agent_getter=agent_getter)
        result["autonomous"] = True
        return result

    return autonomous


def make_runtime_turn_handler(agent_getter: Callable[..., Any]):
    """Canonical job kind: one request, auto-classified chat vs mission."""

    def turn(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        prefer = str(payload.get("prefer") or "auto").lower()
        if prefer not in ("auto", "chat", "mission"):
            prefer = "auto"
        ctx.emit("turn_started", {"prefer": prefer,
                                  "text": str(payload.get("text") or "")[:200]})
        return _runtime_execute(ctx, prefer=prefer, agent_getter=agent_getter)

    return turn


def make_mission_start_handler(agent_getter: Callable[..., Any]):
    def mission_start(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        goal = str(payload.get("goal") or payload.get("text") or "")
        if not goal:
            return {"error": "goal required"}
        from core.mission import MissionEngine, mission_engine

        ctx.emit("mission_started", {"goal": goal[:200]})
        engine = MissionEngine(storage_dir=mission_engine.storage_dir)
        report = engine.start_mission(
            goal=goal,
            requirements=payload.get("requirements"),
            domain=payload.get("domain"),
            subgoals=payload.get("subgoals"),
            budget_steps=int(payload.get("budget_steps", 20)),
            on_event=ctx.emit,
        )
        out = report.to_dict()
        out["job_id"] = ctx.id
        out["run_kind"] = "mission"
        ctx.emit("agent_response", {"text": (out.get("response") or "")[:12000],
                                    "run_kind": "mission",
                                    "mission_id": out.get("mission_id"),
                                    "state": out.get("state")})
        return out

    return mission_start


def make_swe_develop_handler(agent_getter: Optional[Callable[..., Any]] = None):
    def swe_develop(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        task = str(payload.get("task") or payload.get("text") or "")
        if not task:
            return {"error": "task required"}
        from core.swe_mode import swe_mode

        ctx.emit("swe_started", {"task": task[:200]})
        # The SWE lifecycle runs its coder phase on the same agent runtime as
        # everything else (real tools, real diffs as evidence) instead of a
        # deterministic template that touched nothing.
        agent = None
        if agent_getter is not None and not payload.get("no_agent"):
            try:
                agent = agent_getter(
                    payload.get("platform", "api"),
                    payload.get("user_id", "anonymous"),
                    **_agent_kwargs(payload),
                )
            except Exception as exc:
                ctx.emit("log", {"level": "warning",
                                 "message": f"agent unavailable for SWE coder phase: {exc}"})
        res = swe_mode.execute(
            task=task,
            max_repairs=int(payload.get("max_repairs", 3)),
            agent=agent,
            on_event=ctx.emit,
            should_cancel=ctx.should_cancel,
            steer_source=ctx.pending_steers,
        )
        out = res.to_dict()
        out["job_id"] = ctx.id
        ctx.emit("agent_response", {"text": (out.get("change_report") or "")[:12000],
                                    "run_kind": "swe"})
        return out

    return swe_develop


def make_research_handler():
    def research(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        query = str(payload.get("query") or payload.get("text") or "")
        if not query:
            return {"error": "query required"}
        from core.research import research_pipeline

        ctx.emit("research_started", {"query": query[:200]})
        out = research_pipeline.run(
            query, max_sources=int(payload.get("max_sources", 6)),
            synthesize=bool(payload.get("synthesize", True)),
        )
        out["job_id"] = ctx.id
        return out

    return research


def make_delegate_handler():
    def delegate(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        tasks = payload.get("tasks")
        goal = payload.get("goal") or payload.get("text") or ""
        from core.delegation import delegation

        ctx.emit("delegation_started", {"tasks": len(tasks or []), "goal": str(goal)[:200]})
        if tasks:
            out = delegation.fanout(
                [str(t) for t in tasks],
                goal=str(goal),
                depth=int(payload.get("depth", 1)),
                on_event=ctx.emit,
                should_cancel=ctx.should_cancel,
            )
        else:
            out = delegation.decompose_and_run(
                str(goal),
                max_children=int(payload.get("max_children", 4)),
                on_event=ctx.emit,
            )
        out["job_id"] = ctx.id
        return out

    return delegate


def make_memory_sweep_handler():
    def sweep(ctx) -> dict[str, Any]:
        from core.memory2 import memory2

        payload = dict(ctx.payload)
        report = memory2.sweep(
            project=payload.get("project") or None,
            dry_run=bool(payload.get("dry_run", False)),
        )
        try:
            from core.skill_manager import skill_manager

            report["skills_pruned"] = skill_manager.prune_stale_skills()
        except Exception:
            pass
        ctx.emit("memory_swept", {"summary": report.get("summary")})
        return report

    return sweep


def make_channel_reply_handler(agent_getter: Callable[..., Any]):
    """Telegram/webhook style job: run the agent, then push the answer back."""

    def channel_reply(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        # Channels (Telegram/Discord/Slack) go through the same universal
        # runtime as the dashboard and CLI — auto-classified, so a goal-like
        # channel message gets the full mission lifecycle.
        runtime_result = _runtime_execute(
            _PseudoCtx(ctx.id, ctx.run_id,
                       {**payload, "platform": payload.get("platform", "telegram")},
                       ctx.emit),
            prefer=str(payload.get("prefer") or "auto"),
            agent_getter=agent_getter,
        )
        chat_result = runtime_result
        answer = str(runtime_result.get("response") or runtime_result.get("error") or "")
        sent = {"delivered": False}
        platform = payload.get("platform", "telegram")
        if platform == "telegram" and answer:
            try:
                from gateway.channels import telegram_send_message

                raw = telegram_send_message(int(payload["chat_id"]), answer)
                if isinstance(raw, dict):
                    ok = bool(raw.get("ok") or raw.get("delivered") or raw.get("message_id"))
                    sent = {"delivered": ok, "detail": {k: v for k, v in raw.items()
                                                        if k in ("ok", "error", "status_code", "stub")}}
                else:
                    sent = {"delivered": bool(raw)}
            except Exception as e:
                sent = {"delivered": False, "error": str(e)[:200]}
        elif platform == "slack" and answer:
            try:
                import requests

                webhook = payload.get("webhook_url") or ""
                if webhook:
                    r = requests.post(webhook, json={"text": answer[:3000]}, timeout=15)
                    sent = {"delivered": r.status_code < 300, "status": r.status_code}
                else:
                    sent = {"delivered": False, "error": "slack webhook_url not provided"}
            except Exception as e:
                sent = {"delivered": False, "error": str(e)[:200]}
        elif not answer:
            sent = {"delivered": False, "error": "nothing to send"}
        elif platform not in ("telegram", "slack"):
            sent = {"delivered": False, "error": f"no outbound adapter for platform '{platform}'"}
        try:
            ctx.emit("channel_delivery", {"platform": platform, **sent})
        except Exception:
            pass
        return {"answer": answer[:4000], "delivery": sent, "chat": chat_result,
                "delivered": bool(sent.get("delivered"))}

    return channel_reply


class _PseudoCtx:
    """Adapter so handlers can be composed (channel_reply wraps the runtime)."""

    def __init__(self, job_id: str, run_id: str, payload: dict[str, Any], emit: Callable) -> None:
        self.id = job_id
        self.run_id = run_id
        self.payload = payload
        self._emit = emit

    def emit(self, event_type: str, data: Optional[dict[str, Any]] = None) -> None:
        try:
            self._emit(event_type, data)
        except Exception:
            pass

    def should_cancel(self) -> bool:
        return False

    def pending_steers(self) -> list[str]:
        return []


def register_handlers(queue, agent_getter: Callable[..., Any], *, overwrite: bool = True) -> dict[str, str]:
    """Register every gateway job kind. Returns the kind → description map."""
    from core.agent_manager import make_agent_computer_handler, make_agent_general_handler
    kinds = {
        "runtime.turn": (make_runtime_turn_handler(agent_getter), "canonical runtime turn: auto-classified chat or full mission (universal core)"),
        "agent.general": (make_agent_general_handler(), "named-agent general task through the universal runtime (role dispatch)"),
        "agent.computer": (make_agent_computer_handler(), "named-agent desktop/computer task (role dispatch)"),
        "agent.chat": (make_chat_handler(agent_getter), "run one agent turn (ReAct loop, streamed events)"),
        "agent.autonomous": (make_autonomous_handler(agent_getter), "goal through the universal mission runtime (plan→execute→verify→repair)"),
        "mission.start": (make_mission_start_handler(agent_getter), "objective-driven mission with verifiers & dynamic budgets"),
        "swe.develop": (make_swe_develop_handler(agent_getter), "repository-level software engineering lifecycle (agent-backed coder phase)"),
        "research.deep": (make_research_handler(), "multi-source research pipeline with citations"),
        "subagent.delegate": (make_delegate_handler(), "hierarchical sub-agent fan-out + aggregation"),
        "memory.sweep": (make_memory_sweep_handler(), "decay/archive/purge pass over typed memory"),
        "channel.reply": (make_channel_reply_handler(agent_getter), "runtime turn + deliver answer back to the channel"),
    }
    for kind, (fn, _desc) in kinds.items():
        queue.register(kind, fn, overwrite=overwrite)
    return {k: v[1] for k, v in kinds.items()}
