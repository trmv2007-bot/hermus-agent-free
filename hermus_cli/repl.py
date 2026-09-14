"""Default command: interactive TUI / console fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import config

if TYPE_CHECKING:
    from ._common import CLIContext


def run_default(args, ctx: CLIContext) -> None:
    # Default: start TUI - show update check on startup
    if args.profile:
        config.profile = args.profile
    print(f"Hermus Agent Free - Model {args.model} | Mode {args.mode}" + (f" | Profile {args.profile}" if args.profile else ""))
    # Check for updates on startup and show in CLI
    try:
        from core.updater import get_updater_for_current_repo

        updater = get_updater_for_current_repo()
        result = updater.check_for_updates()
        if result.get("update_available"):
            print(f"\n🚀 Update available! {result.get('message')}")
            print("   Run 'hermus update' to update - shows in dashboard and CLI")
            print("   Dashboard http://localhost:8000/control will show banner")
    except Exception:
        pass
    print("Starting TUI (full terminal interface with slash commands)...")
    print(
        "Modes: agent can control everything, chat let's u chat, multi-agent can use multiple keys at once and reach goal no matter how difficult, multi-chat can get accurate reliable info with multiple ai models and api keys"
    )
    try:
        from tui.tui import HermusTUI

        tui = HermusTUI(model=args.model, mode=args.mode)
        tui.run()
    except ImportError as e:
        print(f"TUI deps missing: {e} - pip install prompt_toolkit rich - falling back to simple agent")
        from core.agent import HermusAgent

        agent = HermusAgent(model=args.model, mode=args.mode)
        print(
            f"Simple chat in {args.mode} mode (type /exit to quit, /new for new session, /skills to list skills, /mode to switch)"
        )
        while True:
            try:
                text = input(f"\nYou [{args.mode}]> ").strip()
                if not text:
                    continue
                if text.lower() in ("/exit", "exit", "quit"):
                    break
                if text.lower().startswith("/new"):
                    agent.new_session()
                    continue
                if text.lower().startswith("/skills"):
                    from core.skill_manager import skill_manager

                    skills = skill_manager.list_skills()
                    print(f"Skills: {skills}")
                    continue
                if text.lower().startswith("/mode"):
                    parts = text.split()
                    if len(parts) > 1:
                        new_mode = parts[1]
                        agent = HermusAgent(model=args.model, mode=new_mode)
                        print(f"Switched to {new_mode} mode: {agent.mode_config.description[:100]}")
                    else:
                        from core.modes import list_modes

                        modes = list_modes()
                        print(f"Current mode: {agent.mode.value} - {agent.mode_config.name}")
                        print("Available modes:")
                        for m, cfg in modes.items():
                            print(f" - {m}: {cfg['name']} - {cfg['description'][:80]}")
                    continue
                result = agent.chat(text)
                print(f"\nHermus [{args.mode}]> {result['response']}")
                if result.get("skill_created", {}).get("created"):
                    print(f"[New skill: {result['skill_created']['name']}]")
            except KeyboardInterrupt:
                print("\nUse /exit to quit")
            except Exception as e:
                print(f"Error: {e}")
