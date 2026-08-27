"""Job handlers registered on the gateway queue (intake ⇄ execution contract).

Everything the gateway used to do inline inside an HTTP request moves here, so a
request handler only has to ``submit(kind, payload)`` and return a job id. Keep
handlers idempotent-ish and *never* raise for expected failures — return
``{"error": ...}`` so the job finishes as `succeeded` with a structured error the
caller can render, instead of burning a retry.

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
        except Exception:
            pass
    return payload


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
        result = agent.chat(
            text,
            on_event=ctx.emit,
            stream=want_stream and bool(getattr(config, "gateway_stream_tokens", True)),
            should_cancel=ctx.should_cancel,
        )
        result.setdefault("run_id", ctx.run_id)
        result["job_id"] = ctx.id
        result["elapsed_ms"] = int((time.time() - started) * 1000)
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


def make_autonomous_handler(agent_getter: Callable[..., Any]):
    def autonomous(ctx) -> dict[str, Any]:
        payload = _resolve_key_from_store(dict(ctx.payload))
        text = str(payload.get("text") or payload.get("task") or "")
        if not text.strip():
            return {"error": "text required"}
        agent = agent_getter(
            payload.get("platform", "api"), payload.get("user_id", "anonymous"),
            **_agent_kwargs(payload),
        )
        ctx.emit("autonomous_started", {"task": text[:200]})
        result = agent.autonomous(text, max_repairs=int(payload.get("max_repairs", 2)))
        result["job_id"] = ctx.id
        result["autonomous"] = True
        result.setdefault("run_id", ctx.run_id)
        return result

    return autonomous


def make_mission_start_handler(agent_getter: Callable[..., Any]):
    def mission_start(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        goal = str(payload.get("goal") or payload.get("text") or "")
        if not goal:
            return {"error": "goal required"}
        from core.mission import mission_engine

        ctx.emit("mission_started", {"goal": goal[:200]})
        report = mission_engine.start_mission(
            goal=goal,
            requirements=payload.get("requirements"),
            domain=payload.get("domain"),
            subgoals=payload.get("subgoals"),
            budget_steps=int(payload.get("budget_steps", 20)),
        )
        out = report.to_dict()
        out["job_id"] = ctx.id
        return out

    return mission_start


def make_swe_develop_handler():
    def swe_develop(ctx) -> dict[str, Any]:
        payload = dict(ctx.payload)
        task = str(payload.get("task") or payload.get("text") or "")
        if not task:
            return {"error": "task required"}
        from core.swe_mode import swe_mode

        ctx.emit("swe_started", {"task": task[:200]})
        res = swe_mode.execute(
            task=task,
            max_repairs=int(payload.get("max_repairs", 3)),
        )
        out = res.to_dict()
        out["job_id"] = ctx.id
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
        chat_result = make_chat_handler(agent_getter)(
            _PseudoCtx(ctx.id, ctx.run_id, {**payload, "platform": payload.get("platform", "telegram")}, ctx.emit)
        )
        answer = str(chat_result.get("response") or chat_result.get("error") or "")
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
    """Adapter so handlers can be composed (channel_reply wraps chat)."""

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


def register_handlers(queue, agent_getter: Callable[..., Any], *, overwrite: bool = True) -> dict[str, str]:
    """Register every gateway job kind. Returns the kind → description map."""
    kinds = {
        "agent.chat": (make_chat_handler(agent_getter), "run one agent turn (ReAct loop, streamed events)"),
        "agent.autonomous": (make_autonomous_handler(agent_getter), "plan→act→verify→repair loop for a goal"),
        "mission.start": (make_mission_start_handler(agent_getter), "objective-driven mission with verifiers & dynamic budgets"),
        "swe.develop": (make_swe_develop_handler(), "repository-level software engineering lifecycle"),
        "research.deep": (make_research_handler(), "multi-source research pipeline with citations"),
        "subagent.delegate": (make_delegate_handler(), "hierarchical sub-agent fan-out + aggregation"),
        "memory.sweep": (make_memory_sweep_handler(), "decay/archive/purge pass over typed memory"),
        "channel.reply": (make_channel_reply_handler(agent_getter), "agent turn + deliver answer back to the channel"),
    }
    for kind, (fn, _desc) in kinds.items():
        queue.register(kind, fn, overwrite=overwrite)
    return {k: v[1] for k, v in kinds.items()}
