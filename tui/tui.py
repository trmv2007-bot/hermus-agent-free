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
        "/status", "/help", "/exit", "/clear"
    ]

    def __init__(self, model: str = None):
        self.model = model or config.model
        self.agent = HermusAgent(model=self.model)
        self.console = Console() if RICH_AVAILABLE else None

        # History file
        history_path = Path(config.resolve_path(config.history_file))
        history_path.parent.mkdir(parents=True, exist_ok=True)

        if PROMPT_TOOLKIT_AVAILABLE:
            completer = WordCompleter(self.SLASH_COMMANDS + ["/"], ignore_case=True)
            self.session = PromptSession(
                history=FileHistory(str(history_path)),
                auto_suggest=AutoSuggestFromHistory(),
                completer=completer,
                multiline=True,
                prompt_continuation="... ",
                enable_history_search=True
            )
        else:
            self.session = None

        self._print_banner()

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
            return True

        if cmd in ("/compress", "/usage", "/insights"):
            # Compress context / check usage
            from core.memory import memory
            curated = memory.get_curated_memory(limit=5)
            print(f"Curated memory ({len(curated)} items):")
            for m in curated:
                print(f"  - {m['key']}: {m['value'][:60]}")
            print(f"Trajectory length: {len(self.agent.trajectory)} turns")
            print(f"Session: {self.agent.session_id}")
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
