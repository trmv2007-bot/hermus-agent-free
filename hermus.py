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
    parser.add_argument("--mode", default=None, help="Mode: agent can control everything, chat let's u chat, multi-agent can use multiple keys at once and reach goal no matter how difficult, multi-chat can get accurate reliable info with multiple ai models and api keys - persisted to user_model.json")

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

    # multi-key subcommand - multiple API keys at once
    multikey_parser = subparsers.add_parser("multikey", help="Multi-API Keys - use multiple keys at once to complete quickly")
    multikey_sub = multikey_parser.add_subparsers(dest="multikey_action")
    multikey_add = multikey_sub.add_parser("add", help="Add API key for provider")
    multikey_add.add_argument("--provider", required=True, choices=["groq", "hf", "openai", "custom"], help="Provider")
    multikey_add.add_argument("--key", required=True, help="API key")
    multikey_add.add_argument("--name", help="Key name")
    multikey_list = multikey_sub.add_parser("list", help="List keys per provider")
    multikey_list.add_argument("--provider", help="Provider filter")
    multikey_remove = multikey_sub.add_parser("remove", help="Remove key")
    multikey_remove.add_argument("--provider", required=True)
    multikey_remove.add_argument("--key", required=True, help="Key or name to remove")
    multikey_parallel = multikey_sub.add_parser("parallel", help="Test parallel execution with multiple keys")
    multikey_parallel.add_argument("--provider", default="groq")
    multikey_parallel.add_argument("--tasks", nargs="+", help="Tasks to run in parallel with different keys")

    # multi-ai subcommand - multiple AIs talk to each other
    multiai_parser = subparsers.add_parser("multiai", help="Multi-AI - multiple AIs talk to each other for anything")
    multiai_sub = multiai_parser.add_subparsers(dest="multiai_action")
    multiai_debate = multiai_sub.add_parser("debate", help="Multi-AI debate on topic")
    multiai_debate.add_argument("topic", help="Topic for debate")
    multiai_debate.add_argument("--rounds", type=int, default=2, help="Rounds")
    multiai_debate.add_argument("--model", default=None, help="Model for all agents")
    multiai_debate.add_argument("--agents", nargs="+", default=None, help="Agent personas: researcher coder reviewer writer planner debater optimist pessimist")

    multiai_chat = multiai_sub.add_parser("chat", help="Multi-AI collaborative chat")
    multiai_chat.add_argument("task", help="Task for collaborative chat")
    multiai_chat.add_argument("--rounds", type=int, default=3)
    multiai_chat.add_argument("--model", default=None)

    multiai_personas = multiai_sub.add_parser("personas", help="List persona presets")

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

    # update subcommand - check for GitHub updates and show in dashboard and CLI too
    update_parser = subparsers.add_parser("update", help="Update from GitHub - shows update in dashboard and CLI too - like hermes update")
    update_parser.add_argument("--check", action="store_true", help="Only check for updates, don't pull")

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

    elif args.command == "multikey":
        from core.multi_key import multi_key_manager
        import json as json_lib
        if args.multikey_action == "add":
            result = multi_key_manager.add_key(args.provider, args.key, name=args.name)
            print(f"Add key result: {result}")
            if result.get("success"):
                print(f"✅ Added key for {args.provider}, now total {result['total_keys']} keys - will use round-robin + fallback, completing quickly")
        elif args.multikey_action == "list":
            apis = multi_key_manager.list_keys(args.provider)
            print(f"Multi-API Keys:")
            for provider, keys in apis.items():
                print(f" {provider}: {len(keys)} keys")
                for k in keys:
                    if isinstance(k, dict):
                        print(f"   - {k.get('name')}: {k.get('key','')[:10]}... usage={k.get('usage_count',0)} added={k.get('added','')[:10]}")
                    else:
                        print(f"   - {k[:10]}...")
        elif args.multikey_action == "remove":
            result = multi_key_manager.remove_key(args.provider, args.key)
            print(f"Remove result: {result}")
        elif args.multikey_action == "parallel":
            # Example parallel tasks with different keys
            tasks = []
            if args.tasks:
                for t in args.tasks:
                    tasks.append({"prompt": t, "messages": [{"role": "user", "content": t}]})
            else:
                tasks = [
                    {"prompt": "What is Python async?", "messages": [{"role": "user", "content": "What is Python async?"}]},
                    {"prompt": "What is Rust async?", "messages": [{"role": "user", "content": "What is Rust async?"}]},
                    {"prompt": "What is Go concurrency?", "messages": [{"role": "user", "content": "What is Go concurrency?"}]},
                ]
            results = multi_key_manager.execute_parallel_with_keys(args.provider, tasks)
            print(f"Parallel results with {args.provider} multiple keys ({len(results)} tasks):")
            for r in results:
                print(f" - Task {r.get('task_id')}: success={r.get('success')} key={r.get('api_key')} response={str(r.get('response',''))[:150]}")
        else:
            parser.parse_args(["multikey", "--help"])

    elif args.command == "multiai":
        from core.multi_ai import multi_ai_manager, PERSONA_PRESETS
        if args.multiai_action == "debate":
            from core.multi_ai import MultiAIChat
            chat = MultiAIChat()
            # Add agents based on personas or default team
            if args.agents:
                for persona_name in args.agents:
                    persona_desc = PERSONA_PRESETS.get(persona_name, f"You are a {persona_name}")
                    chat.add_agent(persona_name, persona_desc, model=args.model)
            else:
                chat.add_default_team(model=args.model)
            result = chat.debate(args.topic, rounds=args.rounds, model=args.model)
            print(f"\n=== Multi-AI Debate: {result['topic']} ===")
            print(f"Agents: {', '.join(result['agents'])} | Rounds: {result['rounds']}")
            for turn in result['history']:
                print(f"\n[{turn['agent']} - Round {turn['round']}]: {turn['content'][:500]}")
            print(f"\n=== Final Answer ===\n{result['final_answer']}")
        elif args.multiai_action == "chat":
            from core.multi_ai import MultiAIChat
            chat = MultiAIChat()
            chat.add_default_team(model=args.model)
            result = chat.collaborate_on_task(args.task, rounds=args.rounds)
            print(f"\n=== Multi-AI Collaborative Chat: {result['task']} ===")
            for turn in result['history']:
                print(f"\n[{turn['agent']} - R{turn['round']}]: {turn['content'][:500]}")
            print(f"\n=== Final ===\n{result['final']}")
        elif args.multiai_action == "personas":
            print("Persona presets free:")
            for name, desc in PERSONA_PRESETS.items():
                print(f" - {name}: {desc}")
        else:
            parser.parse_args(["multiai", "--help"])

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

    elif args.command == "update":
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        if args.check:
            print("🔍 Checking for updates from GitHub...")
            result = updater.check_for_updates()
            if result.get("update_available"):
                print(f"\n🚀 Update available! {result.get('message')}")
                print(f"Local: {result.get('local',{}).get('short')} - {result.get('local',{}).get('message','')[:80]}")
                print(f"Remote: {result.get('remote',{}).get('short')} - {result.get('remote',{}).get('message','')[:80]} by {result.get('remote',{}).get('author','')} on {result.get('remote',{}).get('date','')[:10]}")
                print(f"Behind by: {result.get('behind_by',1)} commit(s)")
                print(f"Remote URL: {result.get('remote_url','')}")
                print("\nTo update: Run 'hermus update' without --check or 'git pull' - shows in dashboard and CLI")
                print("Dashboard will show banner if update available at http://localhost:8000/dashboard")
            elif result.get("up_to_date"):
                print(f"\n✅ Up to date! {result.get('message')}")
            else:
                print(f"\nUpdate check result: {result}")
        else:
            print("🔄 Updating from GitHub via git pull origin main + pip install -r requirements.txt...")
            result = updater.update()
            if result.get("success"):
                print(f"\n✅ Update success! {result.get('message')}")
                print(f"New commit: {result.get('new_commit',{}).get('short')} - {result.get('new_commit',{}).get('message','')[:80]}")
                print("Pull output:", result.get("pull_stdout","")[:500])
                print("\nDashboard and CLI will now show up to date - refresh dashboard to see new version")
            else:
                print(f"\n❌ Update failed: {result.get('error', result.get('pull_stderr',''))[:500]}")

    else:
        # Default: start TUI - show update check on startup
        print(f"Hermus Agent Free - Model {args.model} | Mode {args.mode}")
        # Check for updates on startup and show in CLI
        try:
            from core.updater import get_updater_for_current_repo
            updater = get_updater_for_current_repo()
            result = updater.check_for_updates()
            if result.get("update_available"):
                print(f"\n🚀 Update available! {result.get('message')}")
                print(f"   Run 'hermus update' to update - shows in dashboard and CLI")
                print(f"   Dashboard http://localhost:8000/dashboard will show banner")
        except:
            pass
        print("Starting TUI (full terminal interface with slash commands)...")
        print(f"Modes: agent can control everything, chat let's u chat, multi-agent can use multiple keys at once and reach goal no matter how difficult, multi-chat can get accurate reliable info with multiple ai models and api keys")
        try:
            from tui.tui import HermusTUI
            tui = HermusTUI(model=args.model, mode=args.mode)
            tui.run()
        except ImportError as e:
            print(f"TUI deps missing: {e} - pip install prompt_toolkit rich - falling back to simple agent")
            from core.agent import HermusAgent
            agent = HermusAgent(model=args.model, mode=args.mode)
            print(f"Simple chat in {args.mode} mode (type /exit to quit, /new for new session, /skills to list skills, /mode to switch)")
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

if __name__ == "__main__":
    main()
