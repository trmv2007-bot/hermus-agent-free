"""Main Agent Loop - Free Hermes Clone
Multi-step ReAct tool loop + auto tool registry + semantic memory hooks.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .config import config
from .llm import free_llm, FreeLLM
from .memory import memory
from .skill_manager import skill_manager
from .task_tracker import task_tracker
from .modes import AgentMode, get_mode_config, list_modes
from .tool_registry import tool_registry


class HermusAgent:
    """Free Hermes-like agent - self-improving with memory, skills, multi-step tools."""

    def __init__(
        self,
        model: str = None,
        session_id: str = None,
        mode: str = None,
        max_steps: int = None,
    ):
        self.model_name = model or config.model
        self.llm = FreeLLM(self.model_name)
        self.session_id = session_id or (
            f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"
        )
        self.trajectory: List[Dict] = []
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
        except Exception:
            pass

    def _get_tools(self) -> List[Dict]:
        """Definitions from auto-discovered registry, filtered by mode."""
        allowed = self.mode_config.tools_allowed
        if "all" in allowed:
            return tool_registry.get_definitions(allowed={"all"})
        if "none" in allowed:
            return []
        return tool_registry.get_definitions(allowed=set(allowed))

    def reload_tools(self):
        """Force reload registry (e.g. after MCP connect)."""
        tool_registry.load(force=True)
        self.tools = self._get_tools()
        return {"tools": len(self.tools)}

    def _execute_tool(self, name: str, args: Dict) -> Dict:
        """Execute via central registry — all tools including full pentest map."""
        return tool_registry.execute(name, args or {})

    def _build_system_prompt(self, user_message: str = "") -> str:
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
        except Exception:
            pass

        return f"""You are Hermus Agent Free - a self-improving AI agent that grows with the user.

You have:
- Multi-step tool use (ReAct): you may call tools across multiple rounds until the task is done (max {self.max_steps} steps)
- Persistent memory (SQLite FTS5) + semantic/hybrid search (embeddings)
- Auto-created skills (skill_list / skill_use with task context)
- MCP tools when configured (mcp_list_servers / mcp_connect_all)
- {tool_count} tools registered (browser, vision, voice, internet eyes, pentest, backends, etc.)

Curated Memory:
{curated_text}

User Model:
{user_model_text}

Available Skills:
{skills_text}

Periodic Nudges:
{nudges_text}
{lessons_block}
Rules:
- Use tools when needed; do not hallucinate facts you can look up
- After tools return, continue reasoning; call more tools if needed
- When finished, respond with a clear final answer and NO further tool calls
- Prefer skill_use for known workflows; memory_search/hybrid for past context
- Prefer embeddings_ingest + embeddings_search for document Q&A
- You are free, MIT, no paywall — Ollama / Groq / HF
- Session: {self.session_id}
- Model: {self.model_name}
- Mode: {self.mode.value}
"""

    def _format_tool_result(self, name: str, result: Any, limit: int = 3000) -> str:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            text = str(result)
        if len(text) > limit:
            text = text[:limit] + "...(truncated)"
        return f"Tool {name} returned:\n{text}"

    def chat(self, user_message: str) -> Dict[str, Any]:
        """Multi-step agent loop: plan → tool calls → observe → repeat → final answer."""
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
        except Exception:
            pass

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
        except Exception:
            pass

        system_prompt = self._build_system_prompt(user_message)

        # DeepThink plan-first (Phase 0): write an explicit plan for multi-step tasks
        plan = None
        budget_steps = self.max_steps
        try:
            from .reasoning.governor import governor
            from .reasoning.scaffold import plan_builder

            budget_steps = governor.step_budget(user_message, mode=self.mode.value)
            if governor.should_plan_first(user_message, mode=self.mode.value):
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

        messages: List[Dict] = [
            {
                "role": "system",
                "content": system_prompt
                + (f"\n\nRelevant memory:\n{memory_summary}" if memory_summary else ""),
            },
        ]
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

        all_tool_results: List[Dict] = []
        steps = 0
        final_content = ""
        last_usage = {}

        # ---- Multi-step ReAct loop ----
        while steps < budget_steps:
            steps += 1
            try:
                task_tracker.update_agent(
                    self.agent_tracker_id,
                    status="running",
                    progress=f"Step {steps}/{budget_steps}",
                )
            except Exception:
                pass

            # Only pass tools while we still have budget for another tool round
            use_tools = self.tools if self.tools else None
            response = self.llm.chat(messages, tools=use_tools)
            last_usage = getattr(response, "usage", None) or last_usage

            if not response.tool_calls:
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
                result = self._execute_tool(tool_name, tool_args)
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

        # DeepThink deliberation strategy (Phase 3): reflexion / verify / self-consistency
        strategy = "none"
        strategy_meta: Dict = {}
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

        # Persist assistant reply
        memory.add_session_message(self.session_id, "assistant", final_content)
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
        except Exception:
            pass

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

        # Auto skill creation
        skill_created = None
        if skill_manager.should_create_skill(self.trajectory):
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
        except Exception:
            pass

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
        }

    def _maybe_fleet_distribute(self, user_message: str) -> Optional[Dict[str, Any]]:
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

            strategy = "fanout" if self.mode == AgentMode.MULTI_CHAT else "auto"
            if "race" in lower:
                strategy = "race"
            elif "map" in lower or "subtask" in lower or self.mode == AgentMode.MULTI_AGENT:
                strategy = "map" if self.mode == AgentMode.MULTI_AGENT else strategy

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
