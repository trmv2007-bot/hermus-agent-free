"""
Agent Pool - Manages multiple persistent agents with load balancing.

Features:
- Create, retrieve, and destroy agents
- Multi-key load balancing across providers
- Agent persistence with idle timeout
- Resource monitoring and limits
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from core.log import get_logger
from core.config import config
from core.providers import PROVIDER_PRESETS
from .agent import Agent, AgentConfig, AgentState, AgentRole

logger = get_logger(__name__)


@dataclass
class PoolConfig:
    """Configuration for the agent pool."""

    max_agents: int = 100  # Maximum agents in pool
    max_concurrent: int = 10  # Maximum concurrently working agents
    idle_timeout: float = 900.0  # 15 minutes idle before cleanup
    cleanup_interval: float = 60.0  # Cleanup check interval (seconds)

    # Per-provider limits
    max_agents_per_provider: dict[str, int] = field(
        default_factory=lambda: {
            "groq": 10,
            "mistral": 10,
            "openrouter": 10,
            "ollama": 5,  # Local, less overhead
            "nollama": 5,
        }
    )

    @classmethod
    def from_env(cls) -> "PoolConfig":
        """Load configuration from environment."""
        return cls(
            max_agents=int(os.environ.get("HERMUS_MAX_AGENTS", "100")),
            max_concurrent=int(os.environ.get("HERMUS_MAX_CONCURRENT", "10")),
            idle_timeout=float(os.environ.get("HERMUS_AGENT_IDLE_TIMEOUT", "900")),
            cleanup_interval=float(os.environ.get("HERMUS_AGENT_CLEANUP_INTERVAL", "60")),
        )


@dataclass
class ProviderKey:
    """Represents an API key for a provider."""

    provider_id: str
    key: str
    base_url: str = None
    rate_limit: int = None
    tokens_used: int = 0
    last_used: float = 0

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "key": "***REDACTED***" if self.key else None,
            "base_url": self.base_url,
            "rate_limit": self.rate_limit,
            "tokens_used": self.tokens_used,
            "last_used": self.last_used,
        }


class AgentPool:
    """
    Manages a pool of persistent agents.

    Features:
    - Create agents with specific configurations
    - Load balance across multiple API keys
    - Track agent states and resources
    - Auto-cleanup idle agents
    - Rate limit management
    """

    def __init__(self, config: PoolConfig = None):
        self.config = config or PoolConfig()
        self._agents: dict[str, Agent] = {}
        self._provider_keys: dict[str, list[ProviderKey]] = defaultdict(list)
        self._provider_usage: dict[str, int] = defaultdict(int)
        self._active_tasks: dict[str, str] = {}  # agent_id -> task_id
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task = None
        self._running = False

        # Load API keys from environment
        self._load_api_keys()

    def _load_api_keys(self):
        """Load API keys from environment variables."""
        for provider_id, preset in PROVIDER_PRESETS.items():
            env_key = preset.get("env_key")
            if env_key and os.environ.get(env_key):
                base_url = preset.get("base_url", "")
                rate_limit = preset.get("default_rpm")

                # Support multiple keys (comma-separated)
                keys = os.environ[env_key].split(",")
                for key in keys:
                    key = key.strip()
                    if key:
                        self._provider_keys[provider_id].append(
                            ProviderKey(
                                provider_id=provider_id,
                                key=key,
                                base_url=base_url,
                                rate_limit=rate_limit,
                            )
                        )
                        logger.info(f"🔑 Loaded {provider_id} key (total: {len(self._provider_keys[provider_id])})")

    async def start(self):
        """Start the agent pool."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"🏊 Agent pool started (max: {self.config.max_agents}, concurrent: {self.config.max_concurrent})")

    async def stop(self):
        """Stop the agent pool and all agents."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()

        # Destroy all agents
        for agent_id, agent in list(self._agents.items()):
            agent.destroy()

        logger.info("🛑 Agent pool stopped")

    async def _cleanup_loop(self):
        """Periodically cleanup idle agents."""
        while self._running:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                await self.cleanup_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    async def cleanup_idle(self, timeout: float = None) -> list[str]:
        """
        Cleanup agents that have been idle too long.

        Args:
            timeout: Override the default idle timeout

        Returns:
            List of destroyed agent IDs
        """
        timeout = timeout or self.config.idle_timeout
        destroyed = []

        async with self._lock:
            for agent_id, agent in list(self._agents.items()):
                if agent.is_idle(timeout) and agent.state != AgentState.WORKING:
                    # Check if we're over the limit
                    if len(self._agents) > self.config.max_agents:
                        agent.destroy()
                        destroyed.append(agent_id)
                        del self._agents[agent_id]

        if destroyed:
            logger.info(f"🧹 Cleaned up {len(destroyed)} idle agents")

        return destroyed

    async def create_agent(
        self, name: str = None, role: AgentRole = AgentRole.GENERAL, provider: str = None, model: str = None, **kwargs
    ) -> Agent:
        """
        Create a new agent in the pool.

        Args:
            name: Agent name (optional)
            role: Agent role/specialization
            provider: Provider to use (ollama, groq, mistral, etc.)
            model: Model to use
            **kwargs: Additional config

        Returns:
            The created Agent
        """
        async with self._lock:
            # Check pool limit
            if len(self._agents) >= self.config.max_agents:
                await self.cleanup_idle()
                if len(self._agents) >= self.config.max_agents:
                    raise Exception(f"Agent pool full ({self.config.max_agents} agents)")

            # Select provider if not specified
            provider = provider or self._select_provider(role)

            # Get API key if needed
            api_key = None
            base_url = None

            if provider != "ollama" and provider != "nollama":
                # Try to get a key for this provider
                keys = self._provider_keys.get(provider, [])
                if keys:
                    # Round-robin selection
                    idx = self._provider_usage.get(provider, 0) % len(keys)
                    selected_key = keys[idx]
                    api_key = selected_key.key
                    base_url = selected_key.base_url
                    self._provider_usage[provider] = (self._provider_usage.get(provider, 0) + 1) % len(keys)
                else:
                    # Fallback to Ollama
                    provider = "ollama"

            # Create agent config
            config = AgentConfig(
                name=name,
                provider=provider,
                model=model or self._get_default_model(provider, role),
                role=role,
                api_key=api_key,
                base_url=base_url,
                **kwargs,
            )

            # Create the agent
            agent = Agent(config=config)
            self._agents[agent.agent_id] = agent

            logger.info(f"✨ Created agent {agent.config.name} ({agent.agent_id[:8]}) with {provider}/{config.model}")

            return agent

    def _select_provider(self, role: AgentRole = None) -> str:
        """
        Select the best provider for a given role.

        Priority:
        1. Local providers (ollama, nollama) - no API keys needed
        2. Free API providers with keys configured
        3. First available
        """
        # Check local providers first
        for local in ["ollama", "nollama"]:
            if self._has_provider(local):
                return local

        # Check free API providers with keys
        for provider_id in ["groq", "openrouter", "mistral", "codestral", "together", "fireworks", "deepseek"]:
            if self._provider_keys.get(provider_id):
                return provider_id

        # Fallback to Ollama
        return "ollama"

    def _has_provider(self, provider_id: str) -> bool:
        """Check if a provider is available."""
        if provider_id in ["ollama", "nollama"]:
            return True  # Local providers always available
        return len(self._provider_keys.get(provider_id, [])) > 0

    def _get_default_model(self, provider: str, role: AgentRole) -> str:
        """Get the default model for a provider and role."""
        # RTX 3050 optimized models
        rtx3050_models = {
            "ollama": {
                AgentRole.GENERAL: "mistral:7b",
                AgentRole.RESEARCHER: "llama3.2:3b",
                AgentRole.CODER: "phi3:3.8b",
                AgentRole.VERIFIER: "llama3.2:3b",
            },
            "nollama": {
                AgentRole.GENERAL: "MiniCPM5-1B-int4-g128-ov",
                AgentRole.RESEARCHER: "MiniCPM5-1B-int4-g128-ov",
                AgentRole.CODER: "MiniCPM5-1B-int4-g128-ov",
                AgentRole.VERIFIER: "MiniCPM5-1B-int4-g128-ov",
            },
            "groq": {
                AgentRole.GENERAL: "llama-3.1-8b-instant",
                AgentRole.RESEARCHER: "llama-3.1-8b-instant",
                AgentRole.CODER: "llama-3.1-8b-instant",
                AgentRole.VERIFIER: "llama-3.1-8b-instant",
            },
            "mistral": {
                AgentRole.GENERAL: "devstral-latest",
                AgentRole.RESEARCHER: "devstral-latest",
                AgentRole.CODER: "codestral-latest",
                AgentRole.VERIFIER: "devstral-latest",
            },
        }

        if provider in rtx3050_models and role in rtx3050_models[provider]:
            return rtx3050_models[provider][role]

        # Default models
        defaults = {
            "ollama": "mistral:7b",
            "nollama": "MiniCPM5-1B-int4-g128-ov",
            "groq": "llama-3.1-8b-instant",
            "mistral": "devstral-latest",
            "openrouter": "openrouter/auto",
        }

        return defaults.get(provider, "mistral:7b")

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[Agent]:
        """Get all agents in the pool."""
        return list(self._agents.values())

    def get_agents_by_state(self, state: AgentState) -> list[Agent]:
        """Get agents by their current state."""
        return [agent for agent in self._agents.values() if agent.state == state]

    def get_agents_by_role(self, role: AgentRole) -> list[Agent]:
        """Get agents by their role."""
        return [agent for agent in self._agents.values() if agent.role == role]

    def get_agents_by_provider(self, provider: str) -> list[Agent]:
        """Get agents using a specific provider."""
        return [agent for agent in self._agents.values() if agent.config.provider == provider]

    async def destroy_agent(self, agent_id: str) -> bool:
        """Destroy an agent by ID."""
        async with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].destroy()
                del self._agents[agent_id]
                return True
            return False

    async def destroy_all(self) -> int:
        """Destroy all agents."""
        count = 0
        async with self._lock:
            for agent_id in list(self._agents.keys()):
                await self.destroy_agent(agent_id)
                count += 1
        return count

    def get_stats(self) -> dict:
        """Get pool statistics."""
        agents = list(self._agents.values())

        states = {}
        providers = {}
        roles = {}

        for agent in agents:
            state = agent.state.value
            states[state] = states.get(state, 0) + 1

            provider = agent.config.provider
            providers[provider] = providers.get(provider, 0) + 1

            role = agent.role.value
            roles[role] = roles.get(role, 0) + 1

        return {
            "total_agents": len(agents),
            "states": states,
            "providers": providers,
            "roles": roles,
            "max_agents": self.config.max_agents,
            "max_concurrent": self.config.max_concurrent,
            "idle_timeout": self.config.idle_timeout,
            "provider_keys": {k: len(v) for k, v in self._provider_keys.items()},
        }

    def get_provider_stats(self) -> dict:
        """Get statistics about provider key usage."""
        stats = {}
        for provider_id, keys in self._provider_keys.items():
            total_tokens = sum(k.tokens_used for k in keys)
            stats[provider_id] = {
                "key_count": len(keys),
                "total_tokens_used": total_tokens,
            }
        return stats

    async def assign_task(self, task: str, role: AgentRole = None) -> Agent:
        """
        Assign a task to an available agent.

        Args:
            task: The task to perform
            role: Preferred agent role

        Returns:
            Agent that will handle the task
        """
        async with self._lock:
            # Find idle agent with matching role
            candidates = []

            for agent in self._agents.values():
                if agent.state == AgentState.IDLE:
                    if role and agent.role == role:
                        candidates.insert(0, agent)  # Prioritize matching role
                    else:
                        candidates.append(agent)

            # If no idle agents, create a new one
            if not candidates:
                if len(self._agents) < self.config.max_agents:
                    agent = await self.create_agent(role=role)
                    return agent
                else:
                    # Wait for an agent to become available
                    raise Exception("All agents busy, try again later")

            # Return the best candidate
            return candidates[0]

    def get_available_keys(self, provider: str) -> list[str]:
        """Get list of available API keys for a provider."""
        return [k.key for k in self._provider_keys.get(provider, [])]

    def add_api_key(self, provider: str, key: str, base_url: str = None) -> bool:
        """
        Add an API key for a provider.

        Args:
            provider: Provider ID (groq, mistral, etc.)
            key: API key
            base_url: Optional custom base URL

        Returns:
            True if added successfully
        """
        preset = PROVIDER_PRESETS.get(provider, {})
        rate_limit = preset.get("default_rpm")
        base_url = base_url or preset.get("base_url", "")

        self._provider_keys[provider].append(
            ProviderKey(
                provider_id=provider,
                key=key,
                base_url=base_url,
                rate_limit=rate_limit,
            )
        )

        logger.info(f"🔑 Added {provider} API key")
        return True

    def remove_api_key(self, provider: str, key: str) -> bool:
        """Remove an API key."""
        keys = self._provider_keys.get(provider, [])
        for i, pk in enumerate(keys):
            if pk.key == key:
                del keys[i]
                logger.info(f"🗑️ Removed {provider} API key")
                return True
        return False

    def list_api_keys(self) -> dict[str, list[dict]]:
        """List all configured API keys (redacted)."""
        return {provider: [k.to_dict() for k in keys] for provider, keys in self._provider_keys.items()}


# Global pool instance
_pool: Optional[AgentPool] = None


def get_pool() -> AgentPool:
    """Get the global agent pool instance."""
    global _pool
    if _pool is None:
        _pool = AgentPool()
    return _pool


async def init_pool() -> AgentPool:
    """Initialize and start the global agent pool."""
    pool = get_pool()
    await pool.start()
    return pool


async def shutdown_pool() -> None:
    """Shutdown the global agent pool."""
    global _pool
    if _pool is not None:
        await _pool.stop()
        _pool = None
