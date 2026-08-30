"""
Universal AI provider registry — any OpenAI-compatible API key works.

Presets for popular free/cheap endpoints + fully custom base_url.
Model discovery via GET /v1/models (or provider-specific).

Free-tier rate budgets
----------------------
Every preset carries ``default_rpm`` / ``default_tpm``, which seed a key's
per-minute request/token budget when it is registered without explicit
``--rpm`` / ``--tpm``. The numbers below track each provider's *free* tier as
published in its own docs (verified 2026-08), and are deliberately set to the
**lowest limit a free key is likely to hit** rather than the best case:

* A budget is per API key, but a provider's quota is usually per *organisation*
  and per *model*. Groq, for example, allows 30 RPM everywhere but ranges from
  6K TPM (``llama-3.1-8b-instant``) to 30K TPM (``llama-4-scout``) — so the
  preset takes the 6K floor, which is safe whichever model a key ends up using.
* Overshooting costs real throughput: a 429 burns the request, trips the
  key's failure counter and forces a retry on another key. Undershooting only
  makes the router rotate to another key a little earlier.

Providers that publish no per-minute ceiling (DeepSeek, HuggingFace, local
runtimes) intentionally leave these unset, which means "unmetered" to
``MultiKeyManager`` — inventing a number there would throttle for no reason.

Anything here is only a default. Precedence is:
``explicit --rpm/--tpm`` > ``limits reported in provider response headers`` >
``these presets``.
"""
from __future__ import annotations

from typing import Any

# Canonical provider presets. base_url is OpenAI-compatible chat completions root
# (we append /chat/completions and /models as needed).
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "gpt-4o-mini",
        "supports_tools": True,
        "env_key": "OPENAI_API_KEY",
        "notes": "Official OpenAI API — free tier ~3 RPM / 40K TPM; paid tiers far higher",
        # Free tier is 3 RPM / 40K TPM. Paid Tier 1 jumps to 500 RPM, and the
        # real limit arrives in x-ratelimit-* headers on the first call, which
        # overwrites this. Seeding the free number keeps an unpaid key from
        # spraying 429s before that first response lands.
        "default_rpm": 3,
        "default_tpm": 40000,
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "openai/gpt-oss-20b",
        "supports_tools": True,
        # Groq's OpenAI-compatible API rejects requests containing more than
        # 128 function definitions.  Hermus can register more than that, so
        # callers must trim the advertised set before sending a request.
        "max_tools": 128,
        "env_key": "GROQ_API_KEY",
        # Groq documents that x-ratelimit-limit-requests "always refers to
        # Requests Per Day", unlike OpenAI where the same header is per
        # minute. Without this flag we'd adopt 14400 as an RPM budget and
        # effectively switch request throttling off for Groq keys.
        "requests_header_window": "day",
        "notes": "Free tier 30 RPM / 6K TPM (per-model TPM ranges 6K–70K) — very fast",
        # Groq publishes 30 RPM on nearly every free model (60 on a few small
        # ones). TPM is the binding limit and varies per model: 6K on
        # llama-3.1-8b-instant and qwen3-32b, 8K on gpt-oss-*, 30K on
        # llama-4-scout. 6K is the floor, so it holds for any model choice.
        "default_rpm": 30,
        "default_tpm": 6000,
    },
    "nvidia": {
        "name": "NVIDIA NIM (free endpoints)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "supports_tools": True,
        "max_tools": 128,
        "env_key": "NVIDIA_API_KEY",
        "notes": "NVIDIA API Catalog free endpoints ~40 RPM; Hermus exposes only hosted Free Endpoint chat models",
        # NVIDIA staff cite ~40 RPM per model on the free build.nvidia.com
        # tier (raisable to 200 on request). No TPM is published, so none is
        # set — the request cap is what actually binds here.
        "default_rpm": 40,
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "openrouter/auto",
        "supports_tools": True,
        "env_key": "OPENROUTER_API_KEY",
        "extra_headers": {
            "HTTP-Referer": "https://github.com/trmv2007-bot/hermus-agent-free",
            "X-Title": "Hermus Agent Free",
        },
        "notes": "Hundreds of models; ':free' variants capped at 20 RPM (50/day, 1000/day after $10 topped up)",
        # OpenRouter caps :free model variants at 20 RPM. The tighter limit is
        # the daily one (50 RPD, or 1000 RPD once $10 has ever been purchased),
        # which is not a per-minute budget and so cannot be modelled here.
        # Paid models have no platform-level RPM cap.
        "default_rpm": 20,
    },
    "together": {
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "supports_tools": True,
        "env_key": "TOGETHER_API_KEY",
        "notes": "Open models; entry tier ~60 RPM, limits are dynamic and reported in response headers",
        # Together moved to dynamic per-model limits that scale with sustained
        # traffic and are returned in response headers, so there is no
        # published free number. 60 RPM matches the commonly observed entry
        # tier and is replaced by the header value after the first call.
        "default_rpm": 60,
    },
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "supports_tools": True,
        "env_key": "FIREWORKS_API_KEY",
        "notes": "Free/no-card accounts throttled to ~10 RPM; adding a payment method lifts it to thousands",
        # Fireworks hard-caps accounts without a payment method at ~10 RPM.
        # Its paid limits are adaptive TPM rather than RPM, so only the free
        # request cap is seeded here.
        "default_rpm": 10,
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "deepseek-chat",
        "supports_tools": True,
        "env_key": "DEEPSEEK_API_KEY",
        "notes": "No published RPM/TPM cap — DeepSeek throttles by concurrency and slows under load instead of 429ing",
        # Deliberately no default_rpm/default_tpm: DeepSeek documents that it
        # "does NOT constrain user's rate limit" and instead queues requests
        # under load. A made-up ceiling would throttle a key that the provider
        # is happy to serve.
    },
    "mistral": {
        "name": "Mistral / Devstral",
        "base_url": "https://api.mistral.ai/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "devstral-latest",
        "supports_tools": True,
        "env_key": "MISTRAL_API_KEY",
        "notes": "Free 'Experiment' tier ~1 req/sec (60 RPM) / 500K TPM, ~1B tokens/month",
        # Mistral's free Experiment plan is throttled to roughly 1 request per
        # second with a 500K TPM ceiling. Exact numbers are now per-workspace
        # in the console, so treat this as the documented baseline.
        "default_rpm": 60,
        "default_tpm": 500000,
    },
    "codestral": {
        "name": "Codestral / Devstral",
        "base_url": "https://codestral.mistral.ai/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "devstral-latest",
        "supports_tools": True,
        "env_key": "CODESTRAL_API_KEY",
        "notes": "Free Codestral endpoint: 30 RPM / 2000 requests per day",
        # The codestral.mistral.ai endpoint has its own quota, distinct from
        # api.mistral.ai: 30 RPM and 2000 RPD. No TPM is published.
        "default_rpm": 30,
    },
    "gemini": {
        "name": "Google Gemini (OpenAI-compatible)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "gemini-2.0-flash",
        "supports_tools": True,
        "env_key": "GEMINI_API_KEY",
        "notes": "Free tier 10 RPM / 250K TPM on Flash models (Pro is far tighter) — use a Google AI Studio key",
        # Google's free tier is RPM-bound, not token-bound: Flash sits at
        # 10-15 RPM with a roomy 250K TPM, Flash-Lite at 15-30 RPM, and Pro at
        # ~5 RPM. 10 RPM covers the default gemini-2.0-flash and stays safe if
        # a key is pointed at a Pro-class model. The real cap for heavy use is
        # the daily RPD quota, which is outside this per-minute budget.
        "default_rpm": 10,
        "default_tpm": 250000,
    },
    "cerebras": {
        "name": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "llama3.1-8b",
        "supports_tools": True,
        "env_key": "CEREBRAS_API_KEY",
        "notes": "Free trial tier 5 RPM / 30K TPM (1M tokens/day) — very fast, but credit-bounded",
        # Cerebras tightened its free tier: the current docs list 5 RPM and
        # 30K TPM per model (1M TPD), down from the 30 RPM / 60K TPM that this
        # preset used to assume. It is now a $5/30-day credit trial rather
        # than a standing free tier; the Developer tier is 1K RPM / 1M TPM.
        "default_rpm": 5,
        "default_tpm": 30000,
    },
    "sambanova": {
        "name": "SambaNova",
        "base_url": "https://api.sambanova.ai/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "Meta-Llama-3.1-8B-Instruct",
        "supports_tools": True,
        "env_key": "SAMBANOVA_API_KEY",
        "notes": "Free tier 20 RPM / 200K tokens per day per model (10 RPM on 405B-class models)",
        # SambaNova's free tier (no payment method linked) is 20 RPM with a
        # 200K tokens/day per-model cap; the largest models drop to ~10 RPM.
        # The daily token cap is not a per-minute budget, so only RPM is set.
        "default_rpm": 20,
    },
    "huggingface": {
        "name": "HuggingFace Router (OpenAI-compatible)",
        "base_url": "https://router.huggingface.co/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "supports_tools": False,
        "env_key": "HF_TOKEN",
        "alias": ["hf"],
        "notes": "Routed to third-party providers; free accounts get a small monthly credit (<$0.10) rather than an RPM quota",
        # No default_rpm/default_tpm: HuggingFace bills Inference Providers by
        # credit, and its per-tier rate limits are undocumented. The binding
        # constraint is the monthly credit balance, not requests per minute.
    },
    "hf": {
        "name": "HuggingFace (alias)",
        "base_url": "https://router.huggingface.co/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "supports_tools": False,
        "env_key": "HF_TOKEN",
        "notes": "Alias of 'huggingface' — credit-metered, no documented RPM quota",
    },
    "github": {
        "name": "GitHub Models",
        "base_url": "https://models.inference.ai.azure.com",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "gpt-4o-mini",
        "supports_tools": True,
        "env_key": "GITHUB_TOKEN",
        "notes": "RETIRED — GitHub Models shut down 2026-07-30 (inference API no longer served); use Azure AI Foundry instead",
        # Kept as a preset so existing configs still resolve rather than
        # erroring, but the endpoint is gone as of 2026-07-30. The old free
        # tier was 10-15 RPM / 50-150 RPD depending on model class; no budget
        # is seeded because no request will succeed.
        "retired": True,
    },
    "azure": {
        "name": "Azure OpenAI",
        "base_url": "",  # must be set per deployment
        "auth_header": "api-key",
        "auth_prefix": "",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "gpt-4o-mini",
        "supports_tools": True,
        "env_key": "AZURE_OPENAI_API_KEY",
        "notes": "Set base_url to your Azure resource endpoint + /openai/deployments/{deployment}",
    },
    "ollama": {
        "name": "Ollama (local OpenAI-compatible)",
        "base_url": "http://localhost:11434/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "llama3.1:8b",
        "supports_tools": True,
        "env_key": None,
        "notes": "No API key needed — local, no rate limit (bounded by your hardware)",
        "no_auth": True,
        # Local runtimes are intentionally unmetered: the only ceiling is the
        # machine itself, and a synthetic RPM cap would just idle the GPU.
    },
    "nollama": {
        "name": "NoLlama (Intel NPU / Arc iGPU via OpenVINO)",
        # 8010 rather than NoLlama's 8000 default: the Hermus gateway owns 8000.
        "base_url": "http://localhost:8010/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "MiniCPM5-1B-int4-g128-ov",
        # Tool calling works on GPU/CPU; the NPU has a hard prompt cap and
        # cannot do it (core.accelerators strips tools for NPU roles).
        "supports_tools": True,
        "env_key": None,
        "notes": "Local OpenVINO server for Intel NPU/Arc — no API key needed",
        "no_auth": True,
    },
    "lmstudio": {
        "name": "LM Studio",
        "base_url": "http://localhost:1234/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "local-model",
        "supports_tools": True,
        "no_auth": True,
    },
    "vllm": {
        "name": "vLLM / any OpenAI-compatible",
        "base_url": "http://localhost:8000/v1",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "default",
        "supports_tools": True,
        "notes": "Point base_url at any OpenAI-compatible server",
    },
    "custom": {
        "name": "Custom OpenAI-compatible",
        "base_url": "",  # required at add time
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "default_model": "default",
        "supports_tools": True,
        "notes": "Any provider with /v1/chat/completions — set base_url",
    },
    "anthropic": {
        "name": "Anthropic (via OpenAI-compat proxies)",
        "base_url": "https://api.anthropic.com/v1",
        "auth_header": "x-api-key",
        "auth_prefix": "",
        "models_path": "/models",
        "chat_path": "/messages",  # native differs — prefer proxy base_url
        "default_model": "claude-3-5-haiku-latest",
        "supports_tools": True,
        "env_key": "ANTHROPIC_API_KEY",
        "native_anthropic": True,
        "notes": "No free tier — entry (Tier 1) is 50 RPM / 30K input TPM. Prefer an OpenAI-compatible proxy, or use native messages API",
        # Anthropic has no standing free tier; Tier 1 (after $5 paid) is
        # 50 RPM with 30K input TPM on Sonnet-class models. Seeding Tier 1
        # keeps a fresh key from burning through 429s on its first burst.
        "default_rpm": 50,
        "default_tpm": 30000,
    },
}


def list_providers() -> list[dict[str, Any]]:
    out = []
    for pid, p in PROVIDER_PRESETS.items():
        out.append(
            {
                "id": pid,
                "name": p.get("name"),
                "base_url": p.get("base_url"),
                "default_model": p.get("default_model"),
                "notes": p.get("notes", ""),
                "env_key": p.get("env_key"),
                "no_auth": p.get("no_auth", False),
                # Surfaced so the dashboard/CLI can show the budget a key will
                # get before it is added. None means "unmetered by default".
                "default_rpm": p.get("default_rpm"),
                "default_tpm": p.get("default_tpm"),
                "retired": p.get("retired", False),
            }
        )
    return out


def requests_header_window(provider_id: str) -> str:
    """
    What ``x-ratelimit-limit-requests`` means for this provider: ``"minute"``
    (OpenAI's convention, and the default) or ``"day"``.

    Groq reuses the same header name for a *daily* quota, so callers must not
    treat its value as an RPM budget.
    """
    return get_provider(provider_id).get("requests_header_window") or "minute"


def get_provider(provider_id: str) -> dict[str, Any]:
    pid = (provider_id or "custom").lower().strip()
    # aliases
    if pid in ("huggingface",):
        pid = "hf"
    if pid not in PROVIDER_PRESETS:
        # Unknown → treat as custom openai-compatible name
        base = dict(PROVIDER_PRESETS["custom"])
        base["id"] = pid
        base["name"] = provider_id
        return base
    base = dict(PROVIDER_PRESETS[pid])
    base["id"] = pid
    return base


def resolve_endpoint(
    provider: str,
    base_url: str = None,
    path_key: str = "chat_path",
) -> str:
    preset = get_provider(provider)
    root = (base_url or preset.get("base_url") or "").rstrip("/")
    path = preset.get(path_key) or "/chat/completions"
    if not path.startswith("/"):
        path = "/" + path
    if not root:
        raise ValueError(
            f"Provider '{provider}' needs a base_url. "
            f"Example: hermus multikey add --provider custom --base-url https://api.example.com/v1 --key sk-..."
        )
    # Don't double-append the path when the user pasted the full endpoint
    # (e.g. base_url = https://api.example.com/v1/chat/completions).
    if root.endswith(path):
        return root
    return root + path


def build_auth_headers(
    provider: str,
    api_key: str = None,
    extra: dict[str, str] = None,
) -> dict[str, str]:
    preset = get_provider(provider)
    headers = {"Content-Type": "application/json"}
    extra_h = preset.get("extra_headers") or {}
    headers.update(extra_h)
    if extra:
        headers.update(extra)
    if preset.get("no_auth") and not api_key:
        return headers
    if not api_key:
        return headers
    hdr = preset.get("auth_header") or "Authorization"
    prefix = preset.get("auth_prefix")
    if prefix is None:
        prefix = "Bearer "
    if hdr.lower() == "authorization":
        headers[hdr] = f"{prefix}{api_key}" if prefix else api_key
    else:
        headers[hdr] = f"{prefix}{api_key}" if prefix else api_key
    # Gemini sometimes wants key as query — also set header
    if provider.lower() == "gemini" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def parse_model_ref(model_str: str) -> tuple:
    """
    Parse 'provider/model' or 'provider/org/model' or bare model.
    Returns (provider, model_name).
    """
    if not model_str:
        return "ollama", "llama3.1:8b"
    s = model_str.strip()
    # openai-compatible custom: openai-compat@https://host/v1/model-name
    if s.startswith("openai-compat@") or s.startswith("compat@"):
        rest = s.split("@", 1)[1]
        # base|model or just use custom
        if "|" in rest:
            base, model = rest.split("|", 1)
            return "custom", model
        return "custom", rest
    if "/" not in s:
        return "ollama", s
    provider, name = s.split("/", 1)
    provider = provider.lower()
    # Known multi-segment model ids that include provider-like prefixes
    # e.g. openrouter/meta-llama/... — provider is first segment only if known
    if provider in PROVIDER_PRESETS or provider in ("hf", "huggingface", "mock"):
        return provider, name
    # Unknown first segment → treat it as a custom OpenAI-compatible provider
    # (it may have keys added via `hermus multikey add --provider <name>`),
    # falling back to the shared "custom" key pool when no key exists.
    return provider, name
