"""multikey — Multi-API Keys - ANY OpenAI-compatible key (groq/openai/openrouter/gemini/custom/...). Health, models, rate limits, parallel.."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
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


def run(args, ctx: CLIContext) -> None:

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
        ctx.parser.parse_args(["multikey", "--help"])
