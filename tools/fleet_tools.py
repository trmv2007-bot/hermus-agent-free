"""Tools: multi-model fleet, key health, model discovery, rate limits — any API key."""


def list_ai_providers() -> dict:
    from core.providers import list_providers

    return {"providers": list_providers(), "count": len(list_providers())}


def list_api_keys(provider: str = None) -> dict:
    from core.multi_key import multi_key_manager

    return {"keys": multi_key_manager.list_keys(provider, redact=True)}


def add_api_key(
    provider: str,
    api_key: str,
    name: str = None,
    base_url: str = None,
    default_model: str = None,
    rpm_limit: int = None,
    tpm_limit: int = None,
) -> dict:
    """Add any AI API key (OpenAI-compatible). Auto health + model discovery."""
    from core.multi_key import multi_key_manager

    return multi_key_manager.add_key(
        provider=provider,
        api_key=api_key,
        name=name,
        base_url=base_url,
        default_model=default_model,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        auto_discover=True,
    )


def discover_models(provider: str, api_key: str = None, base_url: str = None) -> dict:
    """List models this API key can run."""
    from core.multi_key import multi_key_manager

    return multi_key_manager.discover_models(provider, api_key=api_key, base_url=base_url)


def check_api_key_health(
    provider: str = None,
    api_key: str = None,
    base_url: str = None,
    model: str = None,
    all_keys: bool = False,
) -> dict:
    """Health check: auth, latency, rate-limit headers, usable models."""
    from core.multi_key import multi_key_manager

    if all_keys or (not provider and not api_key):
        results = multi_key_manager.check_all_health(provider)
        healthy = sum(1 for r in results if r.get("healthy"))
        return {
            "results": results,
            "healthy": healthy,
            "total": len(results),
            "unhealthy": len(results) - healthy,
        }
    if not provider:
        return {"error": "provider required (or all_keys=true)"}
    return multi_key_manager.check_key_health(provider, api_key=api_key, base_url=base_url, model=model)


def get_rate_limit_status(provider: str = None) -> dict:
    """RPM/TPM usage vs limits + last server-reported rate headers."""
    from core.multi_key import multi_key_manager

    return multi_key_manager.rate_status(provider)


def fleet_list_workers(models: str = None, providers: str = None) -> dict:
    from core.model_fleet import model_fleet

    m = [x.strip() for x in (models or "").split(",") if x.strip()] or None
    p = [x.strip() for x in (providers or "").split(",") if x.strip()] or None
    return model_fleet.list_workers(models=m, providers=p)


def fleet_distribute_task(
    goal: str,
    strategy: str = "auto",
    models: str = None,
    providers: str = None,
    max_workers: int = 4,
) -> dict:
    """
    Give tasks to multiple AI models/keys in parallel.
    strategy: auto | fanout | map | race
    models: comma-separated provider/model e.g. groq/openai/gpt-oss-20b,openai/gpt-4o-mini
    """
    from core.model_fleet import model_fleet

    m = [x.strip() for x in (models or "").split(",") if x.strip()] or None
    p = [x.strip() for x in (providers or "").split(",") if x.strip()] or None
    return model_fleet.auto_distribute(
        goal,
        strategy=strategy,
        models=m,
        providers=p,
        max_workers=max_workers,
    )


def fleet_fanout(prompt: str, models: str = None, providers: str = None, max_workers: int = 4) -> dict:
    from core.model_fleet import model_fleet

    m = [x.strip() for x in (models or "").split(",") if x.strip()] or None
    p = [x.strip() for x in (providers or "").split(",") if x.strip()] or None
    return model_fleet.fanout(prompt, models=m, providers=p, max_workers=max_workers, judge=True)


def fleet_map_goal(goal: str, models: str = None, providers: str = None, max_workers: int = 4) -> dict:
    from core.model_fleet import model_fleet

    m = [x.strip() for x in (models or "").split(",") if x.strip()] or None
    p = [x.strip() for x in (providers or "").split(",") if x.strip()] or None
    return model_fleet.map_goal(goal, models=m, providers=p, max_workers=max_workers)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_ai_providers",
            "description": "List all known AI providers Hermus supports (OpenAI-compatible + presets). Any custom base_url also works.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_api_keys",
            "description": "List stored API keys (redacted) with health, models, rate limits",
            "parameters": {
                "type": "object",
                "properties": {"provider": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_api_key",
            "description": "Add ANY AI API key (OpenAI/Groq/OpenRouter/Together/Gemini/DeepSeek/custom). Auto-discovers models and checks health. For custom endpoints set base_url.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": "groq|openai|openrouter|together|gemini|deepseek|mistral|cerebras|custom|...",
                    },
                    "api_key": {"type": "string"},
                    "name": {"type": "string"},
                    "base_url": {
                        "type": "string",
                        "description": "OpenAI-compatible base e.g. https://api.groq.com/openai/v1",
                    },
                    "default_model": {"type": "string"},
                    "rpm_limit": {"type": "integer", "description": "Requests per minute budget"},
                    "tpm_limit": {"type": "integer", "description": "Tokens per minute budget"},
                },
                "required": ["provider", "api_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discover_models",
            "description": "Check what models can be run with this API key (GET /v1/models)",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "api_key": {"type": "string"},
                    "base_url": {"type": "string"},
                },
                "required": ["provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_api_key_health",
            "description": "Check API key health: auth OK, latency, rate-limit headers, sample usable models. Set all_keys=true to probe every stored key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "api_key": {"type": "string"},
                    "base_url": {"type": "string"},
                    "model": {"type": "string"},
                    "all_keys": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rate_limit_status",
            "description": "Get token/request rate usage vs limits for all keys (RPM/TPM + server headers)",
            "parameters": {
                "type": "object",
                "properties": {"provider": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_list_workers",
            "description": "List multi-model workers available (keys x models) for task distribution",
            "parameters": {
                "type": "object",
                "properties": {
                    "models": {"type": "string", "description": "comma-separated provider/model"},
                    "providers": {"type": "string", "description": "comma-separated providers"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_distribute_task",
            "description": "Distribute a goal across MULTIPLE AI models and API keys in parallel. strategy=auto|fanout|map|race. Use when user wants multi-model answers or hard goals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "strategy": {
                        "type": "string",
                        "enum": ["auto", "fanout", "map", "race"],
                        "default": "auto",
                    },
                    "models": {"type": "string", "description": "comma-separated e.g. groq/openai/gpt-oss-20b,openai/gpt-4o-mini"},
                    "providers": {"type": "string"},
                    "max_workers": {"type": "integer", "default": 4},
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_fanout",
            "description": "Send the SAME prompt to multiple models/keys in parallel and judge a consensus answer",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "models": {"type": "string"},
                    "providers": {"type": "string"},
                    "max_workers": {"type": "integer", "default": 4},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fleet_map_goal",
            "description": "Split a goal into subtasks and assign each to a different model/API key, then merge",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "models": {"type": "string"},
                    "providers": {"type": "string"},
                    "max_workers": {"type": "integer", "default": 4},
                },
                "required": ["goal"],
            },
        },
    },
]

TOOL_MAP = {
    "list_ai_providers": list_ai_providers,
    "list_api_keys": list_api_keys,
    "add_api_key": add_api_key,
    "discover_models": discover_models,
    "check_api_key_health": check_api_key_health,
    "get_rate_limit_status": get_rate_limit_status,
    "fleet_list_workers": fleet_list_workers,
    "fleet_distribute_task": fleet_distribute_task,
    "fleet_fanout": fleet_fanout,
    "fleet_map_goal": fleet_map_goal,
}
