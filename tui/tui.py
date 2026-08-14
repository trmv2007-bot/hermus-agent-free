"""TUI - Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, streaming tool output - free"""
import os
import sys
from pathlib import Path
from datetime import datetime

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.styles import Style
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.live import Live
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from core.config import config
from core.agent import HermusAgent
from core.skill_manager import skill_manager

class HermusTUI:
    """Free TUI - Full terminal interface like original Hermes"""

    SLASH_COMMANDS = [
        "/new", "/reset", "/model", "/mode", "/personality", "/retry", "/undo",
        "/compress", "/usage", "/insights", "/skills", "/platforms",
        "/status", "/settings", "/help", "/exit", "/clear", "/panel", "/agents",
        "/update", "/check-update", "/counsel", "/think", "/plan", "/eval", "/project"
    ]

    def __init__(self, model: str = None, mode: str = "agent"):
        self.model = model or config.model
        self.mode = mode or "agent"
        self.agent = HermusAgent(model=self.model, mode=self.mode)
        self.console = Console() if RICH_AVAILABLE else None

        # History file
        history_path = Path(config.resolve_path(config.history_file))
        history_path.parent.mkdir(parents=True, exist_ok=True)

        if PROMPT_TOOLKIT_AVAILABLE:
            from prompt_toolkit.formatted_text import HTML

            def get_toolbar():
                return HTML(f'<b>{self._get_bottom_toolbar()}</b>')

            completer = WordCompleter(self.SLASH_COMMANDS + ["/"], ignore_case=True)
            self.session = PromptSession(
                history=FileHistory(str(history_path)),
                auto_suggest=AutoSuggestFromHistory(),
                completer=completer,
                multiline=True,
                prompt_continuation="... ",
                enable_history_search=True,
                bottom_toolbar=get_toolbar,
                refresh_interval=2  # Refresh toolbar every 2 sec to show live agents
            )
        else:
            self.session = None

        self._print_banner()

    def _get_bottom_toolbar(self):
        """Bottom toolbar showing active agents/models for slide panel hint + mode - free"""
        try:
            from core.task_tracker import task_tracker
            status = task_tracker.get_status()
            return f" Mode: {self.mode} | Agents: {status['active_agents_count']} | Tasks: {status['active_tasks_count']} | Models: {','.join(status['models_in_use'][:2]) or 'none'} | /mode for agent/chat/multi-agent/multi-chat | /panel slide open | /help "
        except:
            return f" Mode: {self.mode} | /mode for agent/chat/multi-agent/multi-chat | /panel slide open | /help "

    def _print_banner(self):
        banner = f"""
╔════════════════════════════════════════════════════════════╗
║  ☤ Hermus Agent Free - The agent that grows with you      ║
║  100% Free, No Paywall, MIT                               ║
║  Model: {self.model:<50} ║
║  Mode: {self.mode:<51} ║
║  Session: {self.agent.session_id:<48} ║
╚════════════════════════════════════════════════════════════╝

Free stack: Ollama local (no API key) + DuckDuckGo search + SQLite FTS5 memory + auto skills

Modes (as you requested):
  agent - Can control everything (full 88+ tools)
  chat - Let's u chat (no system tools, but your custom APIs work here)
  multi-agent - Can use multiple keys at once and reach goal no matter how difficult (parallel subagents + multi-key)
  multi-chat - Can get accurate reliable info with multiple AI models and API keys (researcher/coder/reviewer debate + custom APIs)

Slash commands: {', '.join(self.SLASH_COMMANDS)}

Type your message, or /help for help. Ctrl+D or /exit to quit. Ctrl+C to interrupt.
Tip: Type /panel to slide open panel showing what agents/models are running
Tip: Type /mode multi-agent for difficult goals, /mode multi-chat for accurate info
        """
        if self.console:
            self.console.print(banner)
        else:
            print(banner)

    def _handle_slash_command(self, text: str) -> bool:
        """Handle slash commands like original Hermes - returns True if handled"""
        text = text.strip()
        if not text.startswith("/"):
            return False

        parts = text.split()
        cmd = parts[0].lower()

        if cmd in ("/new", "/reset"):
            self.agent.new_session()
            print(f"New session {self.agent.session_id}")
            return True

        if cmd == "/model":
            if len(parts) > 1:
                new_model = parts[1]
                self.model = new_model
                self.agent = HermusAgent(model=new_model, mode=self.mode)
                print(f"Switched model to {new_model} (free: ollama/llama3.1:8b, groq/llama-3.1-70b-versatile, mock/mock)")
                print(f"Current mode: {self.mode} - {self.agent.mode_config.name}: {self.agent.mode_config.description[:100]}")
            else:
                print(f"Current model: {self.agent.model_name} | Mode: {self.mode}")
                print("Free options: ollama/llama3.1:8b, ollama/mistral, groq/llama-3.1-70b-versatile (free tier), hf/mistralai/Mistral-7B-Instruct-v0.3 (free), mock/mock")
                print("Custom URL + key: /model custom/<your-model>  (after: hermus multikey add --provider custom --base-url https://... --key sk-...)")
            return True

        if cmd == "/think":
            # DeepThink plan-first toggle (Phase 0)
            if len(parts) > 1 and parts[1].lower() in ("on", "off"):
                config.think_enabled = parts[1].lower() == "on"
            else:
                config.think_enabled = not config.think_enabled
            print(f"DeepThink plan-first: {'ON' if config.think_enabled else 'OFF'} (counsel: "
                  f"{'ON' if config.counsel_enabled else 'OFF'}, min difficulty {config.counsel_min_difficulty})")
            return True

        if cmd == "/plan":
            # DeepThink plan persistence (Phase 4, P1)
            from core.reasoning.scaffold import list_plans, show_plan
            plans = list_plans(limit=5)
            if not plans:
                print("No saved plans yet. Multi-step tasks auto-draft plans (data/plans/).")
            else:
                for p in plans:
                    print(f"  {p['session_id'][:30]} | steps={p['steps']} done={p['done']} status={p['status']} | {p['goal']}")
                print("Resume from CLI: hermus plan resume <session_id>")
            return True

        if cmd == "/eval":
            # Eval harness summary (Phase 4)
            try:
                from core.reasoning.eval import eval_harness
                s = eval_harness.summary()
                if s.get("runs"):
                    print(f"Eval history: {s['runs']} runs | last: {s.get('last_strategy')} "
                          f"rate={s.get('last_success_rate')} runs={s.get('last_runs')}")
                    for r in s.get("recent", [])[-3:]:
                        print(f"  {r['timestamp'][:16]} {r['strategy']} rate={r['success_rate']} runs={r['runs']}")
                else:
                    print("No eval runs yet — `hermus eval run --strategy reflexion`")
            except Exception as e:
                print(f"Eval status error: {e}")
            return True

        if cmd == "/project":
            # Project-scoped memory (Phase 4, P4)
            if len(parts) > 2 and parts[1] == "set":
                config.project = " ".join(parts[2:])
                print(f"Project set to: {config.project} (memory_search(project=...) now scopes to it)")
            else:
                print(f"Current project: {config.project} | set: /project set <name>")
            return True

        if cmd == "/counsel":
            # Counsel System status / quick run (Phases 0-2)
            if len(parts) > 1 and parts[1] == "run" and len(parts) > 2:
                from core.counsel.council import CouncilSession
                goal = " ".join(parts[2:])
                print(f"⚖️ Convening council for: {goal[:120]}... (this streams live)")
                result = CouncilSession(goal, model=self.model, execute=True).run()
                print(f"\n✅ Council final answer:\n{result['final_answer']}")
                return True
            try:
                from core.counsel.meta import meta_counsel
                st = meta_counsel.status()
                print(f"⚖️ COUNSEL — constitution v{st['constitution']['version']} | "
                      f"members: {', '.join(st['constitution']['members'])} | "
                      f"pending amendments: {st['pending_amendments']} | reviews: {st['reviews_logged']}")
                rules = st.get("constitution", {}).get("rules", {})
                if rules:
                    print("   Rules:")
                    for k, v in rules.items():
                        print(f"     - {k}: {str(v)[:120]}")
                print("   Hard tasks (difficulty >= {} ) auto-convene the council. "
                      "Try: /counsel run <your task>".format(config.counsel_min_difficulty))
            except Exception as e:
                print(f"Counsel status error: {e}")
            return True

        if cmd == "/mode":
            if len(parts) > 1:
                new_mode = parts[1].lower()
                try:
                    from core.modes import AgentMode, list_modes
                    from core.skin_engine import skin_engine
                    # Validate mode
                    valid_modes = [m.value for m in AgentMode]
                    if new_mode not in valid_modes:
                        print(f"Invalid mode {new_mode}. Valid: {', '.join(valid_modes)}")
                        return True
                    self.mode = new_mode
                    self.agent = HermusAgent(model=self.model, mode=new_mode)
                    # Persist mode as requested
                    skin_engine.set_mode(new_mode)
                    print(f"Switched to {new_mode} mode: {self.agent.mode_config.name}")
                    print(f"Description: {self.agent.mode_config.description}")
                    print(f"Tools allowed: {self.agent.mode_config.tools_allowed[:3]}... max {self.agent.mode_config.max_tool_calls_per_turn} tool calls per turn")
                    print(f"Multi-key: {self.agent.mode_config.use_multi_key}, Multi-AI: {self.agent.mode_config.use_multi_ai}")
                    print(f"Mode persisted to user_model.json - will remember on next startup")
                except Exception as e:
                    print(f"Mode switch failed: {e}")
            else:
                print(f"Current mode: {self.mode} - {self.agent.mode_config.name}")
                print(f"Description: {self.agent.mode_config.description}")
                from core.modes import list_modes
                modes = list_modes()
                print("\nAvailable modes (as you requested):")
                for m, cfg in modes.items():
                    print(f" - {m}: {cfg['name']} - {cfg['description'][:100]}")
                print("\nUsage: /mode agent | /mode chat | /mode multi-agent | /mode multi-chat")
                print("Agent mode can control everything")
                print("Chat mode let's u chat")
                print("Multi agent mode can use multiple keys at once and reach the goal given to u no matter how difficult it is")
                print("Multi chat mode can get u as accurate and reliable information as possible with working of multiple ai models and api keys")
                # Show persisted mode
                try:
                    from core.skin_engine import skin_engine
                    persisted = skin_engine.get_persisted_mode()
                    print(f"\nPersisted mode: {persisted} (from user_model.json) - will load on next startup")
                except:
                    pass
            return True

        if cmd == "/skills":
            skills = skill_manager.list_skills()
            print(f"Skills ({len(skills)}):")
            for s in skills:
                print(f"  - {s['name']}: {s['description'][:80]}")
            return True

        if cmd.startswith("/skills/") or (len(parts) == 1 and cmd[1:] in [s["name"] for s in skill_manager.list_skills()]):
            # /<skill-name> - use skill directly
            skill_name = cmd.lstrip("/").replace("skills/", "")
            if not skill_name and len(parts) > 1:
                skill_name = parts[1]
            from core.skill_manager import skill_manager as sm
            skill = sm.get_skill(skill_name)
            if skill:
                print(f"Skill {skill_name}: {skill.get('doc','')[:500]}")
            else:
                print(f"Skill {skill_name} not found. Use /skills to list")
            return True

        if cmd == "/platforms":
            print("Platforms (free): Telegram (Bot API free), Discord (free), CLI, Slack webhook free")
            print("Setup: hermus gateway setup --platform telegram")
            print("Start: hermus gateway start")
            try:
                from core.task_tracker import task_tracker
                print("\n" + task_tracker.get_for_tui())
            except Exception as e:
                print(f"Task tracker error: {e}")
            return True

        if cmd in ("/panel", "/agents"):
            print("\n" + "="*70)
            print("🔍 SLIDE PANEL - What agents/models are running or doing the task")
            print("="*70)
            try:
                from core.task_tracker import task_tracker
                status_text = task_tracker.get_for_tui()
                if self.console:
                    from rich.panel import Panel
                    self.console.print(Panel(status_text, title="Agents Panel - Slide Open", border_style="cyan"))
                else:
                    print(status_text)
                print("\n" + "="*70)
                print("Panel auto-refreshes every 2 sec in bottom toolbar")
                print("Gateway dashboard with slide panel: http://localhost:8000/dashboard -> Click 'Agents Panel' button")
                print("API: GET /agents/status for JSON")
            except Exception as e:
                print(f"Panel error: {e} - No active agents, idle")
            return True

        if cmd in ("/update", "/check-update"):
            # Check for GitHub updates and show in CLI
            print("\n🔄 Checking for updates from GitHub...")
            try:
                from core.updater import get_updater_for_current_repo
                updater = get_updater_for_current_repo()
                result = updater.check_for_updates()
                if result.get("update_available"):
                    print(f"\n🚀 Update available! {result.get('message')}")
                    print(f"Local: {result.get('local',{}).get('short')} - {result.get('local',{}).get('message','')[:60]}")
                    print(f"Remote: {result.get('remote',{}).get('short')} - {result.get('remote',{}).get('message','')[:60]} by {result.get('remote',{}).get('author','')} on {result.get('remote',{}).get('date','')[:10]}")
                    print(f"Behind by: {result.get('behind_by',1)} commit(s)")
                    print(f"Remote URL: {result.get('remote_url','')}")
                    print("\nTo update: Run 'hermus update' or 'git pull' or dashboard Update button")
                    # Also check if in dashboard, it will show banner
                elif result.get("up_to_date"):
                    print(f"\n✅ Up to date! {result.get('message')}")
                    print(f"Local: {result.get('local',{}).get('short')} == Remote: {result.get('remote',{}).get('short')}")
                elif result.get("error"):
                    print(f"\n⚠️ Update check: {result.get('error')}")
                else:
                    print(f"\nUpdate check: {result.get('message', 'see status')}")
            except Exception as e:
                print(f"Update check error: {e}")
                print("Try: git fetch origin main && git log --oneline HEAD..origin/main")
            return True

        if cmd in ("/compress", "/usage", "/insights"):
            # Compress context / check usage + token counting free
            from core.memory import memory
            curated = memory.get_curated_memory(limit=5)
            print(f"Curated memory ({len(curated)} items):")
            for m in curated:
                print(f"  - {m['key']}: {m['value'][:60]}")
            print(f"Trajectory length: {len(self.agent.trajectory)} turns")
            print(f"Session: {self.agent.session_id}")

            # Token usage free tracking
            try:
                usage_data = memory.get_token_usage(session_id=self.agent.session_id, limit=20)
                totals = usage_data.get("totals", {})
                recent = usage_data.get("recent", [])
                print(f"\n--- Token Usage (Free Tracking) ---")
                print(f"Session {self.agent.session_id}:")
                print(f"  Prompt tokens: {totals.get('prompt_tokens',0)}")
                print(f"  Completion tokens: {totals.get('completion_tokens',0)}")
                print(f"  Total tokens: {totals.get('total_tokens',0)}")
                print(f"  Total cost: ${totals.get('total_cost',0.0):.6f} (0.0 = free Ollama/mock/HF free)")
                print(f"  Recent calls: {len(recent)}")
                for r in recent[:5]:
                    print(f"    - {r.get('timestamp','')[:19]} {r.get('model','')} : {r.get('prompt_tokens',0)}+{r.get('completion_tokens',0)}={r.get('total_tokens',0)} tokens cost=${r.get('cost',0):.6f} free={r.get('is_free')}")

                # Global totals
                global_usage = memory.get_token_usage(limit=100)
                g_totals = global_usage.get("totals", {})
                print(f"\nGlobal totals (all sessions):")
                print(f"  Prompt: {g_totals.get('prompt_tokens',0)}, Completion: {g_totals.get('completion_tokens',0)}, Total: {g_totals.get('total_tokens',0)}, Cost: ${g_totals.get('total_cost',0.0):.6f}")

                if cmd == "/compress":
                    # Compress context - show trajectory tokens
                    from core.token_counter import token_counter
                    traj_text = str(self.agent.trajectory)
                    traj_tokens = token_counter.count_text(traj_text)
                    print(f"\nTrajectory tokens: {traj_tokens} - Use /new to reset if too large for context window")
            except Exception as e:
                print(f"Token usage tracking error: {e}")

            return True

        if cmd == "/settings":
            print("\n⚙️ SETTINGS (human-readable)")
            print(f"  Model            : {self.agent.model_name}")
            print(f"  Mode             : {self.mode} ({self.agent.mode_config.name})")
            print(f"  Session          : {self.agent.session_id}")
            print(f"  Tools            : {len(self.agent.tools)} available for this mode")
            print(f"  DeepThink        : {'ON' if config.think_enabled else 'OFF'} (strategy={config.think_strategy})")
            print(f"  Counsel          : {'ON' if config.counsel_enabled else 'OFF'} (min difficulty {config.counsel_min_difficulty})")
            print(f"  Project memory   : {config.project}")
            print(f"  Max tool steps   : {self.agent.max_steps}")

            print("\n  API keys / custom URLs:")
            try:
                from core.multi_key import multi_key_manager
                keys = multi_key_manager.list_keys(redact=True)
                found = False
                for provider, entries in keys.items():
                    for k in entries or []:
                        found = True
                        healthy = "✅" if k.get("healthy") else ("❌" if k.get("healthy") is False else "❓")
                        base = f" @ {k.get('base_url')}" if k.get("base_url") else ""
                        print(f"    {healthy} {provider}/{k.get('name','')} | model={k.get('default_model','—')}{base}")
                if not found:
                    print("    (none added — hermus multikey add --provider custom --base-url https://... --key sk-...)")
            except Exception as e:
                print(f"    error listing keys: {e}")

            print("\n  Custom APIs (usable as tools in every mode):")
            try:
                from core.custom_api import custom_api_manager
                apis = custom_api_manager.list_apis()
                if not apis:
                    print("    (none — hermus api add ...)")
                for a in apis:
                    token = a.get("auth", {}).get("token") or a.get("auth", {}).get("value") or ""
                    tok = f"{token[:6]}...{token[-4:]}" if len(token) > 10 else ("no-token" if not token else "****")
                    print(f"    - {a.get('name')} [{a.get('method','GET')}] {a.get('url','')} token={tok}")
            except Exception as e:
                print(f"    error listing custom APIs: {e}")

            print("\n  Hint: change model with /model custom/<your-model>, mode with /mode <name>")
            return True

        if cmd in ("/help", "/?"):
            print("""
Hermus Agent Free - Slash Commands (same as original Hermes):
  /new or /reset - Start fresh conversation
  /model [model] - Change model: ollama/llama3.1:8b (free offline), groq/llama-3.1-70b-versatile (free tier), hf/... (free), custom/<model> (your URL+key), mock/mock (test)
  /mode [name] - agent | chat | multi-agent | multi-chat
  /settings - Human-readable settings: model, mode, API keys, custom URLs, custom APIs
  /skills - Browse skills
  /<skill-name> - Use skill directly e.g. /file_watcher_emailer
  /platforms - Platform-specific status
  /compress, /usage, /insights - Compress context / check usage
  /retry, /undo - Retry or undo last turn (not yet implemented free)
  /clear - Clear screen
  /exit - Exit

Free stack: No API keys needed for ollama/ + DuckDuckGo search + SQLite FTS5
            """)
            return True

        if cmd == "/clear":
            os.system("clear" if os.name != "nt" else "cls")
            return True

        if cmd in ("/exit", "/quit"):
            print("Exiting Hermus Free")
            sys.exit(0)

        print(f"Unknown command {cmd}. Type /help")
        return True

    def run(self):
        """Main TUI loop - streaming tool output, interrupt-and-redirect"""
        if not PROMPT_TOOLKIT_AVAILABLE:
            print("prompt_toolkit not available - using simple input (pip install prompt_toolkit for full TUI)")
            print("Full TUI features: multiline editing, autocomplete, history search")

        while True:
            try:
                if self.session:
                    text = self.session.prompt("\nYou> ", style=Style.from_dict({'': '#ansicyan'}))
                else:
                    text = input("\nYou> ")

                if not text.strip():
                    continue

                # Handle slash commands
                if self._handle_slash_command(text):
                    continue

                # Streaming tool output simulation
                print("\nHermus> ", end="", flush=True)

                # For free version, simple non-streaming but could stream via llm.chat_stream()
                try:
                    # Try streaming via LLM
                    from core.llm import free_llm
                    # Build messages similar to agent
                    messages = [
                        {"role": "system", "content": self.agent._build_system_prompt()},
                        {"role": "user", "content": text}
                    ]
                    # Stream
                    full_response = ""
                    for chunk in free_llm.chat_stream(messages, tools=self.agent.tools):
                        print(chunk, end="", flush=True)
                        full_response += chunk

                    # Actually call agent.chat for full logic with tools (for now use non-streaming result after)
                    # For TUI, we already streamed via chat_stream, but need real agent logic
                    result = self.agent.chat(text)
                    # If result has tools, print tools
                    if result.get("tool_results"):
                        print(f"\n[Tools: {', '.join([tr['tool'] for tr in result['tool_results']])}]")

                    # If skill created
                    if result.get("skill_created") and result["skill_created"].get("created"):
                        print(f"\n[New skill auto-created: {result['skill_created']['name']} - self-improving]")

                    # If we streamed earlier, we already printed, but ensure final response printed if different
                    if full_response.strip() != result["response"][:len(full_response)].strip():
                        print(f"\n{result['response']}")

                except KeyboardInterrupt:
                    print("\n[Interrupted - Ctrl+C] Use /exit to quit or continue chatting")
                    continue

            except KeyboardInterrupt:
                print("\n[Interrupt] Type /exit to quit")
            except EOFError:
                print("\nExiting")
                break
            except Exception as e:
                print(f"\nError: {e}")
                import traceback
                traceback.print_exc()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hermus TUI Free")
    parser.add_argument("--model", default=config.model)
    args = parser.parse_args()
    tui = HermusTUI(model=args.model)
    tui.run()
