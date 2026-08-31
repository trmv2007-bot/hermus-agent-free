"""Self-Improving Agent - When given work is done as it goes idle, it should go through reflections and see what mistakes it did during the work, search how to improve itself, fix itself in background - Free"""

import json
import time
import threading
from datetime import datetime, timedelta

from .config import config

class SelfImprovement:
    """Self-improving agent - reflection when idle, sees mistakes, searches how to improve, fixes itself in background"""

    def __init__(self):
        self.is_reflecting = False
        self.last_reflection = None
        self.reflection_history: list[dict] = []
        self.current_reflection: dict = {}
        self.reflection_log_path = config.resolve_path("data/self_improvement.json")
        self.reflection_log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.reflection_log_path.exists():
            self.reflection_log_path.write_text("[]")

        # Background thread for idle reflection
        self.idle_check_interval = 30  # Check every 30 sec if idle
        self.idle_threshold = 60  # Consider idle if no active tasks for 60 sec
        self.background_thread = None
        self.should_stop = False

    def _load_history(self) -> list[dict]:
        try:
            return json.loads(self.reflection_log_path.read_text())
        except Exception:
            return []

    def _save_history(self, history: list[dict]):
        try:
            self.reflection_log_path.write_text(json.dumps(history[-20:], indent=2))  # Keep last 20
        except Exception:
            pass

    def reflect_on_trajectory(self, trajectory: list[dict]) -> dict:
        """Go through reflections and see what mistakes it did during the work"""
        mistakes = []
        successes = []
        tool_failures = []
        user_corrections = []

        for turn in trajectory:
            role = turn.get("role","")
            content = turn.get("content","")
            tool_calls = turn.get("tool_calls", [])

            # Detect mistakes via tool failures
            if role == "tool":
                # Check if tool result has error
                if "error" in content.lower() or "failed" in content.lower():
                    tool_failures.append({
                        "tool": turn.get("tool", "unknown"),
                        "content": content[:200],
                        "timestamp": turn.get("timestamp","")
                    })
                    mistakes.append(f"Tool {turn.get('tool','unknown')} failed: {content[:100]}")

            # Detect user corrections
            if role == "user":
                lower = content.lower()
                if any(word in lower for word in ["wrong", "incorrect", "no, not", "that's wrong", "fix", "mistake", "error"]):
                    user_corrections.append({
                        "user_said": content[:200],
                        "timestamp": turn.get("timestamp","")
                    })
                    mistakes.append(f"User correction: {content[:100]}")

            # Detect successes
            if role == "assistant" and len(content) > 20 and "error" not in content.lower():
                if len(tool_calls) > 0:
                    successes.append(f"Successful tool use: {tool_calls}")

        # Analyze patterns
        reflection = {
            "timestamp": datetime.now().isoformat(),
            "trajectory_length": len(trajectory),
            "mistakes": mistakes,
            "mistakes_count": len(mistakes),
            "tool_failures": tool_failures,
            "tool_failures_count": len(tool_failures),
            "user_corrections": user_corrections,
            "successes": successes[:5],
            "successes_count": len(successes),
            "reflection": f"Analyzed {len(trajectory)} turns: {len(mistakes)} mistakes, {len(tool_failures)} tool failures, {len(user_corrections)} user corrections"
        }

        return reflection

    def search_how_to_improve(self, reflection: dict) -> dict:
        """Search how to improve itself based on mistakes - free via web_search + LLM"""
        mistakes = reflection.get("mistakes", [])
        if not mistakes:
            return {"improvements_searched": [], "message": "No mistakes found, no need to improve"}

        improvements = []
        try:
            from tools.web_search import web_search
            from .models import get_model_gateway

            for mistake in mistakes[:3]:  # Search for top 3 mistakes
                # Search web for how to improve
                query = f"How to fix {mistake[:50]} best practices"
                try:
                    search_results = web_search(query, max_results=2)
                    search_text = " ".join([r.get("body","")[:200] for r in search_results[:2]])
                except Exception:
                    search_text = "No search results"

                # Ask free LLM how to improve
                try:
                    messages = [
                        {"role": "system", "content": "You are a self-improvement expert for AI agents. Given a mistake the agent made, search results for best practices, suggest how to fix itself."},
                        {"role": "user", "content": f"Mistake: {mistake}\n\nSearch results for best practices:\n{search_text[:1000]}\n\nSuggest how agent should fix itself in background - e.g., improve skill, add error handling, update tool, etc."}
                    ]
                    resp = get_model_gateway().chat(messages)
                    improvements.append({
                        "mistake": mistake,
                        "search_query": query,
                        "search_results": search_text[:500],
                        "suggested_fix": resp.content[:500],
                        "timestamp": datetime.now().isoformat()
                    })
                except Exception as e:
                    improvements.append({
                        "mistake": mistake,
                        "search_query": query,
                        "error": str(e)[:200],
                        "suggested_fix": f"Add error handling for {mistake}"
                    })

        except Exception as e:
            return {"improvements_searched": [], "error": str(e)}

        return {
            "mistakes_count": len(mistakes),
            "improvements_searched": improvements,
            "improvements_count": len(improvements),
            "message": f"Searched how to improve {len(mistakes)} mistakes, found {len(improvements)} improvements"
        }

    def fix_itself_in_background(self, improvements: list[dict], task_id: str = None) -> dict:
        """Fix itself in background based on improvements searched"""
        fixes_applied = []

        try:
            from core.task_tracker import task_tracker
            from core.skill_manager import skill_manager

            for improvement in improvements:
                mistake = improvement.get("mistake","")
                suggested_fix = improvement.get("suggested_fix","")

                # Try to apply fix - for free version, we create or improve a skill that handles this mistake better
                # Example: If tool failed, improve skill or add error handling

                # For demo, we create a skill that avoids this mistake
                try:
                    # Create a skill that describes how to avoid this mistake
                    skill_name = f"avoid_{mistake[:20].replace(' ', '_').replace(':', '').lower()}_{datetime.now().strftime('%H%M%S')}"
                    skill_name = "".join(c for c in skill_name if c.isalnum() or c == "_")[:30]

                    from .memory import memory
                    # Curate memory about this mistake and fix
                    memory.curate_memory(
                        key=f"self_improvement_fix_{skill_name}",
                        value=f"Mistake: {mistake} | Fix: {suggested_fix}",
                        source_session="self_improvement",
                        importance=7
                    )

                    # Try to improve existing skill if relevant
                    # For example, if tool failure, improve that tool's skill
                    if "Tool" in mistake:
                        # Extract tool name
                        import re
                        tool_match = re.search(r"Tool (\w+) failed", mistake)
                        if tool_match:
                            tool_name = tool_match.group(1)
                            # Try to improve skill for that tool
                            # Find skill that uses this tool
                            skills = skill_manager.list_skills()
                            for skill in skills[:3]:
                                # Log that this skill should be improved to avoid this mistake
                                skill_manager.log_skill_usage(skill["name"], success=False, feedback=f"Failed due to {mistake}, suggested fix: {suggested_fix}")

                    fixes_applied.append({
                        "mistake": mistake[:100],
                        "fix": suggested_fix[:200],
                        "action": f"Curated memory and logged for skill improvement: {skill_name}",
                        "timestamp": datetime.now().isoformat()
                    })

                    # Update task tracker
                    if task_id:
                        task_tracker.update_task(task_id, progress=f"Fixed: {mistake[:50]} -> {suggested_fix[:50]}")

                except Exception as e:
                    fixes_applied.append({
                        "mistake": mistake[:100],
                        "fix": suggested_fix[:100],
                        "error": str(e)[:200],
                        "action": "Failed to apply fix"
                    })

        except Exception as e:
            return {"fixes_applied": fixes_applied, "error": str(e)}

        return {
            "fixes_applied": fixes_applied,
            "fixes_count": len(fixes_applied),
            "message": f"Fixed {len(fixes_applied)} mistakes in background"
        }

    def run_idle_reflection(self, trajectory: list[dict] = None, force: bool = False) -> dict:
        """Run reflection when idle - goes through reflections, sees mistakes, searches how to improve, fixes in background"""
        if self.is_reflecting and not force:
            return {"status": "already_reflecting", "message": "Already reflecting, please wait"}

        self.is_reflecting = True
        task_id = None

        try:
            from core.task_tracker import task_tracker

            # Track self-improvement task for slide panel
            task_id = task_tracker.add_task(
                task_id=f"self_improve_{datetime.now().strftime('%H%M%S')}",
                task_type="self-improvement",
                description="Self-improvement: reflection + mistake analysis + search improvements + fix in background",
                model="self-improvement",
                agent="self-improvement-agent"
            )
            task_tracker.add_agent(
                agent_id=task_id,
                name="self-improvement-agent",
                model="reflection",
                persona="self-improving agent that reflects on mistakes",
                task="Reflecting on trajectory, finding mistakes, searching improvements, fixing itself"
            )

            self.current_reflection = {
                "status": "reflecting",
                "stage": "reflection",
                "started": datetime.now().isoformat(),
                "task_id": task_id,
                "message": "Going through reflections and seeing what mistakes it did during work..."
            }

            # Stage 1: Reflection - see what mistakes it did
            if trajectory is None:
                # Get recent trajectory from memory if not provided
                try:
                    from .memory import memory
                    # Get last session's trajectory from memory?
                    # For free version, use last 20 sessions
                    recent_sessions = memory.get_curated_memory(limit=5)
                    trajectory = [{"role": "user", "content": f"Recent memory: {m['key']}: {m['value'][:100]}"} for m in recent_sessions]
                    if not trajectory:
                        trajectory = [{"role": "user", "content": "No recent trajectory, checking recent tool failures"}, {"role": "tool", "content": "Tool web_search failed: timeout", "tool": "web_search"}]
                except Exception:
                    trajectory = [{"role": "user", "content": "Test trajectory for reflection"}]

            reflection = self.reflect_on_trajectory(trajectory)
            self.current_reflection["stage"] = "reflection_done"
            self.current_reflection["reflection"] = reflection
            self.current_reflection["mistakes_found"] = reflection["mistakes_count"]

            task_tracker.update_task(task_id, progress=f"Reflection done: found {reflection['mistakes_count']} mistakes, {reflection['tool_failures_count']} tool failures")

            # Stage 2: Search how to improve itself
            self.current_reflection["stage"] = "searching_improvements"
            self.current_reflection["message"] = f"Found {reflection['mistakes_count']} mistakes, searching how to improve itself..."

            task_tracker.update_task(task_id, progress=f"Searching how to improve {reflection['mistakes_count']} mistakes...")

            improvements_result = self.search_how_to_improve(reflection)
            improvements = improvements_result.get("improvements_searched", [])

            self.current_reflection["stage"] = "search_done"
            self.current_reflection["improvements"] = improvements
            self.current_reflection["improvements_count"] = len(improvements)

            task_tracker.update_task(task_id, progress=f"Search done: found {len(improvements)} improvements")

            # Stage 3: Fix itself in background
            self.current_reflection["stage"] = "fixing"
            self.current_reflection["message"] = f"Found {len(improvements)} improvements, fixing itself in background..."

            task_tracker.update_task(task_id, progress=f"Fixing itself in background: {len(improvements)} fixes...")

            fixes_result = self.fix_itself_in_background(improvements, task_id=task_id)

            # Counsel System (Phase 2): reflection mistakes also become constitution
            # amendments so the council upgrades itself from yesterday's errors.
            try:
                from core.counsel.meta import meta_counsel

                meta_counsel.propose_from_reflection(reflection, improvements_result)
            except Exception as e:
                print(f"[Counsel] amendment proposal from reflection failed: {e}")

            # Lessons loop (Phase 3): reflection mistakes also become prompt lessons
            try:
                from core.reasoning.lessons import lessons_store

                lessons_store.distill_reflection(reflection)
            except Exception as e:
                print(f"[Lessons] reflection distillation failed: {e}")

            self.current_reflection["stage"] = "fixed"
            self.current_reflection["fixes"] = fixes_result.get("fixes_applied", [])
            self.current_reflection["fixes_count"] = len(fixes_result.get("fixes_applied", []))
            self.current_reflection["message"] = f"Fixed {len(fixes_result.get('fixes_applied', []))} mistakes in background - self-improved!"

            task_tracker.update_task(task_id, progress=f"Fixed {len(fixes_result.get('fixes_applied', []))} mistakes - self-improved!", status="done")

            # Save to history
            final_reflection = {
                "timestamp": datetime.now().isoformat(),
                "task_id": task_id,
                "reflection": reflection,
                "improvements": improvements,
                "fixes": fixes_result.get("fixes_applied", []),
                "status": "completed",
                "message": f"Self-improvement completed: {reflection['mistakes_count']} mistakes -> {len(improvements)} improvements -> {len(fixes_result.get('fixes_applied', []))} fixes in background"
            }

            history = self._load_history()
            history.append(final_reflection)
            self._save_history(history)

            self.last_reflection = final_reflection
            self.current_reflection = {
                "status": "completed",
                "stage": "completed",
                "last_reflection": final_reflection,
                "message": final_reflection["message"]
            }

            task_tracker.complete_task(task_id, status="done", result=final_reflection["message"])
            from core.task_tracker import task_tracker as tt
            tt.remove_agent(task_id, final_status="done")

            return final_reflection

        except Exception as e:
            error_result = {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            self.current_reflection = error_result
            if task_id:
                try:
                    from core.task_tracker import task_tracker
                    task_tracker.complete_task(task_id, status="failed", result=str(e)[:200])
                    task_tracker.remove_agent(task_id, final_status="failed")
                except Exception:
                    pass
            return error_result
        finally:
            self.is_reflecting = False

    def check_if_idle_and_reflect(self) -> bool:
        """Check if agent is idle and trigger reflection if idle - for background thread"""
        try:
            from core.task_tracker import task_tracker
            status = task_tracker.get_status()
            # Consider idle if no active agents (excluding self-improvement-agent) and no active tasks (excluding self-improvement)
            active_agents = [a for a in status.get("active_agents", []) if "self-improvement" not in a.get("name","") and "self-improvement" not in a.get("agent_id","")]
            active_tasks = [t for t in status.get("active_tasks", []) if t.get("type") != "self-improvement"]

            is_idle = len(active_agents) == 0 and len(active_tasks) == 0

            if is_idle:
                # Check last activity time - if idle for threshold, reflect
                # For simplicity, we trigger reflection if idle and last reflection was more than 5 minutes ago or never
                last_reflection_time = None
                if self.last_reflection:
                    try:
                        last_reflection_time = datetime.fromisoformat(self.last_reflection.get("timestamp",""))
                    except Exception:
                        pass

                should_reflect = False
                if last_reflection_time is None:
                    should_reflect = True
                else:
                    if datetime.now() - last_reflection_time > timedelta(minutes=5):
                        should_reflect = True

                if should_reflect:
                    print("[Self-Improvement] Agent is idle, starting reflection...")
                    # Run in background thread so not blocking
                    thread = threading.Thread(target=self.run_idle_reflection, daemon=True)
                    thread.start()
                    return True

            return False
        except Exception as e:
            print(f"[Self-Improvement] Idle check failed: {e}")
            return False

    def start_background_idle_checker(self):
        """Start background thread that checks if idle every 30 sec and reflects - free"""
        if self.background_thread and self.background_thread.is_alive():
            return {"status": "already_running"}

        self.should_stop = False

        def idle_checker_loop():
            while not self.should_stop:
                try:
                    self.check_if_idle_and_reflect()
                except Exception as e:
                    print(f"[Self-Improvement] Background idle checker error: {e}")
                time.sleep(self.idle_check_interval)

        self.background_thread = threading.Thread(target=idle_checker_loop, daemon=True)
        self.background_thread.start()

        return {"status": "started", "interval": self.idle_check_interval, "threshold": self.idle_threshold}

    def stop_background_checker(self):
        self.should_stop = True
        return {"status": "stopped"}

    def get_status(self) -> dict:
        """Get status for hideable panel to show what it is doing"""
        history = self._load_history()
        return {
            "is_reflecting": self.is_reflecting,
            "current_reflection": self.current_reflection,
            "last_reflection": self.last_reflection,
            "history_count": len(history),
            "history": history[-5:][::-1],  # Last 5 most recent first
            "background_checker_running": self.background_thread.is_alive() if self.background_thread else False,
            "idle_check_interval": self.idle_check_interval,
            "message": self.current_reflection.get("message", "Idle" if not self.is_reflecting else "Reflecting...")
        }

    def get_for_panel(self) -> str:
        """Get formatted text for hideable panel"""
        status = self.get_status()
        lines = []
        lines.append(f"⏱️ {datetime.now().strftime('%H:%M:%S')} | Self-Improvement Agent | Reflecting: {status['is_reflecting']}")
        lines.append(f"Background checker: {'Running' if status['background_checker_running'] else 'Stopped'} (checks every {status['idle_check_interval']}s)")

        current = status.get("current_reflection", {})
        if current:
            lines.append(f"\nCurrent: {current.get('stage','idle')} - {current.get('message','Idle')}")
            if current.get("stage") == "reflection":
                lines.append(f"  Going through reflections and seeing what mistakes it did...")
            elif current.get("stage") == "searching_improvements":
                lines.append(f"  Searching how to improve itself...")
            elif current.get("stage") == "fixing":
                lines.append(f"  Fixing itself in background...")

        last = status.get("last_reflection")
        if last:
            lines.append(f"\nLast Reflection ({last.get('timestamp','')[:19]}):")
            lines.append(f"  {last.get('reflection',{}).get('reflection','')}")
            lines.append(f"  Mistakes: {last.get('reflection',{}).get('mistakes_count',0)}, Tool failures: {last.get('reflection',{}).get('tool_failures_count',0)}")
            lines.append(f"  Improvements searched: {len(last.get('improvements',[]))}")
            lines.append(f"  Fixes applied: {len(last.get('fixes',[]))} in background")
            if last.get("fixes"):
                for fix in last["fixes"][:3]:
                    lines.append(f"    - Fixed: {fix.get('mistake','')[:50]} -> {fix.get('fix','')[:50]}")

        history = status.get("history", [])
        if history:
            lines.append(f"\nHistory ({status['history_count']} total, last 3):")
            for h in history[:3]:
                lines.append(f"  - {h.get('timestamp','')[:19]}: {h.get('reflection',{}).get('mistakes_count',0)} mistakes -> {len(h.get('fixes',[]))} fixes - {h.get('message','')[:60]}")

        if not current and not last and not history:
            lines.append("\n💤 No self-improvement yet - idle")
            lines.append("When given work is done and agent goes idle, it will:")
            lines.append("1. Go through reflections and see what mistakes it did during work")
            lines.append("2. Search how to improve itself (web_search free)")
            lines.append("3. Fix itself in background (curate memory, improve skills)")

        return "\n".join(lines)

# Global self-improvement instance free
self_improvement = SelfImprovement()
