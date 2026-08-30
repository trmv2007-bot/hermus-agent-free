"""Main Agent Loop - Free Hermes Clone
Multi-step ReAct tool loop + auto tool registry + semantic memory hooks.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from .config import config
from .llm import FreeLLM
from .memory import memory
from .skill_manager import skill_manager
from .task_tracker import task_tracker
from .modes import AgentMode, get_mode_config
from .tool_registry import tool_registry
from .run_events import record_issue
from .run_hooks import CancelledRun, CancelToken, make_emitter


class HermusAgent:
    """Free Hermes-like agent - self-improving with memory, skills, multi-step tools."""

    def __init__(
        self,
        model: str = None,
        session_id: str = None,
        mode: str = None,
        max_steps: int = None,
        api_key: str = None,
        base_url: str = None,
    ):
        self.model_name = model or config.model
        self._model_pinned = model is not None
        self.llm = FreeLLM(self.model_name, api_key=api_key, base_url=base_url)
        self.session_id = session_id or (
            f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"
        )
        self.trajectory: list[dict] = []
        self.plan_override = None  # Phase 4: resume an existing plan instead of drafting a new one
        self.max_steps = max_steps or getattr(config, "max_tool_steps", 8)

        if mode is None:
            try:
                from .skin_engine import skin_engine

                mode = skin_engine.get_current_mode()
            except Exception:
                mode = "agent"
        try:
            self.mode = AgentMode(mode.lower().replace("_", "-"))
        except Exception:
            self.mode = AgentMode.AGENT
        self.mode_config = get_mode_config(self.mode)

        # Load tool registry once
        tool_registry.load()
        self.tools = self._get_tools()
        self.agent_tracker_id = None

        # Architecture upgrades: active project + persona profile
        try:
            from .workspace import workspace

            self.project = workspace.active_project()
        except Exception:
            self.project = getattr(config, "project", "default") or "default"
        self.profile = getattr(config, "profile", "") or ""

        print(
            f"[Hermus Free] Session {self.session_id} | Model {self.model_name} | "
            f"Mode {self.mode.value} ({self.mode_config.name}) | "
            f"Tools {len(self.tools)} | max_steps={self.max_steps} | Free stack"
        )
        try:
            self.agent_tracker_id = task_tracker.add_agent(
                agent_id=self.session_id,
                name=f"agent_{self.mode.value}_{self.session_id[:6]}",
                model=self.model_name,
                persona=self.mode.value,
                task=f"Mode: {self.mode_config.name} - {self.mode_config.description[:60]}",
            )
        except Exception as exc:
            record_issue("telemetry", "agent_register", exc, retryable=False,
                         fallback="agent runs untracked this session")

    def _get_tools(self) -> list[dict]:
        """Definitions from auto-discovered registry, filtered by mode."""
        allowed = self.mode_config.tools_allowed
        if "all" in allowed:
            return tool_registry.get_definitions(allowed={"all"})
        if "none" in allowed:
            defs: list[dict] = []
        else:
            defs = tool_registry.get_definitions(allowed=set(allowed))
        # User-defined custom APIs are available in every mode that allows them
        # (agent, chat, multi-agent, multi-chat) — even when the mode has no
        # other tools. The registry already includes them for "all".
        if self.mode_config.use_custom_api:
            try:
                from .custom_api import custom_api_manager

                existing = {d.get("function", {}).get("name") for d in defs}
                for tdef in custom_api_manager.get_tool_definitions():
                    if tdef.get("function", {}).get("name") not in existing:
                        defs.append(tdef)
            except Exception:
                pass
        return defs

    def reload_tools(self):
        """Force reload registry (e.g. after MCP connect)."""
        tool_registry.load(force=True)
        self.tools = self._get_tools()
        return {"tools": len(self.tools)}

    def _custom_api_signature(self) -> str:
        """Compact signature of the current custom APIs (name/url/token) so we
        can detect additions/removals made via Settings between messages."""
        try:
            from .custom_api import custom_api_manager

            return json.dumps(
                [
                    (a.get("name"), a.get("url"), a.get("auth", {}).get("token") or a.get("auth", {}).get("value") or "")
                    for a in custom_api_manager.list_apis()
                ],
                sort_keys=True,
                default=str,
            )
        except Exception:
            return ""

    def _execute_tool(self, name: str, args: dict) -> dict:
        """Execute via central registry — all tools including full pentest map."""
        return tool_registry.execute(name, args or {})

    def _apply_router(self, user_message: str) -> Optional[dict]:
        """Model Router 2.0: swap the LLM to the best available model for this turn.

        Returns the selection dict on success, or None when routing is skipped
        (mock provider, no workers, or same model).
        """
        try:
            if getattr(self.llm, "provider", "") == "mock":
                return None
            from .router2 import router2
            from .llm import FreeLLM

            sel = router2.select(user_message)
            if not sel.get("success"):
                return None
            new_ref = sel["model"]
            if new_ref == self.model_name:
                return sel
            new_llm = FreeLLM(new_ref)
            self.llm = new_llm
            self.model_name = new_ref
            print(f"[Router] {sel['task_type']} -> {new_ref} ({sel['reason']})")
            return sel
        except Exception as e:
            print(f"[Router] skipped ({e})")
            return None

    def _build_system_prompt(self, user_message: str = "", emit=None) -> str:
        curated = memory.get_curated_memory(limit=10)
        curated_text = (
            "\n".join([f"- {m['key']}: {m['value'][:200]}" for m in curated])
            if curated
            else "No curated memory yet."
        )

        user_model = memory.load_user_model()
        user_model_text = json.dumps(user_model, indent=2)[:1000] if user_model else "No user model yet."

        skills = skill_manager.list_skills()
        skills_text = ", ".join([s["name"] for s in skills[:15]]) if skills else "No skills yet."

        nudges = memory.periodic_nudges()
        nudges_text = "\n".join(nudges) if nudges else "No nudges."

        tool_count = len(self.tools)

        # Lessons loop (Phase 3): past corrections/failures injected into the prompt
        lessons_block = ""
        try:
            from .reasoning.lessons import lessons_store

            lessons_block = lessons_store.to_prompt_block(user_message)
        except Exception as exc:
            record_issue("memory", "lessons_prompt", exc, retryable=False,
                         fallback="turn continues without lessons block")

        # Memory 2.0 (architecture upgrade): hybrid recall + decay + eviction
        memory2_block = ""
        if getattr(config, "memory2_enabled", True):
            try:
                from .memory2 import memory2

                ctx = memory2.recall_context(user_message, limit=5, project=self.project)
                memory2_block = ctx.get("text") or ""
                if emit is not None:
                    emit(
                        "memory_recalled",
                        {
                            "mode": ctx.get("mode"),
                            "kept": len(ctx.get("kept") or []),
                            "evicted": len(ctx.get("evicted") or []),
                            "ids": (ctx.get("ids") or [])[:12],
                            "tokens": ctx.get("tokens"),
                            "budget_tokens": ctx.get("budget_tokens"),
                            "index": ctx.get("index"),
                            "preview": [
                                {"id": m.get("id"), "kind": m.get("kind"),
                                 "score": m.get("score"),
                                 "rrf": m.get("rrf_score"),
                                 "text": (m.get("content") or "")[:120]}
                                for m in (ctx.get("kept") or [])[:5]
                            ],
                        },
                    )
            except Exception as exc:
                record_issue("memory", "memory2_recall", exc, retryable=False,
                             fallback="turn continues without memory2 context block")
                memory2_block = ""

        # Profile persona (architecture upgrade)
        persona_block = ""
        if self.profile:
            try:
                from .profiles import profile_manager

                persona_block = f"\nPersona ({self.profile}):\n{profile_manager.system_prompt(self.profile)}\n"
            except Exception:
                persona_block = ""

        return f"""You are Hermus Agent Free - a self-improving AI agent that grows with the user.

You have:
- Multi-step tool use (ReAct): you may call tools across multiple rounds until the task is done (max {self.max_steps} steps)
- Persistent memory (SQLite FTS5) + semantic/hybrid search (embeddings)
- Typed long-term memory (episodic/semantic/procedural/project) via memory2_recall / memory2_remember
- Auto-created skills (skill_list / skill_use with task context)
- MCP tools when configured (mcp_list_servers / mcp_connect_all)
- {tool_count} tools registered (browser, vision, voice, internet eyes, pentest, backends, research, screen, etc.)

Curated Memory:
{curated_text}

User Model:
{user_model_text}

Available Skills:
{skills_text}

Periodic Nudges:
{nudges_text}
{lessons_block}
{memory2_block}
{persona_block}
Rules:
- Use tools when needed; do not hallucinate facts you can look up
- After tools return, continue reasoning; call more tools if needed
- When finished, respond with a clear final answer and NO further tool calls
- Prefer skill_use for known workflows; memory_search/hybrid for past context
- Prefer research_deep for multi-source questions needing citations
- Prefer embeddings_ingest + embeddings_search for document Q&A
- You are free, MIT, no paywall — Ollama / Groq / HF
- Session: {self.session_id}
- Model: {self.model_name}
- Mode: {self.mode.value}
- Project: {self.project}
"""

    def _format_tool_result(self, name: str, result: Any, limit: int = 3000) -> str:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            text = str(result)
        if len(text) > limit:
            text = text[:limit] + "...(truncated)"
        return f"Tool {name} returned:\n{text}"

    def chat(
        self,
        user_message: str,
        *,
        on_event=None,
        stream: bool = False,
        should_cancel=None,
        steer_source=None,
    ) -> dict[str, Any]:
        """Multi-step agent loop: plan → tool calls → observe → repeat → final answer.

        ``on_event(event_type, data)`` receives lifecycle events (steps, tool
        calls/results, llm deltas, memory recall, skill harvest, verification) so
        the gateway can stream them over SSE/WebSocket. ``stream=True`` requests
        token-level deltas from the model. ``should_cancel`` is polled at every
        step boundary for cooperative cancellation. ``steer_source`` is an
        optional callable returning newly queued mid-run instructions (see
        ``RunBus.pending_steers``); they are injected into the conversation at
        the next step boundary so /run/steer actually reaches the model.
        """
        emit = make_emitter(on_event)
        cancel = CancelToken(should_cancel)

        def _drain_steers() -> list[str]:
            if steer_source is None:
                return []
            try:
                drained = steer_source()
                return [str(s) for s in (drained or []) if str(s).strip()]
            except Exception as exc:
                record_issue("agent", "steer_source", exc,
                             retryable=False, fallback="steering skipped this step")
                return []
        emit(
            "turn_started",
            {"session": self.session_id, "model": self.model_name,
             "mode": self.mode.value, "project": self.project,
             "stream": bool(stream), "chars": len(user_message or "")},
        )
        # Pick up custom APIs added/removed via Settings since this agent started
        sig = self._custom_api_signature()
        if sig != getattr(self, "_custom_api_sig", None):
            self._custom_api_sig = sig
            self.reload_tools()

        task_id = None
        try:
            task_id = task_tracker.add_task(
                task_id=f"chat_{self.session_id[:6]}_{datetime.now().strftime('%H%M%S')}",
                task_type="chat",
                description=user_message[:100],
                model=self.model_name,
                agent=self.session_id,
            )
            task_tracker.update_agent(
                self.agent_tracker_id,
                task=user_message[:100],
                status="running",
                progress="Thinking...",
            )
        except Exception as exc:
            record_issue("telemetry", "task_tracker_add", exc, retryable=False,
                         fallback="turn continues without task tracking")

        # Counsel System (Phases 0-2): for hard tasks, convene the council of AIs
        # instead of answering alone. Falls back to the normal loop on any failure.
        try:
            from .reasoning.governor import governor

            if governor.should_use_council(user_message, mode=self.mode.value):
                cc = governor.council_config(user_message, mode=self.mode.value)
                print(f"[Counsel] difficulty={cc['difficulty']} -> convening council "
                      f"({cc['max_members']} members, {cc['max_rounds']} rounds)")
                try:
                    task_tracker.update_agent(
                        self.agent_tracker_id, status="running", progress="Council convening..."
                    )
                except Exception:
                    pass
                from .counsel.council import CouncilSession

                cs = CouncilSession(
                    user_message,
                    model=self.model_name,
                    difficulty=cc["difficulty"],
                    max_members=cc["max_members"],
                    max_rounds=cc["max_rounds"],
                    execute=True,
                )
                result = cs.run()
                if result and result.get("final_answer"):
                    return {
                        "session_id": self.session_id,
                        "response": result["final_answer"],
                        "tool_results": [
                            {
                                "tool": "council",
                                "args": {"goal": user_message, "difficulty": cc["difficulty"]},
                                "result": {
                                    "session_id": result.get("session_id"),
                                    "members": result.get("members"),
                                    "votes": result.get("votes"),
                                    "replanned": result.get("replanned"),
                                    "steps": result.get("step_results"),
                                },
                            }
                        ],
                        "tool_calls": ["council"],
                        "steps": result.get("transcript_turns", 1),
                        "max_steps": self.max_steps,
                        "council": result,
                        "mode": self.mode.value,
                        "tools_available": len(self.tools),
                        "strategy": "council",
                        "strategy_meta": {"strategy": "council"},
                    }
        except Exception as e:
            print(f"[Counsel] skipped ({e}) - falling back to normal agent loop")

        # Multi-agent / multi-chat modes: distribute across models+keys when beneficial
        if self.mode in (AgentMode.MULTI_AGENT, AgentMode.MULTI_CHAT) and self.mode_config.use_multi_ai:
            fleet_result = self._maybe_fleet_distribute(user_message)
            if fleet_result is not None:
                return fleet_result

        # Model Router 2.0 (architecture upgrade): per-turn model selection
        routed = None
        if not self._model_pinned and getattr(config, "router2_enabled", True):
            routed = self._apply_router(user_message)

        memory.add_session_message(self.session_id, "user", user_message)
        self.trajectory.append({"role": "user", "content": user_message, "tool_calls": []})

        # Lessons loop (Phase 3): user pushing back on a previous answer -> lesson
        try:
            if len(self.trajectory) >= 3:  # only when a prior exchange exists
                from .reasoning.lessons import lessons_store

                lessons_store.distill_user_correction(user_message)
        except Exception:
            pass

        # Index user turn into semantic memory (best-effort)
        try:
            from .embeddings import embedding_store

            embedding_store.add_text(
                user_message,
                metadata={"session_id": self.session_id, "role": "user"},
                source=f"session:{self.session_id}",
            )
        except Exception as exc:
            record_issue("memory", "embeddings_index_user", exc, retryable=False,
                         fallback="user turn not indexed in semantic memory")

        system_prompt = self._build_system_prompt(user_message)

        # DeepThink plan-first (Phase 0): write an explicit plan for multi-step tasks
        # Phase 4: plan_override resumes an existing plan (hermus plan resume)
        plan = None
        budget_steps = self.max_steps
        try:
            from .reasoning.governor import governor
            from .reasoning.scaffold import plan_builder

            budget_steps = governor.step_budget(user_message, mode=self.mode.value)
            if self.plan_override is not None:
                plan = self.plan_override
                if plan and plan.steps:
                    system_prompt += (
                        "\n\nResuming explicit plan (DeepThink):\n"
                        + plan.to_prompt()
                        + "\nWork through the plan from the first not-done step; you may deviate if evidence demands it."
                    )
                    print(f"[DeepThink] Resuming plan ({len(plan.steps)} steps)")
            elif governor.should_plan_first(user_message, mode=self.mode.value):
                plan = plan_builder.build_plan(
                    user_message,
                    session_id=self.session_id,
                    difficulty=governor.classify_difficulty(user_message),
                )
                if plan and plan.steps:
                    plan.save()
                    system_prompt += (
                        "\n\nExplicit plan (DeepThink):\n"
                        + plan.to_prompt()
                        + "\nWork through the plan; you may deviate if evidence demands it."
                    )
                    print(f"[DeepThink] Plan drafted ({len(plan.steps)} steps)")
        except Exception as e:
            print(f"[DeepThink] plan-first skipped ({e})")

        # Hybrid memory recall
        memory_results = []
        memory_summary = ""
        try:
            from .embeddings import embedding_store

            hybrid = embedding_store.hybrid_search(user_message, limit=3)
            memory_results = hybrid.get("results") or []
            memory_summary = hybrid.get("summary") or ""
            if memory_results and not memory_summary:
                memory_summary = "\n".join(
                    f"- ({r.get('score', '?')}) {(r.get('content') or '')[:200]}"
                    for r in memory_results[:3]
                )
        except Exception:
            memory_results = memory.search_sessions(user_message, limit=3)
            memory_summary = (
                memory.summarize_search_results(user_message, memory_results)
                if memory_results
                else ""
            )

        messages: list[dict] = [
            {
                "role": "system",
                "content": system_prompt
                + (f"\n\nRelevant memory:\n{memory_summary}" if memory_summary else ""),
            },
        ]
        try:
            from .harness import harness as _harness

            _prep = _harness.prepare_turn(
                self.session_id, user_message, messages, project=str(self.project or "")
            )
            messages = _prep.get("messages") or messages
        except Exception as exc:
            record_issue("harness", "prepare_turn", exc, retryable=False,
                         fallback="turn continues without harness context")
        # Recent trajectory context
        for turn in self.trajectory[-12:]:
            role = turn.get("role") or "user"
            if role == "tool":
                # Represent prior tool outcomes as user observations for providers without tool role
                messages.append(
                    {"role": "user", "content": turn.get("content", "")[:2000]}
                )
            elif role in ("user", "assistant", "system"):
                messages.append({"role": role, "content": turn.get("content", "")[:4000]})

        all_tool_results: list[dict] = []
        steps = 0
        final_content = ""
        last_usage = {}

        # ---- Multi-step ReAct loop ----
        while steps < budget_steps:
            if cancel.cancelled:
                emit("run_cancelled", {"step": steps})
                raise CancelledRun("agent run cancelled")
            # Mid-run steering: drain new instructions queued since the last
            # step (POST /run/steer) and inject them into the conversation.
            new_steers = _drain_steers()
            if new_steers:
                steer_block = (
                    "MID-RUN STEERING — the user added the following constraint(s) "
                    "while you were working. Treat them as the newest instruction "
                    "and adjust your remaining work accordingly:\n- "
                    + "\n- ".join(new_steers)
                )
                messages.append({"role": "user", "content": steer_block})
                self.trajectory.append({"role": "user", "content": steer_block, "tool_calls": []})
                emit("steer_applied", {"step": steps, "count": len(new_steers),
                                       "texts": [s[:200] for s in new_steers]})
            steps += 1
            try:
                task_tracker.update_agent(
                    self.agent_tracker_id,
                    status="running",
                    progress=f"Step {steps}/{budget_steps}",
                )
            except Exception:
                pass

            emit("step_started", {"step": steps, "of": budget_steps})
            # Only pass tools while we still have budget for another tool round
            use_tools = self.tools if self.tools else None
            if stream:
                response = self.llm.stream_chat(
                    messages, tools=use_tools, on_delta=_delta_sink(emit, steps)
                )
            else:
                response = self.llm.chat(messages, tools=use_tools)
            last_usage = getattr(response, "usage", None) or last_usage
            emit(
                "step_observed",
                {"step": steps, "tool_calls": len(getattr(response, "tool_calls", None) or []),
                 "chars": len(getattr(response, "content", "") or "")},
            )

            if not response.tool_calls:
                # Before committing to a final answer, honor steering that
                # arrived while the model was generating: the user's newest
                # instruction takes precedence over a stale answer.
                late_steers = _drain_steers()
                if late_steers and steps < budget_steps:
                    steer_block = (
                        "MID-RUN STEERING — the user added the following constraint(s) "
                        "while you were working. Treat them as the newest instruction "
                        "and adjust your answer/work accordingly:\n- "
                        + "\n- ".join(late_steers)
                    )
                    messages.append({"role": "assistant", "content": response.content or ""})
                    messages.append({"role": "user", "content": steer_block})
                    self.trajectory.append({"role": "assistant", "content": response.content or "",
                                            "tool_calls": []})
                    self.trajectory.append({"role": "user", "content": steer_block, "tool_calls": []})
                    emit("steer_applied", {"step": steps, "count": len(late_steers),
                                           "phase": "finalizing",
                                           "texts": [s[:200] for s in late_steers]})
                    continue
                final_content = response.content or ""
                break

            # Record assistant tool-call turn
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": response.tool_calls,
                }
            )
            self.trajectory.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": response.tool_calls,
                }
            )

            # Execute each tool call
            observations = []
            for tc in response.tool_calls:
                tool_name = tc.get("name")
                tool_args = tc.get("arguments", {})
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except Exception:
                        tool_args = {}
                print(f"[Tool step {steps}] {tool_name}({tool_args})")
                _t0 = time.time()
                emit("tool_call", {"step": steps, "tool": tool_name,
                                   "args": _safe_trunc_args(tool_args)})
                result = self._execute_tool(tool_name, tool_args)
                emit(
                    "tool_result",
                    {
                        "step": steps, "tool": tool_name,
                        "ms": int((time.time() - _t0) * 1000),
                        "error": _result_failed(result),
                        "preview": _preview(result),
                    },
                )
                try:
                    from .harness import harness as _harness

                    _harness.observe_tool(self.session_id, tool_name, tool_args)
                except Exception as exc:
                    record_issue("harness", "observe_tool", exc, retryable=False,
                                 fallback=f"observation for tool '{tool_name}' skipped")
                all_tool_results.append(
                    {"tool": tool_name, "args": tool_args, "result": result, "step": steps}
                )
                # Lessons loop (Phase 3): tool failures become lessons immediately
                try:
                    rtext = json.dumps(result, default=str)
                    if "error" in rtext[:300].lower() or "failed" in rtext[:300].lower():
                        from .reasoning.lessons import lessons_store

                        lessons_store.distill_tool_failure(tool_name, rtext[:150])
                except Exception:
                    pass
                obs = self._format_tool_result(tool_name, result)
                observations.append(obs)

                memory.add_session_message(
                    self.session_id,
                    "tool",
                    f"Tool {tool_name} result: {json.dumps(result, default=str)[:1000]}",
                    tool_calls=[tc],
                    metadata={"tool": tool_name, "step": steps},
                )
                self.trajectory.append(
                    {
                        "role": "tool",
                        "content": f"{tool_name} result: {json.dumps(result, default=str)[:800]}",
                        "tool_calls": [tc],
                    }
                )

            # Feed observations back for next reasoning step
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Tool results (step "
                        f"{steps}):\n\n"
                        + "\n\n".join(observations)
                        + "\n\nContinue. Call more tools if needed, otherwise give the final answer."
                    ),
                }
            )
        else:
            # Hit max steps with pending work — force a final synthesis without tools
            print(f"[Agent] max_steps={self.max_steps} reached, forcing final answer")
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"You hit the max tool steps ({self.max_steps}). "
                        "Give the best final answer now from the information gathered. No more tools."
                    ),
                }
            )
            final_resp = self.llm.chat(messages, tools=None)
            final_content = final_resp.content or ""
            last_usage = getattr(final_resp, "usage", None) or last_usage

        if not final_content:
            final_content = "(No response generated)"

        # The model can only act agentically when the provider actually accepts
        # tool definitions. When the provider stripped them (supports_tools
        # false), say so instead of letting the model claim it "cannot do
        # agentic tasks" with no visible reason.
        tools_disabled = getattr(self.llm, "last_tools_disabled_reason", None)
        if tools_disabled and self.tools:
            emit("tools_disabled", {"reason": tools_disabled, "provider": getattr(self.llm, "provider", "")})
            final_content += (
                "\n\n---\n⚠️ *This reply ran without tool access: "
                + tools_disabled
                + ". Switch to a tool-calling provider (Groq, Ollama, or the local engine) to let Hermus act on your behalf.*"
            )

        # DeepThink deliberation strategy (Phase 3): reflexion / verify / self-consistency
        strategy = "none"
        strategy_meta: dict = {}
        try:
            from .reasoning.governor import governor as _gov
            from .reasoning.strategies import apply_strategy

            strategy = _gov.strategy_for(user_message, mode=self.mode.value)
            if strategy != "none":
                print(f"[DeepThink] strategy={strategy} refining final answer")
                new_content, strategy_meta = apply_strategy(
                    strategy, user_message, all_tool_results, final_content, model=self.model_name
                )
                if new_content and new_content.strip():
                    final_content = new_content
        except Exception as e:
            print(f"[DeepThink] strategy skipped ({e})")

        # Persist assistant reply (Phase 4, P6: trajectory tagging)
        try:
            from .reasoning.governor import governor as _gov2

            _difficulty = _gov2.classify_difficulty(user_message)
        except Exception:
            _difficulty = None
        memory.add_session_message(
            self.session_id,
            "assistant",
            final_content,
            tag={
                "strategy": strategy,
                "difficulty": _difficulty,
                "plan": plan.to_dict() if plan else None,
                "council": False,
            },
        )
        self.trajectory.append(
            {"role": "assistant", "content": final_content, "tool_calls": []}
        )

        try:
            from .embeddings import embedding_store

            embedding_store.add_text(
                final_content[:2000],
                metadata={"session_id": self.session_id, "role": "assistant"},
                source=f"session:{self.session_id}",
            )
        except Exception:
            pass

        # Token usage
        try:
            if last_usage:
                memory.add_token_usage(self.session_id, last_usage)
        except Exception as exc:
            record_issue("telemetry", "token_usage", exc, retryable=False,
                         fallback="token usage not recorded")

        try:
            if task_id:
                task_tracker.complete_task(
                    task_id, status="done", result=final_content[:200]
                )
            task_tracker.update_agent(
                self.agent_tracker_id, status="idle", progress="Done", task="idle"
            )
        except Exception:
            pass

        # Background self-improvement
        try:
            from .self_improvement import self_improvement
            import threading

            if (
                not self_improvement.background_thread
                or not self_improvement.background_thread.is_alive()
            ):
                self_improvement.start_background_idle_checker()

            def background_reflection():
                try:
                    tool_failures = sum(
                        1
                        for turn in self.trajectory
                        if "error"
                        in turn.get("content", "").lower()
                        or "failed" in turn.get("content", "").lower()
                    )
                    if len(self.trajectory) >= 3 or tool_failures > 0:
                        self_improvement.run_idle_reflection(
                            trajectory=self.trajectory[-20:]
                        )
                except Exception as e:
                    print(f"[Self-Improvement] Background reflection failed: {e}")

            threading.Thread(target=background_reflection, daemon=True).start()
        except Exception as e:
            print(f"[Self-Improvement] Failed to trigger: {e}")

        # Verification is computed *before* the skill forge so distillation is
        # evidence-gated: we never learn a procedure from a task that failed.
        verification_early = None
        if getattr(config, "autonomous_enabled", False) or getattr(config, "skill_forge_enabled", True):
            verification_early = self._verify_final(user_message, final_content)
            if verification_early is not None:
                emit("verification", verification_early)

        # Skill forge (architecture upgrade): evaluate → distill → validate → install
        skill_created = None
        if getattr(config, "skill_forge_enabled", True):
            try:
                from .skill_forge import skill_forge

                emit("skill_harvest_started", {"steps": steps, "turns": len(self.trajectory)})
                skill_created = skill_forge.harvest(
                    user_message,
                    self.trajectory,
                    verification=verification_early,
                    tool_results=all_tool_results,
                    session_id=self.session_id,
                    final_answer=final_content,
                )
                if skill_created.get("created") or skill_created.get("installed"):
                    emit("skill_created", {
                        "name": skill_created.get("name"),
                        "path": skill_created.get("path"),
                        "stage": skill_created.get("stage"),
                        "evaluation": skill_created.get("evaluation"),
                    })
                    print(f"[SkillForge] {skill_created.get('stage') or 'installed'}: "
                          f"{skill_created.get('name') or skill_created.get('merged_into')}")
                else:
                    emit("skill_skipped", {"stage": skill_created.get("stage"),
                                           "reasons": (skill_created.get("evaluation") or {}).get("reasons"),
                                           "merged_into": skill_created.get("merged_into")})
            except Exception as e:
                record_issue("skill_forge", "harvest", e, retryable=False,
                             fallback="legacy auto-skill path")
                print(f"[SkillForge] skipped ({e}) - falling back to legacy auto-skill")
                skill_created = None
        if skill_created is None and skill_manager.should_create_skill(self.trajectory):
            print("[Skill] Complex trajectory detected, auto-creating skill...")
            skill_created = skill_manager.create_skill_from_trajectory(
                self.trajectory, self.session_id
            )

        # Curate memory heuristic
        try:
            if "remember" in user_message.lower() or len(all_tool_results) >= 2:
                memory.curate_memory(
                    key=f"session_{self.session_id[:8]}_topic",
                    value=user_message[:200] + " -> " + final_content[:300],
                    source_session=self.session_id,
                    importance=6,
                )
        except Exception as exc:
            record_issue("memory", "curate", exc, retryable=False,
                         fallback="turn not curated into memory")

        # Memory 2.0 (architecture upgrade): auto-persist typed memories
        self._persist_memory2(user_message, final_content, all_tool_results)

        # Optional autonomous verify gate (non-blocking metadata)
        verification = verification_early

        emit(
            "turn_finished",
            {"steps": steps, "tools": len(all_tool_results), "chars": len(final_content or ""),
             "skill": (skill_created or {}).get("name"),
             "verified": (verification or {}).get("verified")},
        )
        return {
            "session_id": self.session_id,
            "response": final_content,
            "tool_results": all_tool_results,
            "tool_calls": [tr["tool"] for tr in all_tool_results],
            "steps": steps,
            "max_steps": self.max_steps,
            "skill_created": skill_created,
            "memory_results": memory_results[:3] if memory_results else [],
            "tools_available": len(self.tools),
            "plan": plan.to_dict() if plan else None,
            "strategy": strategy,
            "strategy_meta": strategy_meta,
            "router": routed,
            "project": self.project,
            "verification": verification,
            "events": None,
        }

    def _persist_memory2(self, user_message: str, final_content: str,
                         tool_results: list[dict]) -> None:
        """Auto-persist typed memories after a turn (best-effort, offline-safe)."""
        if not getattr(config, "memory2_enabled", True):
            return
        try:
            from .memory2 import memory2

            project = self.project
            # episodic: what happened this turn
            n_tools = len(tool_results)
            failed = sum(1 for tr in tool_results if "error" in str(tr.get("result", "")).lower()[:300])
            memory2.remember(
                "episodic",
                f"User asked: {user_message[:200]}. Agent used {n_tools} tool(s) "
                f"and {'failed' if failed else 'succeeded'}.",
                project=project, success=(failed == 0),
            )
            # semantic: explicit facts / preferences
            low = user_message.lower()
            if any(k in low for k in ("remember that", "i prefer", "i like", "my name is", "i use", "my favorite")):
                memory2.remember("semantic", user_message[:300], project=project, importance=7)
            # procedural: successful multi-tool sequences become recipes
            if n_tools >= 2 and failed == 0:
                chain = " -> ".join(tr["tool"] for tr in tool_results[:5])
                memory2.remember(
                    "procedural",
                    f"For '{user_message[:120]}', a working tool sequence: {chain}",
                    project=project, success=True,
                )
        except Exception as exc:
            record_issue("memory", "memory2_persist", exc, retryable=False,
                         fallback="typed memories for this turn not persisted")

    def _verify_final(self, user_message: str, final_content: str) -> dict:
        """Lightweight verification of the final answer (autonomous gate)."""
        try:
            from .verifiers import MarkerVerifier

            v = MarkerVerifier().verify(user_message, final_content)
            return {"verified": v.get("ok", True), "problems": v.get("problems", [])}
        except Exception as e:
            return {"verified": True, "error": str(e)}

    def autonomous(self, task: str, max_repairs: int = 2, *,
                   on_event=None, should_cancel=None, steer_source=None) -> dict[str, Any]:
        """Run a goal through the universal mission runtime.

        This used to spin up a private ``AutonomousRunner`` — a second,
        weaker autonomous system that ran in parallel with the MissionEngine
        (different budget, different verification, different repair loop), so
        behavior depended on which entry point a request happened to use.
        ``autonomous()`` now routes through the same MissionEngine the
        mission API, gateway queue and CLI use; each DAG stage reuses THIS
        agent's ReAct loop (session memory, model and profile preserved),
        with evidence-gated node success and mid-run steering.

        The returned dict keeps the legacy contract (``status``, ``phases``,
        ``steps``, ``verified``, ``repairs``, ``final_answer``) and adds the
        canonical ``response`` plus the full mission report under ``mission``.

        Set ``HERMUS_MISSION_RUNTIME=0`` to fall back to the old
        AutonomousRunner path.
        """
        from .runtime import execute as runtime_execute

        return runtime_execute(
            task,
            agent=self,
            prefer="mission",
            max_repairs=max_repairs,
            on_event=on_event,
            should_cancel=should_cancel,
            steer_source=steer_source,
        )

    def _maybe_fleet_distribute(self, user_message: str) -> Optional[dict[str, Any]]:
        """
        In multi-agent / multi-chat modes, auto-dispatch hard goals across
        multiple models and API keys via the model fleet.
        Skip for short greetings / simple questions.
        """
        msg = (user_message or "").strip()
        if len(msg) < 40:
            return None
        # Explicit commands
        lower = msg.lower()
        force = any(
            k in lower
            for k in (
                "multi model",
                "multi-model",
                "multiple models",
                "multiple ai",
                "fleet",
                "fanout",
                "debate",
                "no matter how",
                "use all keys",
                "use multiple",
            )
        )
        complexish = (
            force
            or len(msg) > 120
            or any(k in lower for k in ("research", "compare", "plan", "analyze", " and ", "1.", "2."))
        )
        if not complexish:
            return None

        try:
            from .model_fleet import model_fleet

            workers = model_fleet.list_workers().get("count", 0)
            if workers < 2 and not force:
                return None

            strategy = "auto"
            try:
                # Deterministic orchestrator (Phase 4): table-driven strategy
                from .counsel.router import router as _router

                strategy = _router.fleet_strategy(msg, mode=self.mode.value)
            except Exception:
                strategy = "fanout" if self.mode == AgentMode.MULTI_CHAT else "auto"

            print(f"[Fleet] Multi-mode dispatch strategy={strategy} workers≈{workers}")
            try:
                task_tracker.update_agent(
                    self.agent_tracker_id,
                    status="running",
                    progress=f"Fleet {strategy} across models/keys",
                )
            except Exception:
                pass

            result = model_fleet.auto_distribute(msg, strategy=strategy, max_workers=4)
            final = (
                result.get("consensus")
                or result.get("merged")
                or (result.get("winner") or {}).get("response")
                or ""
            )
            if not final and result.get("results"):
                chunks = []
                for r in result["results"]:
                    if r.get("success") and r.get("response"):
                        chunks.append(f"### {r.get('model')}\n{r['response'][:1500]}")
                final = "\n\n".join(chunks) if chunks else str(result.get("error") or "Fleet finished with no text")

            memory.add_session_message(self.session_id, "user", user_message)
            memory.add_session_message(self.session_id, "assistant", final)
            self.trajectory.append({"role": "user", "content": user_message, "tool_calls": []})
            self.trajectory.append({"role": "assistant", "content": final, "tool_calls": []})

            try:
                task_tracker.update_agent(
                    self.agent_tracker_id, status="idle", progress="Fleet done", task="idle"
                )
            except Exception:
                pass

            return {
                "session_id": self.session_id,
                "response": final,
                "tool_results": [
                    {
                        "tool": "fleet_distribute_task",
                        "args": {"goal": msg, "strategy": strategy},
                        "result": {
                            "mode": result.get("mode"),
                            "success": result.get("success"),
                            "workers_used": result.get("workers_used") or len(result.get("results") or []),
                            "subtasks": result.get("subtasks"),
                        },
                    }
                ],
                "tool_calls": ["fleet_distribute_task"],
                "steps": 1,
                "max_steps": self.max_steps,
                "fleet": result,
                "mode": self.mode.value,
                "tools_available": len(self.tools),
            }
        except Exception as e:
            print(f"[Fleet] dispatch skipped: {e}")
            return None

    def new_session(self):
        """Start fresh conversation - /new"""
        self.session_id = (
            f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"
        )
        self.trajectory = []
        print(f"[Hermus] New session {self.session_id}")
        return self.session_id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hermus Agent Free - Self-improving AI")
    parser.add_argument(
        "--model",
        default=config.model,
        help="Model: ollama/llama3.1:8b, groq/..., hf/..., mock/mock",
    )
    args = parser.parse_args()

    agent = HermusAgent(model=args.model)
    print(f"Hermus Free ready. Model {args.model}. Type /new, /skills, /model, /tools, /exit")
    while True:
        try:
            user_input = input("\nYou> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("/exit", "exit", "quit"):
                break
            if user_input.lower().startswith("/new"):
                agent.new_session()
                print("New session started")
                continue
            if user_input.lower().startswith("/skills"):
                skills = skill_manager.list_skills()
                print(f"Skills ({len(skills)}):")
                for s in skills:
                    print(f" - {s['name']}: {s['description'][:100]}")
                continue
            if user_input.lower().startswith("/tools"):
                info = tool_registry.list_tools()
                print(f"Tools: {info['count']}")
                print(", ".join(info["tools"][:40]), "...")
                continue
            if user_input.lower().startswith("/model"):
                parts = user_input.split()
                if len(parts) > 1:
                    agent = HermusAgent(model=parts[1])
                    print(f"Switched to {parts[1]}")
                else:
                    print(f"Current model: {agent.model_name}")
                continue

            result = agent.chat(user_input)
            print(f"\nHermus> {result['response']}")
            if result["tool_results"]:
                print(
                    f"[Tools used ({result.get('steps')} steps): "
                    f"{', '.join([tr['tool'] for tr in result['tool_results']])}]"
                )
            if result["skill_created"] and result["skill_created"].get("created"):
                print(f"[New skill created: {result['skill_created']['name']}]")

        except KeyboardInterrupt:
            print("\nUse /exit to quit")
        except Exception as e:
            print(f"Error: {e}")


# --------------------------------------------------------------------- helpers
def _delta_sink(emit, step: int):
    """on_delta callback: forward model tokens as llm_delta events."""

    def _on_delta(piece: str) -> None:
        emit("llm_delta", {"step": step, "text": (piece or "")[:2000]})

    return _on_delta


def _result_failed(result: Any) -> bool:
    try:
        from .tool_registry import tool_registry

        return bool(tool_registry._looks_like_error(result))
    except Exception:
        if isinstance(result, dict):
            return result.get("success") is False or bool(result.get("error"))
        return False


def _preview(result: Any, limit: int = 400) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        text = str(result)
    return text[:limit]


def _safe_trunc_args(args: Any, limit: int = 300) -> dict[str, Any]:
    """Argument summary safe to publish to a dashboard (no long blobs)."""
    if not isinstance(args, dict):
        return {"value": str(args)[:limit]}
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str):
            out[k] = v[:160] + ("…" if len(v) > 160 else "")
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)[:120]
    return out
