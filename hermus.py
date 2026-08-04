#!/usr/bin/env python3
"""
Hermus Agent Free - CLI Entry Point
Mimics original Hermes CLI: hermes, hermes gateway setup, hermes gateway start, etc.

This free version uses `hermus` instead of `hermes` to avoid conflict, but you can alias:
alias hermes="python -m hermus"

Free stack: Ollama local, no paywall
"""

import sys
import argparse
from pathlib import Path

# Ensure imports work
sys.path.insert(0, str(Path(__file__).parent))

from core.config import config

def main():
    parser = argparse.ArgumentParser(
        description="Hermus Agent Free - The agent that grows with you, 100% free, no paywall",
        prog="hermus"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Default: start TUI chat
    parser.add_argument("--model", default=config.model, help="Model: ollama/llama3.1:8b (free offline), groq/... (free tier), hf/... (free), mock/mock")

    # gateway subcommand
    gateway_parser = subparsers.add_parser("gateway", help="Gateway - single process for Telegram/Discord/CLI")
    gateway_sub = gateway_parser.add_subparsers(dest="gateway_action")
    gateway_setup = gateway_sub.add_parser("setup", help="Setup gateway for platform")
    gateway_setup.add_argument("--platform", default="telegram", help="telegram, discord, slack, etc.")
    gateway_start = gateway_sub.add_parser("start", help="Start gateway")
    gateway_start.add_argument("--port", type=int, default=config.gateway_port)

    # cron subcommand
    cron_parser = subparsers.add_parser("cron", help="Cron scheduler - natural language")
    cron_sub = cron_parser.add_subparsers(dest="cron_action")
    cron_add = cron_sub.add_parser("add", help="Add cron job from natural language")
    cron_add.add_argument("text", help="Natural language schedule: 'daily at 9am send report'")
    cron_add.add_argument("--task", help="Task to execute (defaults to text)")
    cron_add.add_argument("--platform", default="cli")
    cron_add.add_argument("--user-id", default="default")
    cron_list = cron_sub.add_parser("list", help="List cron jobs")
    cron_remove = cron_sub.add_parser("remove", help="Remove cron job")
    cron_remove.add_argument("job_id")

    # subagent subcommand
    subagent_parser = subparsers.add_parser("subagent", help="Subagents - parallel work")
    subagent_parser.add_argument("action", choices=["spawn"], help="spawn")
    subagent_parser.add_argument("task", help="Task for subagent")

    # skill subcommand
    skill_parser = subparsers.add_parser("skill", help="Skills")
    skill_parser.add_argument("action", choices=["list", "improve"], help="list or improve")
    skill_parser.add_argument("--name", help="Skill name for improve")

    args = parser.parse_args()

    if args.command == "gateway":
        if args.gateway_action == "setup":
            from gateway.gateway import setup
            setup(args.platform)
        elif args.gateway_action == "start":
            from gateway.gateway import start
            start(args.port)
        else:
            parser.parse_args(["gateway", "--help"])

    elif args.command == "cron":
        from scheduler.cron import cron_manager
        if args.cron_action == "add":
            job = cron_manager.add_job(args.text, task=args.task, platform=args.platform, user_id=args.user_id)
            print(f"Cron job added: {job['id']} - {job['cron']} - {job['task']}")
        elif args.cron_action == "list":
            jobs = cron_manager.list_jobs()
            print(f"Cron jobs ({len(jobs)}):")
            for j in jobs:
                print(f" - {j['id']}: {j['cron']} - {j['natural']} -> {j['platform']}:{j['user_id']}")
        elif args.cron_action == "remove":
            ok = cron_manager.remove_job(args.job_id)
            print(f"Removed {args.job_id}: {ok}")
        else:
            parser.parse_args(["cron", "--help"])

    elif args.command == "subagent":
        if args.action == "spawn":
            from subagents.subagent import spawn_subagent
            result = spawn_subagent(args.task)
            print(f"Subagent result: {result}")

    elif args.command == "skill":
        from core.skill_manager import skill_manager
        if args.action == "list":
            skills = skill_manager.list_skills()
            print(f"Skills ({len(skills)}):")
            for s in skills:
                print(f" - {s['name']}: {s['description'][:100]}")
        elif args.action == "improve":
            if not args.name:
                print("Need --name for improve")
            else:
                result = skill_manager.improve_skill(args.name)
                print(f"Improve result: {result}")

    else:
        # Default: start TUI
        print(f"Hermus Agent Free - Model {args.model}")
        print("Starting TUI (full terminal interface with slash commands)...")
        try:
            from tui.tui import HermusTUI
            tui = HermusTUI(model=args.model)
            tui.run()
        except ImportError as e:
            print(f"TUI deps missing: {e} - pip install prompt_toolkit rich - falling back to simple agent")
            from core.agent import HermusAgent
            agent = HermusAgent(model=args.model)
            print("Simple chat (type /exit to quit, /new for new session, /skills to list skills)")
            while True:
                try:
                    text = input("\nYou> ").strip()
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
                    result = agent.chat(text)
                    print(f"\nHermus> {result['response']}")
                    if result.get("skill_created", {}).get("created"):
                        print(f"[New skill: {result['skill_created']['name']}]")
                except KeyboardInterrupt:
                    print("\nUse /exit to quit")
                except Exception as e:
                    print(f"Error: {e}")

if __name__ == "__main__":
    main()
