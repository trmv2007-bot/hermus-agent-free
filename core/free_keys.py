"""
Free Key & Model Auto-Provisioner — expands model fleet with free, community, and local tiers.
Enables Hermus to autonomously acquire, test, and register free model endpoints.
"""
from __future__ import annotations

import logging
from typing import Any
import httpx

from .multi_key import multi_key_manager
from .providers import get_provider

logger = logging.getLogger("hermus.free_keys")

FREE_MODEL_CATALOG = [
    {
        "provider": "openrouter",
        "name": "OpenRouter Free Fleet",
        "models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "google/gemini-2.0-flash-exp:free",
            "qwen/qwen-2.5-coder-32b-instruct:free",
            "meta-llama/llama-3.2-11b-vision-instruct:free",
        ],
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
        "base_url": "https://openrouter.ai/api/v1",
        "notes": "OpenRouter 100% Free Community Pool (No credits required)",
    },
    {
        "provider": "mistral",
        "name": "Mistral Devstral Free Tier",
        "models": ["devstral-latest", "codestral-latest", "mistral-small-latest"],
        "default_model": "devstral-latest",
        "base_url": "https://api.mistral.ai/v1",
        "notes": "Free developer tier for Devstral & Codestral",
    },
    {
        "provider": "groq",
        "name": "Groq LPU Free Tier",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "default_model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "notes": "30 RPM free ultra-fast LPU inference",
    },
    {
        "provider": "ollama",
        "name": "Local Ollama Node",
        "models": ["llama3.2", "qwen2.5-coder", "mistral", "deepseek-r1"],
        "default_model": "llama3.2",
        "base_url": "http://localhost:11434/v1",
        "notes": "100% Offline Local Inference",
    },
]


def discover_and_provision_free_models(auto_register: bool = True) -> dict[str, Any]:
    """
    Scans for available free models, local Ollama engines, and registers
    open community endpoints into the Multi-Key Fleet.
    """
    discovered = []
    registered = []

    # 1. Check local Ollama
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=1.5)
        if resp.status_code == 200:
            models_data = resp.json().get("models", [])
            ollama_models = [m.get("name") for m in models_data if m.get("name")]
            if ollama_models:
                discovered.append({
                    "provider": "ollama",
                    "status": "online",
                    "models": ollama_models,
                    "name": "Local Ollama Instance"
                })
                if auto_register:
                    res = multi_key_manager.add_key("ollama", "ollama-local", name="Local Ollama Node", base_url="http://localhost:11434/v1")
                    if res.get("success"):
                        registered.append("ollama")
    except Exception:
        # Ollama not running locally
        pass

    # 2. Add OpenRouter Free Community Tier Preset
    discovered.append({
        "provider": "openrouter",
        "status": "available",
        "models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "google/gemini-2.0-flash-exp:free",
            "qwen/qwen-2.5-coder-32b-instruct:free"
        ],
        "name": "OpenRouter Free Tier"
    })

    # 3. Add Devstral & Groq presets
    discovered.append({
        "provider": "mistral",
        "status": "free_tier_available",
        "models": ["devstral-latest", "codestral-latest", "mistral-small-latest"],
        "name": "Mistral Devstral Free Developer Tier"
    })
    discovered.append({
        "provider": "groq",
        "status": "free_tier_available",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "name": "Groq Fast Tier"
    })

    return {
        "success": True,
        "discovered_pools": discovered,
        "registered_count": len(registered),
        "total_catalog_models": sum(len(c["models"]) for c in FREE_MODEL_CATALOG),
        "available_free_tiers": FREE_MODEL_CATALOG,
    }
