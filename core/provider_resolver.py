"""
Unified provider/credential discovery.

The rest of Hermus must not carry independent ideas of "what providers exist":

* ``core.providers.PROVIDER_PRESETS`` knows *known* providers.
* ``.env`` variables hold *configured* credentials.
* ``data/api_keys.json`` holds *stored* credentials.
* the model fleet and the router only used stored entries (+ Ollama), so a
  provider configured only in ``.env`` was invisible to fallback/routing.

This module is the single place that answers:

    Which providers are configured, where their credentials come from, and
    which ones are actually safe to use for (optionally tool-calling) work?

All runtime consumers (FreeLLM fallback, MultiKeyManager.first_available_bundle,
model fleet, router, capability auto-selection) should read from here rather
than re-implementing credential discovery.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from .config import config
from .providers import PROVIDER_PRESETS, get_provider

#: providers that can be used without a credential (local runtimes).
_LOCAL_NO_AUTH = {"ollama", "lmstudio", "nollama"}

#: local/credential-less provider ids that should NOT be picked by the hosted
#: fallback path. ``first_available_bundle`` is meant to find an API-backed
#: provider when the requested/default local provider is unavailable.
_FALLBACK_EXCLUDE_LOCAL = {"ollama", "lmstudio", "nollama"}

#: canonical ordering used when nothing else differentiates two providers.
_PROVIDER_ORDER = (
    "openai", "groq", "openrouter", "gemini", "nvidia", "together", "fireworks",
    "deepseek", "mistral", "codestral", "cerebras", "sambanova", "anthropic",
    "azure", "huggingface", "github", "custom", "ollama", "lmstudio", "nollama",
    "vllm",
)


def _no_auth(provider: str) -> bool:
    try:
        preset = get_provider(provider)
    except Exception:
        preset = {}
    return bool(preset.get("no_auth") or provider in _LOCAL_NO_AUTH)


def _is_retired(provider: str) -> bool:
    try:
        return bool(get_provider(provider).get("retired"))
    except Exception:
        return False


def _supports_tools(provider: str) -> bool:
    try:
        return get_provider(provider).get("supports_tools") is not False
    except Exception:
        return True


def _env_key(provider: str) -> Optional[str]:
    try:
        return get_provider(provider).get("env_key")
    except Exception:
        return None


def _base_url(provider: str) -> str:
    try:
        return get_provider(provider).get("base_url") or ""
    except Exception:
        return ""


def _default_model(provider: str) -> Optional[str]:
    try:
        return get_provider(provider).get("default_model")
    except Exception:
        return None


def _max_tools(provider: str) -> Optional[int]:
    try:
        return get_provider(provider).get("max_tools")
    except Exception:
        return None


def _visible_provider_ids() -> list[str]:
    """Known provider ids, skipping duplicate aliases (e.g. ``hf``)."""
    ids = []
    for pid in PROVIDER_PRESETS:
        # ``hf`` is a legacy alias of ``huggingface``; the resolver reports one.
        if pid == "hf" and "huggingface" in PROVIDER_PRESETS:
            continue
        ids.append(pid)
    return ids


def _env_credential(provider: str) -> str:
    """Credential from ``.env`` / process env for a provider preset."""
    key_name = _env_key(provider)
    if not key_name:
        return ""
    value = os.getenv(key_name)
    if value:
        return value.strip()
    # A few providers were historically exposed as Config fields too.
    mapping = {
        "groq": getattr(config, "groq_api_key", None),
        "hf": getattr(config, "hf_token", None),
        "huggingface": getattr(config, "hf_token", None),
        "openai": getattr(config, "openai_api_key", None),
        "openrouter": getattr(config, "openrouter_api_key", None),
        "gemini": getattr(config, "gemini_api_key", None),
        "anthropic": getattr(config, "anthropic_api_key", None),
    }
    v = mapping.get(provider)
    return (v or "").strip() if v else ""


def discover_runtime_bundles(include_local: bool = True) -> list[dict[str, Any]]:
    """
    Return every bundled credential set that can be used for a model call.

    Sources, in priority order:

    1. ``data/api_keys.json`` via :class:`MultiKeyManager` (stored, health,
       discovered models and custom endpoints);
    2. ``.env`` / process environment for provider presets (the same keys the
       README and ``.env.example`` already document).

    Returns bundles shaped like ``MultiKeyManager.get_key_bundle`` output plus
    ``source``, ``supports_tools``, ``retired`` and ``models``.
    """
    from .multi_key import multi_key_manager  # local import avoids cycles

    bundles: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    # ---- stored credentials ----------------------------------------------
    try:
        entries = multi_key_manager.get_all_entries()
    except Exception:
        entries = []

    for e in entries:
        provider = (e.get("provider") or "custom").lower()
        preset = get_provider(provider)
        key = e.get("key") or ""
        base = e.get("base_url") or preset.get("base_url") or ""
        if not key and not _no_auth(provider):
            continue
        if not base:
            continue
        default = e.get("default_model") or preset.get("default_model")
        sig = (provider, key, base)
        if sig in seen:
            continue
        seen.add(sig)
        bundles.append(
            {
                "provider": provider,
                "key": key,
                "base_url": base,
                "default_model": default,
                "name": e.get("name") or f"{provider}_key",
                "source": "stored",
                "supports_tools": preset.get("supports_tools") is not False,
                "max_tools": preset.get("max_tools"),
                "retired": bool(preset.get("retired")),
                "models": e.get("models") or [],
                "rpm_limit": e.get("rpm_limit") or preset.get("default_rpm"),
                "tpm_limit": e.get("tpm_limit") or preset.get("default_tpm"),
                "healthy": e.get("healthy"),
                "health_status": e.get("health_status"),
            }
        )

    # ---- credentials from .env / environment -----------------------------
    for provider, preset in PROVIDER_PRESETS.items():
        if provider == "hf" and "huggingface" in PROVIDER_PRESETS:
            continue
        key = _env_credential(provider)
        no_auth = _no_auth(provider)
        if not key and not no_auth:
            continue
        base = preset.get("base_url") or ""
        if not base:
            continue
        sig = (provider, key, base)
        if sig in seen:
            continue
        seen.add(sig)
        bundles.append(
            {
                "provider": provider,
                "key": key,
                "base_url": base,
                "default_model": preset.get("default_model"),
                "name": f"{provider}_env" if key else f"{provider}_local",
                "source": "env" if key else "local",
                "supports_tools": preset.get("supports_tools") is not False,
                "max_tools": preset.get("max_tools"),
                "retired": bool(preset.get("retired")),
                "models": [],
                "rpm_limit": preset.get("default_rpm"),
                "tpm_limit": preset.get("default_tpm"),
                "healthy": None,
                "health_status": None,
            }
        )

    if include_local:
        return bundles
    return [b for b in bundles if b.get("source") != "local"]


def select_usable_bundle(
    require_tools: bool = False,
    *,
    prefer: Optional[Iterable[str]] = None,
    exclude_local: bool = True,
) -> Optional[dict[str, Any]]:
    """
    Pick the best currently available credential bundle.

    Prefers non-retired, non-local API bundles, then tool-capable ones (when
    ``require_tools``), then a useful default model. This is the single
    fallback decision used by the LLM layer.
    """
    bundles = discover_runtime_bundles()
    if exclude_local:
        bundles = [
            b
            for b in bundles
            if b.get("provider") not in _FALLBACK_EXCLUDE_LOCAL
            and not (b.get("key") == "" and b.get("source") == "local")
        ]
    if not bundles:
        return None

    prefer_set = {str(p).lower() for p in (prefer or [])}
    rankable = []
    for b in bundles:
        provider = b.get("provider") or "custom"
        if _is_retired(provider):
            continue
        tools_ok = b.get("supports_tools") is not False
        if require_tools and not tools_ok:
            continue
        model = b.get("default_model") or _default_model(provider)
        if not model:
            continue
        rankable.append(b)
    if not rankable:
        return None

    order = {pid: i for i, pid in enumerate(_PROVIDER_ORDER)}
    prefer_rank = {p: i for i, p in enumerate(prefer_set)}

    def rank(b: dict[str, Any]) -> tuple[Any, ...]:
        provider = (b.get("provider") or "custom").lower()
        pref = prefer_rank.get(provider, len(prefer_rank) + 1)
        order_rank = order.get(provider, len(order) + 1)
        # Health (if known) first, then ties keep configured preference order.
        health_rank = 0 if b.get("healthy") is not False else 1
        return (health_rank, pref, order_rank)

    rankable.sort(key=rank)
    return rankable[0]


def list_available_providers(probe: bool = False) -> list[dict[str, Any]]:
    """
    One authoritative provider report used by the agent, router, fleet and CLI.

    Every known preset is present so callers can distinguish "known" from
    "configured" from "usable". ``probe=True`` performs a best-effort health
    ping for configured endpoint bundles (network calls are avoided by default).
    """
    bundles = discover_runtime_bundles()
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for b in bundles:
        by_provider.setdefault(b.get("provider") or "custom", []).append(b)

    out: list[dict[str, Any]] = []
    for provider in _visible_provider_ids():
        provider_bundles = by_provider.get(provider, [])
        preset = get_provider(provider)
        env_key = _env_key(provider)
        env_cred = _env_credential(provider)
        no_auth = _no_auth(provider)
        source: Optional[str] = None
        if provider_bundles:
            source = provider_bundles[0].get("source") or "stored"
        elif env_cred:
            source = "env"
        elif no_auth:
            source = "local"

        configured = bool(provider_bundles or env_cred or no_auth)
        models = sorted({
            m for b in provider_bundles for m in (b.get("models") or [])
        }) or []
        # Cache a reachability verdict from the last health check; ``None``
        # means "not known yet" rather than "offline".
        healthy_flags = [b.get("healthy") for b in provider_bundles if b.get("healthy") is not None]
        reachable = True if healthy_flags and all(healthy_flags) else (
            False if healthy_flags and any(f is False for f in healthy_flags) else None
        )
        status = next((b.get("health_status") for b in provider_bundles if b.get("health_status")), None)
        default_model = next((b.get("default_model") for b in provider_bundles if b.get("default_model")), _default_model(provider))
        reason = []
        if not configured:
            reason.append("not configured")
        elif not env_cred and provider_bundles and all(
            b.get("key") == "" and b.get("source") == "local" for b in provider_bundles
        ):
            reason.append("local runtime configured (no credential needed)")
        elif env_cred:
            reason.append("credential from environment")
        if _is_retired(provider):
            reason.append("provider retired / endpoint no longer served")
        if not models and not default_model:
            reason.append("no default model known")

        out.append(
            {
                "provider": provider,
                "name": preset.get("name"),
                "configured": configured,
                "credential_source": source,
                "has_credentials": bool(env_cred or any(b.get("key") for b in provider_bundles)),
                "base_url": preset.get("base_url") or "",
                "env_key": env_key,
                "default_model": default_model,
                "models": models,
                "models_available": bool(models or default_model),
                "supports_tools": preset.get("supports_tools") is not False,
                "max_tools": preset.get("max_tools"),
                "retired": bool(preset.get("retired")),
                "reachable": reachable,
                "health_status": status,
                "reason": "; ".join(reason),
            }
        )

    # Stored providers not present in the known preset map (custom-* and
    # user-defined provider names) still count as configured.
    for provider, provider_bundles in by_provider.items():
        if provider in _visible_provider_ids():
            continue
        preset = get_provider(provider)
        env_cred = _env_credential(provider)
        source = provider_bundles[0].get("source") or "stored"
        models = sorted({m for b in provider_bundles for m in (b.get("models") or [])})
        healthy_flags = [b.get("healthy") for b in provider_bundles if b.get("healthy") is not None]
        reachable = True if healthy_flags and all(healthy_flags) else (
            False if healthy_flags and any(f is False for f in healthy_flags) else None
        )
        out.append(
            {
                "provider": provider,
                "name": provider_bundles[0].get("name") or preset.get("name") or provider,
                "configured": True,
                "credential_source": source,
                "has_credentials": bool(env_cred or any(b.get("key") for b in provider_bundles)),
                "base_url": provider_bundles[0].get("base_url") or "",
                "env_key": preset.get("env_key"),
                "default_model": provider_bundles[0].get("default_model") or preset.get("default_model"),
                "models": models,
                "models_available": bool(models or provider_bundles[0].get("default_model")),
                "supports_tools": preset.get("supports_tools") is not False,
                "max_tools": preset.get("max_tools"),
                "retired": False,
                "reachable": reachable,
                "health_status": next((b.get("health_status") for b in provider_bundles if b.get("health_status")), None),
                "reason": "configured via stored custom provider",
            }
        )

    out.sort(key=lambda r: _PROVIDER_ORDER.index(r["provider"]) if r["provider"] in _PROVIDER_ORDER else 99)
    return out


def diagnose(
    require_tools: bool = False,
    *,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """
    Human-readable provider diagnosis used for better error messages.

    Returns configured provider facts, the recommended bundle/model and any
    detected issues. Never raises; a bare ``.env``-only setup is diagnosed
    correctly even when the multikey store is empty.
    """
    try:
        providers = list_available_providers()
    except Exception as exc:
        return {
            "ok": False,
            "error": f"provider discovery failed: {exc}",
            "providers": [],
            "usable_providers": [],
            "recommended_provider": None,
            "recommended_model": None,
            "tools_capable": [],
        }

    usable = [
        p
        for p in providers
        if p.get("configured")
        and not p.get("retired")
        and p.get("models_available")
        and (p.get("supports_tools") or not require_tools)
    ]
    bundle = None
    try:
        bundle = select_usable_bundle(require_tools=require_tools)
    except Exception:
        bundle = None
    recommended = bundle or {}
    recommended_model = (
        recommended.get("default_model")
        or _default_model(recommended.get("provider") or "")
        or None
    )
    if recommended_model and recommended.get("provider"):
        recommended_model = f"{recommended['provider']}/{recommended_model}"
    elif model:
        recommended_model = model

    return {
        "ok": bool(bundle),
        "configured": [p for p in providers if p.get("configured")],
        "providers": providers,
        "usable_providers": usable,
        "tools_capable": [
            p["provider"]
            for p in providers
            if p.get("configured") and p.get("supports_tools") and not p.get("retired")
        ],
        "recommended_provider": (recommended or {}).get("provider"),
        "recommended_model": recommended_model,
    }
