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
        "/new", "/reset", "/model", "/personality", "/retry", "/undo",
        "/compress", "/usage", "/insights", "/skills", "/platforms",
        "/status", "/help", "/exit", "/clear", "/panel", "/agents",
        "/update", "/check-update"
    ]

    def __init__(self, model: str = None):
        self.model = model or config.model
        self.agent = HermusAgent(model=self.model)
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
        """Bottom toolbar showing active agents/models for slide panel hint - free"""
        try:
            from core.task_tracker import task_tracker
            status = task_tracker.get_status()
            return f" Agents: {status['active_agents_count']} | Tasks: {status['active_tasks_count']} | Models: {','.join(status['models_in_use'][:2]) or 'none'} | Press /panel to slide open agents view | /help "
        except:
            return " /panel to see running agents/models | /help "

    def _print_banner(self):
        banner = f"""
╔════════════════════════════════════════════════════════════╗
║  ☤ Hermus Agent Free - The agent that grows with you      ║
║  100% Free, No Paywall, MIT                               ║
║  Model: {self.model:<50} ║
║  Session: {self.agent.session_id:<48} ║
╚════════════════════════════════════════════════════════════╝

Free stack: Ollama local (no API key) + DuckDuckGo search + SQLite FTS5 memory + auto skills

Slash commands: {', '.join(self.SLASH_COMMANDS)}

Type your message, or /help for help. Ctrl+D or /exit to quit. Ctrl+C to interrupt.
Tip: Type /panel to slide open panel showing what agents/models are running
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
                self.agent = HermusAgent(model=new_model)
                print(f"Switched model to {new_model} (free: ollama/llama3.1:8b, groq/llama-3.1-70b-versatile, mock/mock)")
            else:
                print(f"Current model: {self.agent.model_name}")
                print("Free options: ollama/llama3.1:8b, ollama/mistral, groq/llama-3.1-70b-versatile (free tier), hf/mistralai/Mistral-7B-Instruct-v0.3 (free), mock/mock")
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
                else:
                    print(f"\nUpdate check result: {result}")
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

        if cmd in ("/help", "/?"):
            print("""
Hermus Agent Free - Slash Commands (same as original Hermes):
  /new or /reset - Start fresh conversation
  /model [model] - Change model: ollama/llama3.1:8b (free offline), groq/llama-3.1-70b-versatile (free tier), hf/... (free), mock/mock (test)
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
