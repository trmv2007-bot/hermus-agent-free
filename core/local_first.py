"""
Local-First Provider System for HERMUS

PRIORITY ORDER:
1. Local models (Ollama, NoLlama, LM Studio) - NO API KEYS NEEDED
2. Free API services (only if user provides keys) - OPTIONAL
3. Paid services (disabled by default) - USER MUST ENABLE

This ensures HERMUS works 100% offline with zero configuration.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

from core.log import get_logger

logger = get_logger(__name__)


# Local providers that require NO API keys
LOCAL_PROVIDERS = {
    "ollama": {
        "name": "Ollama",
        "base_url": "http://localhost:11434/v1",
        "no_auth": True,
        "default_model": "llama3.2:3b",
        "recommended_models": [
            "llama3.2:3b",
            "phi3:3.8b",
            "mistral:7b",
            "llama3.2:11b",
        ],
        "rtx3050_optimized": "mistral:7b",
        "description": "Primary local model provider",
        "install_command": "curl -fsSL https://ollama.com/install.sh | sh",
        "start_command": "ollama serve",
        "pull_command": "ollama pull {model}",
    },
    "nollama": {
        "name": "NoLlama",
        "base_url": "http://localhost:8010/v1",
        "no_auth": True,
        "default_model": "MiniCPM5-1B-int4-g128-ov",
        "recommended_models": ["MiniCPM5-1B-int4-g128-ov"],
        "description": "Intel NPU/GPU support via OpenVINO",
        "install_command": "pip install nollama",
        "start_command": "nollama serve",
    },
    "lmstudio": {
        "name": "LM Studio",
        "base_url": "http://localhost:1234/v1",
        "no_auth": True,
        "default_model": "local-model",
        "description": "LM Studio local inference server",
        "start_command": "lmstudio server start",
    },
}

# Free providers that require API keys (OPTIONAL)
FREE_API_PROVIDERS = {
    "groq": {
        "name": "Groq",
        "free_tier": True,
        "default_model": "llama-3.1-8b-instant",
        "rate_limit": "30 RPM / 6K-30K TPM",
        "env_key": "GROQ_API_KEY",
        "description": "Very fast, free tier available",
        "priority": 1,
    },
    "openrouter": {
        "name": "OpenRouter",
        "free_tier": True,
        "default_model": "openrouter/auto",
        "rate_limit": "20 RPM (free models)",
        "env_key": "OPENROUTER_API_KEY",
        "description": "Access hundreds of models, free tier with :free models",
        "priority": 2,
    },
    "mistral": {
        "name": "Mistral",
        "free_tier": True,
        "default_model": "devstral-latest",
        "rate_limit": "60 RPM / 500K TPM",
        "env_key": "MISTRAL_API_KEY",
        "description": "Free Experiment tier",
        "priority": 3,
    },
    "codestral": {
        "name": "Codestral",
        "free_tier": True,
        "default_model": "devstral-latest",
        "rate_limit": "30 RPM / 2000 requests/day",
        "env_key": "CODESTRAL_API_KEY",
        "description": "Free coding-focused endpoint",
        "priority": 4,
    },
    "together": {
        "name": "Together AI",
        "free_tier": True,
        "default_model": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "rate_limit": "~60 RPM",
        "env_key": "TOGETHER_API_KEY",
        "description": "Free tier with dynamic limits",
        "priority": 5,
    },
    "fireworks": {
        "name": "Fireworks AI",
        "free_tier": True,
        "default_model": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "rate_limit": "~10 RPM (no card)",
        "env_key": "FIREWORKS_API_KEY",
        "description": "Free tier without payment method",
        "priority": 6,
    },
    "deepseek": {
        "name": "DeepSeek",
        "free_tier": True,
        "default_model": "deepseek-chat",
        "rate_limit": "No hard limit (queued under load)",
        "env_key": "DEEPSEEK_API_KEY",
        "description": "Free, queues requests under load",
        "priority": 7,
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "free_tier": True,
        "default_model": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "rate_limit": "~40 RPM",
        "env_key": "NVIDIA_API_KEY",
        "description": "NVIDIA free endpoints",
        "priority": 8,
    },
}

# RTX 3050 Optimization
RTX3050_CONFIG = {
    "vram_total": 8,
    "vram_safe_limit": 7,
    "recommended_model": "mistral:7b",
    "quantization": "4bit",
    "context_window": 4096,
    "streaming": True,
    "speculative_decoding": True,
}


class LocalFirstProvider:
    """
    Manages providers with Local-First priority.

    Order of preference:
    1. Local providers (Ollama, NoLlama, LM Studio) - Always available
    2. Free API providers (if user has configured keys)
    3. Paid providers (only if explicitly enabled by user)
    """

    def __init__(self):
        self._detected_local = {}
        self._available_free = {}
        self._rtx3050_optimized = False

    def detect_local_providers(self) -> dict[str, bool]:
        """Detect which local providers are available."""
        results = {}

        # Check each local provider
        for provider_id, config in LOCAL_PROVIDERS.items():
            results[provider_id] = self._check_provider_health(provider_id, config)

        self._detected_local = results
        return results

    def _check_provider_health(self, provider_id: str, config: dict) -> bool:
        """Check if a local provider is running and healthy."""
        try:
            import httpx

            client = httpx.Client(timeout=2.0)

            # Try to connect
            if provider_id == "ollama":
                # Check Ollama
                response = client.get("http://localhost:11434/api/tags", timeout=2.0)
                if response.status_code == 200:
                    logger.info(f"✅ {config['name']} detected at localhost:11434")
                    return True
                return False

            elif provider_id == "nollama":
                # Check NoLlama
                response = client.get("http://localhost:8010/v1/models", timeout=2.0)
                if response.status_code == 200:
                    logger.info(f"✅ {config['name']} detected at localhost:8010")
                    return True
                return False

            elif provider_id == "lmstudio":
                # Check LM Studio
                response = client.get("http://localhost:1234/v1/models", timeout=2.0)
                if response.status_code == 200:
                    logger.info(f"✅ {config['name']} detected at localhost:1234")
                    return True
                return False

            return False

        except Exception as e:
            logger.debug(f"{config['name']} not available: {e}")
            return False

    def detect_free_api_keys(self) -> dict[str, bool]:
        """Detect which free API providers have keys configured."""
        results = {}

        for provider_id, info in FREE_API_PROVIDERS.items():
            env_key = info.get("env_key")
            if env_key and os.environ.get(env_key):
                results[provider_id] = True
                logger.info(f"✅ {info['name']} API key detected ({env_key})")
            else:
                results[provider_id] = False

        self._available_free = results
        return results

    def detect_gpu(self) -> dict[str, Any]:
        """Detect GPU information."""
        gpu_info = {
            "detected": False,
            "name": None,
            "vram": 0,
            "is_rtx3050": False,
        }

        try:
            # Try GPUtil
            try:
                import GPUtil

                gpus = GPUtil.getGPUs()
                if gpus:
                    gpu = gpus[0]
                    gpu_info["detected"] = True
                    gpu_info["name"] = gpu.name
                    gpu_info["vram"] = gpu.memoryTotal
                    gpu_info["is_rtx3050"] = "RTX 3050" in gpu.name
            except ImportError:
                pass

            # Try pynvml
            try:
                from pynvml import nvmlInit, nvmlDeviceGetHandleByIndex, nvmlDeviceGetName, nvmlDeviceGetMemoryInfo

                nvmlInit()
                handle = nvmlDeviceGetHandleByIndex(0)
                gpu_info["detected"] = True
                gpu_info["name"] = nvmlDeviceGetName(handle).decode()
                info = nvmlDeviceGetMemoryInfo(handle)
                gpu_info["vram"] = info.total // (1024**3)  # Convert to GB
                gpu_info["is_rtx3050"] = "RTX 3050" in gpu_info["name"]
            except Exception:
                pass

            # Try torch
            try:
                import torch

                if torch.cuda.is_available():
                    gpu_info["detected"] = True
                    gpu_info["name"] = torch.cuda.get_device_name(0)
                    gpu_info["vram"] = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                    gpu_info["is_rtx3050"] = "RTX 3050" in gpu_info["name"]
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"GPU detection failed: {e}")

        return gpu_info

    async def auto_start_ollama(self) -> bool:
        """Automatically start Ollama if installed but not running."""
        # Check if Ollama is installed
        try:
            result = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                logger.info("🔄 Ollama is installed but not running. Starting it...")

                # Start Ollama in background
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                # Wait for it to start
                for _ in range(10):
                    await asyncio.sleep(1)
                    if self._check_provider_health("ollama", LOCAL_PROVIDERS["ollama"]):
                        logger.info("✅ Ollama started successfully")
                        return True

                logger.warning("⚠️ Ollama didn't start within 10 seconds. Run manually: ollama serve")
                return False
            else:
                logger.info("❌ Ollama not installed. Install: curl -fsSL https://ollama.com/install.sh | sh")
                return False

        except Exception as e:
            logger.debug(f"Ollama auto-start failed: {e}")
            return False

    def get_provider_priority_list(self) -> list[str]:
        """
        Get ordered list of providers based on Local-First principle.

        Returns:
            List of provider IDs in priority order
        """
        priority_list = []

        # 1. LOCAL PROVIDERS (always first, no API keys needed)
        for provider_id in ["ollama", "nollama", "lmstudio"]:
            if self._detected_local.get(provider_id, False):
                priority_list.append(provider_id)

        # If no local providers detected, add Ollama as fallback
        if not priority_list:
            priority_list.append("ollama")

        # 2. FREE API PROVIDERS (only if user has keys)
        for provider_id, info in sorted(FREE_API_PROVIDERS.items(), key=lambda x: x[1].get("priority", 99)):
            if self._available_free.get(provider_id, False):
                priority_list.append(provider_id)

        # 3. PAID PROVIDERS (only if explicitly enabled)
        if os.environ.get("HERMUS_ENABLE_PAID_PROVIDERS", "").lower() in ("1", "true", "yes"):
            # Add paid providers here if needed
            pass

        return priority_list

    def get_optimized_config(self, provider_id: str = None) -> dict[str, Any]:
        """
        Get optimized configuration for a provider.

        For RTX 3050, this includes VRAM-safe settings.
        """
        gpu_info = self.detect_gpu()
        is_rtx3050 = gpu_info.get("is_rtx3050", False)

        if provider_id in LOCAL_PROVIDERS:
            config = {
                "provider": provider_id,
                "base_url": LOCAL_PROVIDERS[provider_id]["base_url"],
                "no_auth": True,
            }

            # Add RTX 3050 optimizations
            if is_rtx3050:
                config.update(
                    {
                        "vram_limit": RTX3050_CONFIG["vram_safe_limit"],
                        "recommended_model": RTX3050_CONFIG["recommended_model"],
                        "quantization": RTX3050_CONFIG["quantization"],
                        "context_window": RTX3050_CONFIG["context_window"],
                        "streaming": RTX3050_CONFIG["streaming"],
                    }
                )

            # Add provider-specific settings
            config.update(LOCAL_PROVIDERS[provider_id])

            return config

        elif provider_id in FREE_API_PROVIDERS:
            return {
                "provider": provider_id,
                "base_url": FREE_API_PROVIDERS[provider_id].get("base_url", ""),
                "auth_header": "Authorization",
                "auth_prefix": "Bearer ",
                "env_key": FREE_API_PROVIDERS[provider_id].get("env_key"),
                "free_tier": True,
                "rate_limit": FREE_API_PROVIDERS[provider_id].get("rate_limit"),
            }

        else:
            # Default to Ollama
            return self.get_optimized_config("ollama")

    def get_recommended_setup(self) -> dict[str, Any]:
        """
        Get recommended setup for the current system.

        Returns:
            Dictionary with recommended configuration
        """
        gpu_info = self.detect_gpu()
        is_rtx3050 = gpu_info.get("is_rtx3050", False)

        # Detect local providers
        local_available = self.detect_local_providers()

        # Detect free API keys
        free_available = self.detect_free_api_keys()

        # Get priority list
        priority_list = self.get_provider_priority_list()

        config = {
            "system": {
                "gpu_detected": gpu_info.get("detected", False),
                "gpu_name": gpu_info.get("name"),
                "gpu_vram": gpu_info.get("vram", 0),
                "is_rtx3050": is_rtx3050,
            },
            "local_providers": {
                "available": local_available,
                "primary": priority_list[0] if priority_list else "ollama",
            },
            "free_api_providers": {
                "available": free_available,
                "configured": [k for k, v in free_available.items() if v],
            },
            "priority_list": priority_list,
            "recommended": {
                "provider": priority_list[0] if priority_list else "ollama",
                "model": RTX3050_CONFIG["recommended_model"] if is_rtx3050 else "mistral:7b",
            },
        }

        # Add RTX 3050 specific recommendations
        if is_rtx3050:
            config["rtx3050"] = {
                "optimized": True,
                "vram_total": 8,
                "vram_safe_limit": 7,
                "recommended_models": [
                    "llama3.2:3b",
                    "phi3:3.8b",
                    "mistral:7b",
                ],
                "install_command": "ollama pull mistral:7b",
            }

        return config


# Singleton instance
_local_first: LocalFirstProvider = None


def get_local_first() -> LocalFirstProvider:
    """Get the singleton LocalFirstProvider instance."""
    global _local_first
    if _local_first is None:
        _local_first = LocalFirstProvider()
    return _local_first


async def detect_and_configure() -> dict[str, Any]:
    """
    Detect system capabilities and auto-configure.

    Returns:
        Configuration dictionary
    """
    lfp = get_local_first()

    # Detect GPU
    gpu_info = lfp.detect_gpu()

    # Detect local providers
    local_available = lfp.detect_local_providers()

    # Try to auto-start Ollama if not running
    if not local_available.get("ollama", False):
        await lfp.auto_start_ollama()
        local_available = lfp.detect_local_providers()

    # Detect free API keys
    free_available = lfp.detect_free_api_keys()

    # Get priority list
    priority_list = lfp.get_provider_priority_list()

    return {
        "gpu": gpu_info,
        "local_providers": local_available,
        "free_api_providers": free_available,
        "priority_list": priority_list,
        "recommended": lfp.get_recommended_setup()["recommended"],
    }


def get_default_provider() -> str:
    """Get the default provider (first available local, or ollama)."""
    lfp = get_local_first()
    priority_list = lfp.get_provider_priority_list()
    return priority_list[0] if priority_list else "ollama"


def get_rtx3050_config() -> dict[str, Any]:
    """Get RTX 3050 optimized configuration."""
    return RTX3050_CONFIG.copy()
