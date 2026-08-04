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

    # api subcommand - custom API feature free
    api_parser = subparsers.add_parser("api", help="Custom API - add any API as tool (free)")
    api_sub = api_parser.add_subparsers(dest="api_action")
    api_add = api_sub.add_parser("add", help="Add custom API")
    api_add.add_argument("--name", required=True, help="API tool name, e.g., weather_api")
    api_add.add_argument("--description", required=True, help="Description for LLM")
    api_add.add_argument("--url", required=True, help="URL with optional {param} placeholders, e.g., https://api.example.com/weather/{city}")
    api_add.add_argument("--method", default="GET", choices=["GET", "POST", "PUT", "DELETE"], help="HTTP method")
    api_add.add_argument("--header", action="append", help="Headers as Key:Value, can repeat")
    api_add.add_argument("--param", action="append", help="Parameters as name:description, e.g., city:City name, can repeat")
    api_add.add_argument("--auth-type", choices=["none", "bearer", "apikey", "basic"], default="none")
    api_add.add_argument("--auth-token", help="Bearer token or API key value")
    api_add.add_argument("--auth-key", help="API key header name (for apikey type) or username (for basic)")
    api_add.add_argument("--auth-password", help="Password for basic auth")

    api_list = api_sub.add_parser("list", help="List custom APIs")
    api_remove = api_sub.add_parser("remove", help="Remove custom API")
    api_remove.add_argument("name", help="API name or id")
    api_test = api_sub.add_parser("test", help="Test custom API")
    api_test.add_argument("name", help="API name")
    api_test.add_argument("--args", help="JSON args for test, e.g., '{\"city\": \"London\"}'")

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

    elif args.command == "api":
        from core.custom_api import custom_api_manager
        import json
        if args.api_action == "add":
            # Parse headers
            headers = {}
            if args.header:
                for h in args.header:
                    if ":" in h:
                        k, v = h.split(":", 1)
                        headers[k.strip()] = v.strip()
            # Parse params
            params = {}
            if args.param:
                for p in args.param:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        params[k.strip()] = {"type": "string", "description": v.strip()}
                    else:
                        params[p.strip()] = {"type": "string", "description": f"Parameter {p}"}
            # Auth
            auth = {"type": args.auth_type}
            if args.auth_type == "bearer":
                auth["token"] = args.auth_token
            elif args.auth_type == "apikey":
                auth["key"] = args.auth_key or "X-API-Key"
                auth["value"] = args.auth_token
            elif args.auth_type == "basic":
                auth["username"] = args.auth_key
                auth["password"] = args.auth_password

            api_def = {
                "name": args.name,
                "description": args.description,
                "url": args.url,
                "method": args.method,
                "headers": headers,
                "parameters": params,
                "auth": auth
            }
            result = custom_api_manager.add_api(api_def)
            print(f"Add custom API result: {result}")
            if result.get("success"):
                print(f"✅ Custom API '{args.name}' added! Agent can now use it as tool.")
                print(f"   Try: hermus --model mock/mock and say 'Use {args.name} with ...'")

        elif args.api_action == "list":
            apis = custom_api_manager.list_apis()
            print(f"Custom APIs ({len(apis)}):")
            for api in apis:
                print(f" - {api['name']}: {api['description']} | {api['method']} {api['url']} | enabled={api.get('enabled',True)}")
                if api.get("parameters"):
                    print(f"    Params: {list(api['parameters'].keys())}")

        elif args.api_action == "remove":
            result = custom_api_manager.remove_api(args.name)
            print(f"Remove result: {result}")

        elif args.api_action == "test":
            import json as json_lib
            test_args = {}
            if args.args:
                try:
                    test_args = json_lib.loads(args.args)
                except:
                    print(f"Failed to parse --args JSON: {args.args}, using empty")
            result = custom_api_manager.execute_api(args.name, test_args)
            print(f"Test {args.name} with {test_args}:")
            print(json_lib.dumps(result, indent=2)[:2000])

        else:
            parser.parse_args(["api", "--help"])

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
