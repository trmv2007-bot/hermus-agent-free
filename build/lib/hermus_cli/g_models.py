"""models commands — the Hermus CLI's models group.

Part of the grouped CLI: one module per capability group instead of one
module per command. Each command keeps its own ``configure``/``run`` pair,
so the command bodies are unchanged while the import shim, docstring and
``TYPE_CHECKING`` block that every one of the forty-three files carried are
gone.
"""

from __future__ import annotations

from ._common import CLIContext
from ._spec import Command, no_action


def _configure_multikey(subparsers) -> None:
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
    multikey_sub.add_parser("providers", help="List known AI providers")


def _run_multikey(args, ctx: CLIContext) -> None:
    from core.multi_key import multi_key_manager
    from core.providers import list_providers

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
        if result.get("success"):
            print(
                f"\n✅ Added {args.provider} key '{result.get('key_name')}'. Total keys for provider: {result.get('total_keys')}"
            )
            print(f"   Model       : {args.provider}/{result.get('default_model') or 'MODEL'}")
            print(f"   Base URL    : {result.get('base_url') or '(provider default)'}")
            print(f"   Preset      : {result.get('preset') or args.provider}")
            h = result.get("health") or {}
            if h:
                print(
                    f"   Health      : {h.get('status')} healthy={h.get('healthy')} "
                    f"latency={h.get('latency_ms')}ms models={h.get('models_count')}"
                )
                if h.get("models_sample"):
                    print(f"   Models      : {', '.join(h['models_sample'][:8])}")
                if h.get("error"):
                    print(f"   Probe error : {str(h['error'])[:200]}")
        else:
            print(f"❌ {result.get('error', 'Failed to add key')}")
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
                f"response={str(r.get('response') or r.get('error', ''))[:150]}"
            )
    elif args.multikey_action == "health":
        results = multi_key_manager.check_all_health(args.provider)
        ok = sum(1 for r in results if r.get("healthy"))
        print(f"\nKey health — {ok}/{len(results)} healthy\n")
        for r in results:
            mark = "✅" if r.get("healthy") else "❌"
            mp = r.get("models_probe") or {}
            print(
                f" {mark} {r.get('provider')}/{r.get('key_name', '')} — {r.get('status', 'ok' if r.get('healthy') else 'bad')}"
                f" | latency={r.get('latency_ms')}ms | model={r.get('model_tested')}"
            )
            print(
                f"     base_url={r.get('base_url') or '(preset)'} | models found={mp.get('count', 0)}"
                f"{(' | sample: ' + ', '.join((mp.get('sample') or [])[:6])) if mp.get('sample') else ''}"
            )
            if r.get("error"):
                print(f"     error: {str(r['error'])[:200]}")
    elif args.multikey_action == "models":
        result = multi_key_manager.discover_models(
            args.provider,
            api_key=args.key,
            base_url=getattr(args, "base_url", None),
        )
        if result.get("success"):
            print(f"✅ {result.get('count', 0)} models for {args.provider} (base_url={result.get('base_url') or '(preset)'}):")
            for m in (result.get("models") or [])[:80]:
                print(f"   - {m.get('id') if isinstance(m, dict) else m}")
        else:
            print(f"❌ Could not list models: {result.get('error', 'unknown error')}")
    elif args.multikey_action == "rates":
        rates = multi_key_manager.rate_status(args.provider)
        print("\nRate limits (RPM/TPM used vs limit):")
        for k in rates.get("keys") or []:
            mark = "✅" if k.get("healthy") else ("❌" if k.get("healthy") is False else "❓")
            rt = f"{k['avg_response_time']:.2f}s" if k.get("avg_response_time") is not None else "—"
            print(
                f" {mark} {k.get('provider')}/{k.get('name', '')} | RPM {k.get('rpm_used', 0)}/{k.get('rpm_limit') or '∞'}"
                f" | TPM {k.get('tpm_used', 0)}/{k.get('tpm_limit') or '∞'}"
                f" | avg response {rt} | model={k.get('default_model')}"
            )
    elif args.multikey_action == "providers":
        from core.provider_resolver import diagnose, list_available_providers

        status = {p["provider"]: p for p in list_available_providers()}
        diag = diagnose()
        for p in list_providers():
            try:
                info = status.get(p["id"]) or {}
            except Exception:
                info = {}
            mark = "✅" if info.get("configured") else "❔"
            source = f" | source={info.get('credential_source')}" if info.get("credential_source") else ""
            tools = f" | tools={'yes' if info.get('supports_tools') else 'no'}" if info.get("configured") else ""
            print(f" {mark} {p['id']}: {p['name']} | default={p.get('default_model')} | {p.get('base_url')}{source}{tools}")
            budget = " / ".join(
                part
                for part in (
                    f"{p['default_rpm']:,} RPM" if p.get("default_rpm") else "",
                    f"{p['default_tpm']:,} TPM" if p.get("default_tpm") else "",
                )
                if part
            )
            print(f"     rate budget: {budget or 'unmetered (no published per-minute limit)'}")
            if p.get("notes"):
                print(f"     {p['notes']}")
        print(
            "\nRecommended provider: "
            f"{diag.get('recommended_provider') or 'none'} "
            f"({diag.get('recommended_model') or '<no model>'})"
        )
    else:
        no_action(ctx, "multikey")


def _configure_fleet(subparsers) -> None:
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


def _run_fleet(args, ctx: CLIContext) -> None:
    from core.model_fleet import model_fleet

    def _split(s):
        return [x.strip() for x in (s or "").split(",") if x.strip()] or None

    if args.fleet_action == "workers":
        w = model_fleet.list_workers(models=_split(args.models), providers=_split(args.providers))
        print(f"\nFleet workers ({w.get('count', 0)}):")
        for worker in w.get("workers") or []:
            print(
                f" - {worker.get('name')}: {worker.get('provider')}/{worker.get('model')}"
                f" | key={'yes' if worker.get('has_key') else 'no'}"
                f" | base_url={worker.get('base_url') or '(preset)'}"
            )
        if w.get("providers_configured"):
            print(f"Providers configured: {', '.join(w['providers_configured'])}")
    elif args.fleet_action == "run":
        result = model_fleet.auto_distribute(
            args.goal,
            strategy=args.strategy,
            models=_split(args.models),
            providers=_split(args.providers),
            max_workers=args.workers,
        )
        print(
            f"\nFleet run: mode={result.get('mode')} strategy={result.get('strategy') or args.strategy}"
            f" | success={result.get('success')} workers_used={result.get('workers_used') or len(result.get('results') or [])}"
        )
        if result.get("subtasks"):
            print("Subtasks:")
            for i, s in enumerate(result["subtasks"], 1):
                print(f"   {i}. {s}")
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
        if result.get("error"):
            print(f"\n⚠️ {result['error']}")
    elif args.fleet_action == "fanout":
        result = model_fleet.fanout(
            args.prompt, models=_split(args.models), providers=_split(args.providers), max_workers=args.workers
        )
        print("Workers:", result.get("workers_used"), "success:", result.get("success"))
        if result.get("consensus"):
            print("\n=== CONSENSUS ===\n", result["consensus"][:4000])
    elif args.fleet_action == "map":
        result = model_fleet.map_goal(
            args.goal, models=_split(args.models), providers=_split(args.providers), max_workers=args.workers
        )
        print("Subtasks:")
        for i, s in enumerate(result.get("subtasks") or [], 1):
            print(f"   {i}. {s}")
        if result.get("merged"):
            print("\n=== MERGED ===\n", result["merged"][:4000])
    else:
        no_action(ctx, "fleet")


def _configure_multiai(subparsers) -> None:
    multiai_parser = subparsers.add_parser("multiai", help="Multi-AI - multiple AIs talk to each other for anything")
    multiai_sub = multiai_parser.add_subparsers(dest="multiai_action")
    multiai_debate = multiai_sub.add_parser("debate", help="Multi-AI debate on topic")
    multiai_debate.add_argument("topic", help="Topic for debate")
    multiai_debate.add_argument("--rounds", type=int, default=2, help="Rounds")
    multiai_debate.add_argument("--model", default=None, help="Model for all agents")
    multiai_debate.add_argument(
        "--agents",
        nargs="+",
        default=None,
        help="Agent personas: researcher coder reviewer writer planner debater optimist pessimist",
    )

    multiai_chat = multiai_sub.add_parser("chat", help="Multi-AI collaborative chat")
    multiai_chat.add_argument("task", help="Task for collaborative chat")
    multiai_chat.add_argument("--rounds", type=int, default=3)
    multiai_chat.add_argument("--model", default=None)

    multiai_sub.add_parser("personas", help="List persona presets")


def _run_multiai(args, ctx: CLIContext) -> None:
    from core.multi_ai import PERSONA_PRESETS

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
        for turn in result["history"]:
            print(f"\n[{turn['agent']} - Round {turn['round']}]: {turn['content'][:500]}")
        print(f"\n=== Final Answer ===\n{result['final_answer']}")
    elif args.multiai_action == "chat":
        from core.multi_ai import MultiAIChat

        chat = MultiAIChat()
        chat.add_default_team(model=args.model)
        result = chat.collaborate_on_task(args.task, rounds=args.rounds)
        print(f"\n=== Multi-AI Collaborative Chat: {result['task']} ===")
        for turn in result["history"]:
            print(f"\n[{turn['agent']} - R{turn['round']}]: {turn['content'][:500]}")
        print(f"\n=== Final ===\n{result['final']}")
    elif args.multiai_action == "personas":
        print("Persona presets free:")
        for name, desc in PERSONA_PRESETS.items():
            print(f" - {name}: {desc}")
    else:
        no_action(ctx, "multiai")


def _configure_router(subparsers) -> None:
    router_parser = subparsers.add_parser("router", help="Model Router 2.0 - per-step model selection")
    router_sub = router_parser.add_subparsers(dest="router_action")
    router_choose = router_sub.add_parser("choose", help="Choose the best model for a step")
    router_choose.add_argument("text")


def _run_router(args, ctx: CLIContext) -> None:
    from core.router2 import router2

    sel = router2.select(args.text)
    print(f"Task type : {sel['task_type']} (difficulty {sel['difficulty']}, ~{sel['context_tokens']} tokens)")
    print(f"Model     : {sel['model']}")
    print(f"Reason    : {sel['reason']}")
    if sel.get("alternatives"):
        print(f"Alt       : {', '.join(sel['alternatives'])}")


def _configure_api(subparsers) -> None:
    api_parser = subparsers.add_parser("api", help="Custom API - add any API as tool (free)")
    api_sub = api_parser.add_subparsers(dest="api_action")
    api_add = api_sub.add_parser("add", help="Add custom API")
    api_add.add_argument("--name", required=True, help="API tool name, e.g., weather_api")
    api_add.add_argument("--description", required=True, help="Description for LLM")
    api_add.add_argument(
        "--url", required=True, help="URL with optional {param} placeholders, e.g., https://api.example.com/weather/{city}"
    )
    api_add.add_argument("--method", default="GET", choices=["GET", "POST", "PUT", "DELETE"], help="HTTP method")
    api_add.add_argument("--header", action="append", help="Headers as Key:Value, can repeat")
    api_add.add_argument("--param", action="append", help="Parameters as name:description, e.g., city:City name, can repeat")
    api_add.add_argument("--auth-type", choices=["none", "bearer", "apikey", "basic"], default="none")
    api_add.add_argument("--auth-token", help="Bearer token or API key value")
    api_add.add_argument("--auth-key", help="API key header name (for apikey type) or username (for basic)")
    api_add.add_argument("--auth-password", help="Password for basic auth")

    api_sub.add_parser("list", help="List custom APIs")
    api_remove = api_sub.add_parser("remove", help="Remove custom API")
    api_remove.add_argument("name", help="API name or id")
    api_test = api_sub.add_parser("test", help="Test custom API")
    api_test.add_argument("name", help="API name")
    api_test.add_argument("--args", help='JSON args for test, e.g., \'{"city": "London"}\'')

    api_discover = api_sub.add_parser(
        "discover",
        help="Find useful APIs in the public-apis catalog (offline snapshot)",
    )
    api_discover.add_argument("query", nargs="?", default="", help="Task/API keywords, e.g. weather or threat intelligence")
    api_discover.add_argument("--category", default="", help="Category filter, e.g. Security")
    api_discover.add_argument("--auth", default="any", help="any, No, apiKey, OAuth, ...")
    api_discover.add_argument("--allow-http", action="store_true", help="Include APIs without confirmed HTTPS")
    api_discover.add_argument("--cors", default="any", choices=["any", "Yes", "No", "Unknown"])
    api_discover.add_argument("--limit", type=int, default=10)
    api_discover.add_argument("--refresh", action="store_true", help="Refresh the runtime catalog from GitHub first")
    api_sub.add_parser("categories", help="List public API categories and free/HTTPS counts")
    api_sub.add_parser("refresh-catalog", help="Refresh public API catalog from GitHub")


def _run_api(args, ctx: CLIContext) -> None:
    from core.custom_api import custom_api_manager

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
            "auth": auth,
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
            print(f" - {api['name']}: {api['description']} | {api['method']} {api['url']} | enabled={api.get('enabled', True)}")
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
            except Exception:
                print(f"Failed to parse --args JSON: {args.args}, using empty")
        result = custom_api_manager.execute_api(args.name, test_args)
        print(f"Test {args.name} with {test_args}:")
        if not result.get("success"):
            print(f"❌ error: {result.get('error')}")
            print(f"   url: {result.get('url')}")
        else:
            print(f"✅ HTTP {result.get('status_code')}")
            print(f"   url: {result.get('url')}")
            data = result.get("data_str") or result.get("data")
            if data:
                print(f"   data: {str(data)[:1000]}")
            if result.get("used_key"):
                print(f"   key used: {result['used_key']}")
                print(f"   keys for this API: {result.get('total_keys_for_this_api')}")

    elif args.api_action == "discover":
        from tools.public_apis import public_api_catalog

        result = public_api_catalog.search(
            query=args.query,
            category=args.category,
            auth=args.auth,
            https_only=not args.allow_http,
            cors=args.cors,
            limit=args.limit,
            refresh=args.refresh,
        )
        refresh = result.get("refresh")
        if refresh and not refresh.get("success"):
            print(f"⚠️ Refresh failed; using {refresh.get('using_fallback')}: {refresh.get('error')}")
        print(
            f"Public APIs matching '{args.query or '*'}': "
            f"{result.get('total_matched', 0)} found, showing {result.get('count', 0)}"
        )
        for item in result.get("results", []):
            print(f"\n - {item['name']} [{item['category']}]")
            print(f"   {item['description']}")
            print(f"   auth={item['auth']} https={item['https']} cors={item['cors']} | {item['documentation_url']}")
        catalog = result.get("catalog", {})
        print(f"\nSource: {catalog.get('source')} ({catalog.get('loaded_from')}, {catalog.get('total_apis')} APIs)")
        print("Note: links are documentation, not trusted endpoints. Review the docs, then use `hermus api add`.")

    elif args.api_action == "categories":
        from tools.public_apis import public_api_catalog

        result = public_api_catalog.categories()
        print(f"Public API categories ({result.get('count', 0)}):")
        for item in result.get("categories", []):
            print(
                f" - {item['category']}: total={item['total']} "
                f"no-auth={item['no_auth']} https={item['https']} cors={item['cors_yes']}"
            )

    elif args.api_action == "refresh-catalog":
        from tools.public_apis import public_api_catalog

        result = public_api_catalog.refresh()
        if result.get("success"):
            print(
                f"✅ Refreshed {result.get('count')} APIs across "
                f"{result.get('categories')} categories from {result.get('source')}"
            )
            print(f"   Runtime cache: {result.get('cache_path')}")
        else:
            print(f"❌ Refresh failed: {result.get('error')}")
            print(f"   Continuing with {result.get('using_fallback')} ({result.get('fallback_count')} APIs)")

    else:
        no_action(ctx, "api")


def _configure_mcp(subparsers) -> None:
    mcp_parser = subparsers.add_parser("mcp", help="MCP servers - connect external tool servers (stdio)")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")
    mcp_sub.add_parser("list", help="List MCP servers")
    mcp_add = mcp_sub.add_parser("add", help="Add MCP server")
    mcp_add.add_argument("--name", required=True)
    mcp_add.add_argument("--command", required=True, help="Executable e.g. npx or python3")
    mcp_add.add_argument("--arg", action="append", default=[], help="Repeatable arg")
    mcp_add.add_argument("--disabled", action="store_true", help="Add but leave disabled")
    mcp_remove = mcp_sub.add_parser("remove", help="Remove MCP server")
    mcp_remove.add_argument("name")
    mcp_sub.add_parser("connect", help="Connect enabled MCP servers and register tools")
    mcp_call = mcp_sub.add_parser("call", help="Call MCP tool")
    mcp_call.add_argument("--server", required=True)
    mcp_call.add_argument("--tool", required=True)
    mcp_call.add_argument("--args", default="{}", help="JSON arguments")


def _run_mcp(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.mcp_client import mcp_manager

    if args.mcp_action == "list":
        servers = mcp_manager.list_servers()
        print(f"MCP servers ({len(servers)}):")
        for s in servers:
            print(
                f" - {s.get('name')}: cmd={s.get('command')} enabled={s.get('enabled')} running={s.get('running')} tools={s.get('tool_count')}"
            )
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
        result = mcp_manager.call(args.server, args.tool, call_args)
        print(f"MCP call {args.server}/{args.tool} {call_args}:")
        if isinstance(result, dict):
            if result.get("error"):
                print(f"❌ {result['error']}")
            else:
                for k, v in result.items():
                    print(f"   {k}: {str(v)[:300]}")
        else:
            print(str(result)[:2000])
    else:
        no_action(ctx, "mcp")


def _configure_tools(subparsers) -> None:
    subparsers.add_parser("tools", help="List registered tools (auto registry)")


def _run_tools(args, ctx: CLIContext) -> None:
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


COMMANDS: tuple[Command, ...] = (
    Command(
        name="multikey",
        help="Multi-API Keys - ANY OpenAI-compatible key (groq/openai/openrouter/gemini/custom/...). Health, models, rate limits, parallel.",
        configure=_configure_multikey,
        run=_run_multikey,
    ),
    Command(
        name="fleet",
        help="Model fleet - distribute tasks across multiple AI models + API keys",
        configure=_configure_fleet,
        run=_run_fleet,
    ),
    Command(
        name="multiai",
        help="Multi-AI - multiple AIs talk to each other for anything",
        configure=_configure_multiai,
        run=_run_multiai,
    ),
    Command(name="router", help="Model Router 2.0 - per-step model selection", configure=_configure_router, run=_run_router),
    Command(name="api", help="Custom API - add any API as tool (free)", configure=_configure_api, run=_run_api),
    Command(name="mcp", help="MCP servers - connect external tool servers (stdio)", configure=_configure_mcp, run=_run_mcp),
    Command(name="tools", help="List registered tools (auto registry)", configure=_configure_tools, run=_run_tools),
)

__all__ = ["COMMANDS"]
