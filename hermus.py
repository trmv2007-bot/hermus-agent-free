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

    # multi-key subcommand - any AI API key works
    multikey_parser = subparsers.add_parser(
        "multikey",
        help="Multi-API Keys - ANY OpenAI-compatible key (groq/openai/openrouter/gemini/custom/...). Health, models, rate limits, parallel.",
    )
    multikey_sub = multikey_parser.add_subparsers(dest="multikey_action")
    multikey_add = multikey_sub.add_parser("add", help="Add ANY API key (auto model discover + health)")
    multikey_add.add_argument(
        "--provider",
        required=True,
        help="Provider id: groq, openai, openrouter, together, gemini, deepseek, mistral, cerebras, hf, custom, ...",
    )
    multikey_add.add_argument("--key", required=True, help="API key")
    multikey_add.add_argument("--name", help="Key name")
    multikey_add.add_argument("--base-url", help="OpenAI-compatible base URL e.g. https://api.groq.com/openai/v1")
    multikey_add.add_argument("--model", help="Default model id for this key")
    multikey_add.add_argument("--rpm", type=int, help="Requests-per-minute budget")
    multikey_add.add_argument("--tpm", type=int, help="Tokens-per-minute budget")
    multikey_add.add_argument("--no-probe", action="store_true", help="Skip auto health/model probe")
    multikey_list = multikey_sub.add_parser("list", help="List keys per provider (redacted + health)")
    multikey_list.add_argument("--provider", help="Provider filter")
    multikey_remove = multikey_sub.add_parser("remove", help="Remove key")
    multikey_remove.add_argument("--provider", required=True)
    multikey_remove.add_argument("--key", required=True, help="Key or name to remove")
    multikey_parallel = multikey_sub.add_parser("parallel", help="Run tasks in parallel on different keys")
    multikey_parallel.add_argument("--provider", default="groq")
    multikey_parallel.add_argument("--tasks", nargs="+", help="Tasks to run in parallel with different keys")
    multikey_health = multikey_sub.add_parser("health", help="Check API key health + rate limits + models")
    multikey_health.add_argument("--provider", help="Provider filter (default: all)")
    multikey_models = multikey_sub.add_parser("models", help="Discover models for a provider/key")
    multikey_models.add_argument("--provider", required=True)
    multikey_models.add_argument("--key", help="Specific key (optional)")
    multikey_models.add_argument("--base-url", help="Override base URL")
    multikey_rates = multikey_sub.add_parser("rates", help="Show RPM/TPM usage vs limits")
    multikey_rates.add_argument("--provider", help="Provider filter")
    multikey_providers = multikey_sub.add_parser("providers", help="List known AI providers")

    # fleet - multi-model task distribution
    fleet_parser = subparsers.add_parser(
        "fleet",
        help="Model fleet - distribute tasks across multiple AI models + API keys",
    )
    fleet_sub = fleet_parser.add_subparsers(dest="fleet_action")
    fleet_workers = fleet_sub.add_parser("workers", help="List available model/key workers")
    fleet_workers.add_argument("--providers", help="Comma-separated providers")
    fleet_workers.add_argument("--models", help="Comma-separated provider/model")
    fleet_run = fleet_sub.add_parser("run", help="Distribute a goal (auto|fanout|map|race)")
    fleet_run.add_argument("goal", help="Goal / prompt")
    fleet_run.add_argument("--strategy", default="auto", choices=["auto", "fanout", "map", "race"])
    fleet_run.add_argument("--models", help="Comma-separated provider/model")
    fleet_run.add_argument("--providers", help="Comma-separated providers")
    fleet_run.add_argument("--workers", type=int, default=4)
    fleet_fanout = fleet_sub.add_parser("fanout", help="Same prompt → many models → consensus")
    fleet_fanout.add_argument("prompt")
    fleet_fanout.add_argument("--models", help="Comma-separated")
    fleet_fanout.add_argument("--providers", help="Comma-separated")
    fleet_fanout.add_argument("--workers", type=int, default=4)
    fleet_map = fleet_sub.add_parser("map", help="Split goal into subtasks across models")
    fleet_map.add_argument("goal")
    fleet_map.add_argument("--models", help="Comma-separated")
    fleet_map.add_argument("--providers", help="Comma-separated")
    fleet_map.add_argument("--workers", type=int, default=4)

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

    # counsel subcommand - Council of AIs: talk, plan, execute, and upgrade itself
    counsel_parser = subparsers.add_parser(
        "counsel",
        help="Counsel System - council of AIs plans together, then upgrades itself",
    )
    counsel_sub = counsel_parser.add_subparsers(dest="counsel_action")
    counsel_run = counsel_sub.add_parser("run", help="Convene the council for a task")
    counsel_run.add_argument("goal", help="The task/goal for the council")
    counsel_run.add_argument("--rounds", type=int, default=None, help="Override deliberation rounds")
    counsel_run.add_argument("--difficulty", type=int, default=None, choices=[1, 2, 3, 4, 5], help="Override difficulty 1-5")
    counsel_run.add_argument("--members", type=int, default=None, help="Override max members")
    counsel_run.add_argument("--no-execute", action="store_true", help="Plan only, skip tool execution")
    counsel_run.add_argument("--model", default=None, help="Base model for members (auto-diversified)")
    counsel_status = counsel_sub.add_parser("status", help="Council status: constitution version, roster, upgrades")
    counsel_amend = counsel_sub.add_parser("amend", help="Self-upgrade amendments (Meta-Counsel)")
    counsel_amend_sub = counsel_amend.add_subparsers(dest="counsel_amend_action")
    counsel_amend_list = counsel_amend_sub.add_parser("list", help="List pending amendments + upgrade history")
    counsel_amend_approve = counsel_amend_sub.add_parser("approve", help="Approve a pending high-risk amendment")
    counsel_amend_approve.add_argument("amendment_id")
    counsel_amend_reject = counsel_amend_sub.add_parser("reject", help="Reject a pending amendment")
    counsel_amend_reject.add_argument("amendment_id")
    counsel_amend_rollback = counsel_amend_sub.add_parser("rollback", help="Roll back constitution to a previous version")
    counsel_amend_rollback.add_argument("version", type=int)
    counsel_review = counsel_sub.add_parser("review", help="Run Meta-Counsel review on the last council session")
    counsel_review.add_argument("--session-id", default=None, help="Specific session id (default: latest)")

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

    # mcp subcommand - Model Context Protocol servers
    mcp_parser = subparsers.add_parser("mcp", help="MCP servers - connect external tool servers (stdio)")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")
    mcp_list = mcp_sub.add_parser("list", help="List MCP servers")
    mcp_add = mcp_sub.add_parser("add", help="Add MCP server")
    mcp_add.add_argument("--name", required=True)
    mcp_add.add_argument("--command", required=True, help="Executable e.g. npx or python3")
    mcp_add.add_argument("--arg", action="append", default=[], help="Repeatable arg")
    mcp_add.add_argument("--disabled", action="store_true", help="Add but leave disabled")
    mcp_remove = mcp_sub.add_parser("remove", help="Remove MCP server")
    mcp_remove.add_argument("name")
    mcp_connect = mcp_sub.add_parser("connect", help="Connect enabled MCP servers and register tools")
    mcp_call = mcp_sub.add_parser("call", help="Call MCP tool")
    mcp_call.add_argument("--server", required=True)
    mcp_call.add_argument("--tool", required=True)
    mcp_call.add_argument("--args", default="{}", help="JSON arguments")

    # embeddings / semantic memory
    emb_parser = subparsers.add_parser("embed", help="Semantic memory - ingest docs + vector search (free local)")
    emb_sub = emb_parser.add_subparsers(dest="embed_action")
    emb_status = emb_sub.add_parser("status", help="Backend status")
    emb_ingest = emb_sub.add_parser("ingest", help="Ingest file or directory")
    emb_ingest.add_argument("path")
    emb_ingest.add_argument("--source", default=None)
    emb_search = emb_sub.add_parser("search", help="Semantic or hybrid search")
    emb_search.add_argument("query")
    emb_search.add_argument("--limit", type=int, default=5)
    emb_search.add_argument("--hybrid", action="store_true", default=True)
    emb_search.add_argument("--semantic-only", action="store_true")
    emb_clear = emb_sub.add_parser("clear", help="Clear embeddings")
    emb_clear.add_argument("--source", default=None)

    # tools list
    tools_parser = subparsers.add_parser("tools", help="List registered tools (auto registry)")

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
        from core.providers import list_providers
        import json as json_lib
        if args.multikey_action == "add":
            result = multi_key_manager.add_key(
                args.provider,
                args.key,
                name=args.name,
                base_url=getattr(args, "base_url", None),
                default_model=getattr(args, "model", None),
                rpm_limit=getattr(args, "rpm", None),
                tpm_limit=getattr(args, "tpm", None),
                auto_discover=not getattr(args, "no_probe", False),
            )
            print(json_lib.dumps(result, indent=2, default=str)[:4000])
            if result.get("success"):
                print(
                    f"\n✅ Added {args.provider} key '{result.get('key_name')}'. "
                    f"Total keys for provider: {result.get('total_keys')}"
                )
                print(f"   Use model like: --model {args.provider}/{result.get('default_model') or 'MODEL'}")
                h = result.get("health") or {}
                if h:
                    print(
                        f"   Health: {h.get('status')} healthy={h.get('healthy')} "
                        f"latency={h.get('latency_ms')}ms models={h.get('models_count')}"
                    )
                    if h.get("models_sample"):
                        print(f"   Models sample: {', '.join(h['models_sample'][:8])}")
        elif args.multikey_action == "list":
            apis = multi_key_manager.list_keys(args.provider, redact=True)
            print("Multi-API Keys (redacted):")
            for provider, keys in apis.items():
                print(f" {provider}: {len(keys)} keys")
                for k in keys:
                    print(
                        f"   - {k.get('name')}: {k.get('preview')} | model={k.get('default_model')} "
                        f"| healthy={k.get('healthy')} status={k.get('health_status')} "
                        f"| models={k.get('models_count')} rpm={k.get('rpm_limit')} "
                        f"| avg_rt={k.get('avg_response_time')} usage={k.get('usage_count')}"
                    )
                    if k.get("models_sample"):
                        print(f"     models: {', '.join(k['models_sample'][:6])}")
        elif args.multikey_action == "remove":
            result = multi_key_manager.remove_key(args.provider, args.key)
            print(f"Remove result: {result}")
        elif args.multikey_action == "parallel":
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
            print(f"Parallel results with {args.provider} ({len(results)} tasks):")
            for r in results:
                print(
                    f" - Task {r.get('task_id')}: success={r.get('success')} "
                    f"model={r.get('model')} key={r.get('api_key')} "
                    f"response={str(r.get('response') or r.get('error',''))[:150]}"
                )
        elif args.multikey_action == "health":
            results = multi_key_manager.check_all_health(args.provider)
            print(json_lib.dumps(results, indent=2, default=str)[:6000])
            ok = sum(1 for r in results if r.get("healthy"))
            print(f"\nHealthy: {ok}/{len(results)}")
        elif args.multikey_action == "models":
            result = multi_key_manager.discover_models(
                args.provider,
                api_key=args.key,
                base_url=getattr(args, "base_url", None),
            )
            print(json_lib.dumps(result, indent=2, default=str)[:6000])
        elif args.multikey_action == "rates":
            print(json_lib.dumps(multi_key_manager.rate_status(args.provider), indent=2, default=str)[:5000])
        elif args.multikey_action == "providers":
            for p in list_providers():
                print(f" - {p['id']}: {p['name']} | default={p.get('default_model')} | {p.get('base_url')}")
                if p.get("notes"):
                    print(f"     {p['notes']}")
        else:
            parser.parse_args(["multikey", "--help"])

    elif args.command == "fleet":
        from core.model_fleet import model_fleet
        import json as json_lib

        def _split(s):
            return [x.strip() for x in (s or "").split(",") if x.strip()] or None

        if args.fleet_action == "workers":
            print(json_lib.dumps(model_fleet.list_workers(models=_split(args.models), providers=_split(args.providers)), indent=2)[:4000])
        elif args.fleet_action == "run":
            result = model_fleet.auto_distribute(
                args.goal,
                strategy=args.strategy,
                models=_split(args.models),
                providers=_split(args.providers),
                max_workers=args.workers,
            )
            print(json_lib.dumps({k: result[k] for k in result if k not in ("results",)}, indent=2, default=str))
            if result.get("consensus"):
                print("\n=== CONSENSUS ===\n", result["consensus"][:3000])
            elif result.get("merged"):
                print("\n=== MERGED ===\n", result["merged"][:3000])
            elif result.get("winner"):
                print("\n=== WINNER ===\n", (result["winner"].get("response") or "")[:3000])
            else:
                for r in (result.get("results") or [])[:5]:
                    print(f"\n--- {r.get('model')} success={r.get('success')} ---")
                    print((r.get("response") or r.get("error") or "")[:800])
        elif args.fleet_action == "fanout":
            result = model_fleet.fanout(args.prompt, models=_split(args.models), providers=_split(args.providers), max_workers=args.workers)
            print("Workers:", result.get("workers_used"), "success:", result.get("success"))
            if result.get("consensus"):
                print("\n=== CONSENSUS ===\n", result["consensus"][:4000])
        elif args.fleet_action == "map":
            result = model_fleet.map_goal(args.goal, models=_split(args.models), providers=_split(args.providers), max_workers=args.workers)
            print("Subtasks:", result.get("subtasks"))
            if result.get("merged"):
                print("\n=== MERGED ===\n", result["merged"][:4000])
        else:
            parser.parse_args(["fleet", "--help"])

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

    elif args.command == "counsel":
        import json as _json

        from core.counsel.constitution import constitution
        from core.counsel.council import CouncilSession
        from core.counsel.meta import meta_counsel

        if args.counsel_action == "run":
            result = CouncilSession(
                args.goal,
                model=args.model,
                difficulty=args.difficulty,
                max_members=args.members,
                max_rounds=args.rounds,
                execute=not args.no_execute,
            ).run()
            print(f"\n{'='*60}")
            print(f"⚖️ COUNSEL SESSION: {result['session_id']}")
            print(f"   Difficulty: {result['difficulty']} | Members: "
                  f"{', '.join(m['name'] for m in result['members'])}")
            print(f"   Votes: {_json.dumps(result.get('votes'))}")
            print(f"{'='*60}")
            if result.get("plan") and result["plan"].get("steps"):
                print("\n📋 VOTED PLAN:")
                for i, s in enumerate(result["plan"]["steps"], 1):
                    print(f"   {i}. [{s.get('status','pending')}] {s.get('goal','')}")
            print(f"\n✅ FINAL ANSWER:\n{result['final_answer']}")
            if result.get("errors"):
                print(f"\n⚠️  Council errors: {result['errors']}")
        elif args.counsel_action == "status":
            st = meta_counsel.status()
            print(f"Council status — constitution v{st['constitution']['version']} ({st['constitution']['name']})")
            print(f"  Members: {', '.join(st['constitution']['members'])}")
            print(f"  Rules: {_json.dumps(st['constitution']['rules'])}")
            print(f"  Budget: {_json.dumps(st['constitution']['budget'])}")
            print(f"  Pending amendments: {st['pending_amendments']}")
            print(f"  Meta reviews logged: {st['reviews_logged']}")
            print(f"  Upgrade events: {st['constitution']['upgrade_events']}")
            for ev in constitution.upgrade_log()[-5:]:
                print(f"    - {ev.get('event')} v{ev.get('new_version') or ev.get('version') or ev.get('to_version')} {ev.get('timestamp','')[:19]}")
        elif args.counsel_action == "amend":
            if args.counsel_amend_action == "list":
                pending = constitution.pending_amendments()
                print(f"Pending amendments ({len(pending)}):")
                for p in pending:
                    print(f"  - [{p['id']}] target={p.get('target')} risk=high | {p.get('change','')[:120]}")
                    print(f"      reason: {p.get('reason','')[:180]}")
                print(f"\nUpgrade history (last 10):")
                for ev in constitution.upgrade_log()[-10:]:
                    print(f"  - {ev.get('event')} | v{ev.get('new_version') or ev.get('version') or ev.get('to_version')} | {ev.get('reason','')[:100]} | {ev.get('timestamp','')[:19]}")
                print("\nUse: hermus counsel amend approve <id> | reject <id> | rollback <version>")
            elif args.counsel_amend_action == "approve":
                res = constitution.approve(args.amendment_id)
                print(f"Approve result: {res}")
            elif args.counsel_amend_action == "reject":
                res = constitution.reject(args.amendment_id)
                print(f"Reject result: {res}")
            elif args.counsel_amend_action == "rollback":
                res = constitution.rollback(args.version)
                print(f"Rollback result: {res}")
            else:
                parser.parse_args(["counsel", "amend", "--help"])
        elif args.counsel_action == "review":
            session_id = args.session_id
            if not session_id:
                import glob

                files = sorted(glob.glob(str(config.resolve_path("data/counsel/sessions/*.json"))))
                if not files:
                    print("No council sessions yet — run `hermus counsel run \"task\"` first")
                    sys.exit(1)
                session_id = Path(files[-1]).stem
            from pathlib import Path as _P

            summary = _json.loads(_P(config.resolve_path(f"data/counsel/sessions/{session_id}.json")).read_text())
            res = meta_counsel.review_session(summary)
            print(f"Meta-Counsel review of {session_id}: proposed {res['proposed']} amendment(s)")
            for r in res.get("results", []):
                print(f"  - {r}")
        else:
            parser.parse_args(["counsel", "--help"])

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

    elif args.command == "mcp":
        from core.mcp_client import mcp_manager
        import json as json_lib
        if args.mcp_action == "list":
            servers = mcp_manager.list_servers()
            print(f"MCP servers ({len(servers)}):")
            for s in servers:
                print(f" - {s.get('name')}: cmd={s.get('command')} enabled={s.get('enabled')} running={s.get('running')} tools={s.get('tool_count')}")
                if s.get("last_error"):
                    print(f"   error: {s['last_error']}")
        elif args.mcp_action == "add":
            result = mcp_manager.add_server(
                args.name,
                args.command,
                args=args.arg or [],
                enabled=not args.disabled,
            )
            print(result)
            print("Tip: hermus mcp connect  # register tools on agent")
        elif args.mcp_action == "remove":
            print(mcp_manager.remove_server(args.name))
        elif args.mcp_action == "connect":
            result = mcp_manager.connect_enabled()
            from core.tool_registry import tool_registry
            tool_registry.load(force=True)
            info = tool_registry.list_tools()
            mcp_tools = [t for t in info.get("tools", []) if t.startswith("mcp_")]
            print(result)
            print(f"Registered MCP tools ({len(mcp_tools)}): {mcp_tools}")
        elif args.mcp_action == "call":
            try:
                call_args = json_lib.loads(args.args)
            except Exception:
                call_args = {}
            print(json_lib.dumps(mcp_manager.call(args.server, args.tool, call_args), indent=2)[:3000])
        else:
            parser.parse_args(["mcp", "--help"])

    elif args.command == "embed":
        from core.embeddings import embedding_store
        import json as json_lib
        if args.embed_action == "status":
            print(json_lib.dumps(embedding_store.backend_info(), indent=2))
        elif args.embed_action == "ingest":
            print(json_lib.dumps(embedding_store.ingest_path(args.path, source=args.source), indent=2))
        elif args.embed_action == "search":
            if args.semantic_only:
                print(json_lib.dumps(embedding_store.search(args.query, limit=args.limit), indent=2)[:4000])
            else:
                print(json_lib.dumps(embedding_store.hybrid_search(args.query, limit=args.limit), indent=2)[:4000])
        elif args.embed_action == "clear":
            print(embedding_store.clear(source=args.source))
        else:
            parser.parse_args(["embed", "--help"])

    elif args.command == "tools":
        from core.tool_registry import tool_registry
        info = tool_registry.list_tools()
        print(f"Registered tools: {info['count']} defs={info['definitions']}")
        for t in info.get("tools", []):
            src = info.get("sources", {}).get(t, "")
            print(f" - {t}  [{src}]")
        if info.get("errors"):
            print("Load errors:")
            for e in info["errors"]:
                print(f"   ! {e}")

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
