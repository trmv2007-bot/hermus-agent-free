"""
Universal AI provider registry — any OpenAI-compatible API key works.

Presets for popular free/cheap endpoints + fully custom base_url.
Model discovery via GET /v1/models (or provider-specific).
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
        "notes": "Official OpenAI API",
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
        "notes": "Free tier ~30 RPM — very fast",
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
        "notes": "NVIDIA API Catalog; Hermus exposes only hosted Free Endpoint chat models",
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
        "notes": "Hundreds of models, free tier models available",
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
        "notes": "Open models, free credits often",
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
        "notes": "Free tier available — use Google AI Studio key",
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
        "notes": "Very fast free tier",
        "default_rpm": 30,
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
        "notes": "Free GitHub Models with PAT",
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
        "notes": "No API key needed — local",
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
        "notes": "Prefer an OpenAI-compatible proxy, or use native messages API",
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
            }
        )
    return out


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
