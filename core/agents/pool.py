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
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
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
    max_agents_per_key: int = 2  # Persistent model-agent slots per provider key
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
            max_agents_per_key=int(
                os.environ.get("HERMUS_MAX_AGENTS_PER_KEY", os.environ.get("HERMUS_MAX_MODELS_PER_KEY", "2"))
            ),
            idle_timeout=float(os.environ.get("HERMUS_AGENT_IDLE_TIMEOUT", "900")),
            cleanup_interval=float(os.environ.get("HERMUS_AGENT_CLEANUP_INTERVAL", "60")),
        )


@dataclass
class ProviderKey:
    """Represents an API key for a provider."""

    provider_id: str
    key: str
    name: str = None
    base_url: str = None
    rate_limit: int = None
    tokens_used: int = 0
    last_used: float = 0

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "key": "***REDACTED***" if self.key else None,
            "base_url": self.base_url,
            "rate_limit": self.rate_limit,
            "tokens_used": self.tokens_used,
            "last_used": self.last_used,
        }


class AgentCapacityError(RuntimeError):
    """Raised when a provider or API key has no persistent agent slot left."""


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

    def __init__(self, config: PoolConfig = None, state_path: str = None):
        self.config = config if config is not None else PoolConfig.from_env()
        self._agents: dict[str, Agent] = {}
        self._provider_keys: dict[str, list[ProviderKey]] = defaultdict(list)
        self._provider_usage: dict[str, int] = defaultdict(int)
        self._active_tasks: dict[str, str] = {}  # agent_id -> task_id
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task = None
        self._running = False
        self._state_path = Path(state_path or "data/agents.json")
        if not self._state_path.is_absolute():
            self._state_path = Path(__file__).resolve().parents[2] / self._state_path
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

        # Load API keys from environment
        self._load_api_keys()

    def _load_api_keys(self):
        """Load API keys from environment variables."""
        previous = {provider: list(keys) for provider, keys in self._provider_keys.items()}
        self._provider_keys = defaultdict(list)

        def add_key(entry: ProviderKey) -> None:
            if entry.key and not any(existing.key == entry.key for existing in self._provider_keys[entry.provider_id]):
                self._provider_keys[entry.provider_id].append(entry)

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
                        add_key(
                            ProviderKey(
                                provider_id=provider_id,
                                key=key,
                                name=f"{provider_id}_env_{len(self._provider_keys[provider_id]) + 1}",
                                base_url=base_url,
                                rate_limit=rate_limit,
                            )
                        )
                        logger.info(f"🔑 Loaded {provider_id} key (total: {len(self._provider_keys[provider_id])})")

        # The dashboard writes keys through MultiKeyManager so they survive a
        # gateway restart. Mirror those entries into the pool for round-robin
        # agent assignment without ever exposing them in API responses.
        try:
            from core.multi_key import multi_key_manager

            for entry in multi_key_manager.get_all_entries():
                provider_id = entry.get("provider")
                key = entry.get("key") or ""
                if not provider_id or not key:
                    continue
                add_key(
                    ProviderKey(
                        provider_id=provider_id,
                        key=key,
                        name=entry.get("name"),
                        base_url=entry.get("base_url"),
                        rate_limit=entry.get("rpm_limit"),
                    )
                )
        except Exception as exc:
            logger.warning(f"Could not load stored provider keys: {exc}")

        for provider_id, keys in previous.items():
            for key in keys:
                add_key(key)

    def _key_agent_count(self, provider: str, key: ProviderKey) -> int:
        count = 0
        for agent in self._agents.values():
            if agent.provider != provider:
                continue
            cfg = agent.config
            if key.name and cfg.key_name == key.name:
                count += 1
            elif cfg.api_key and cfg.api_key == key.key:
                count += 1
        return count

    def _direct_key_agent_count(self, provider: str, *, key_name: str = None, api_key: str = None) -> int:
        return sum(
            1
            for agent in self._agents.values()
            if agent.provider == provider
            and (
                (key_name and agent.config.key_name == key_name)
                or (api_key and agent.config.api_key == api_key)
            )
        )

    def _select_provider_key(
        self,
        provider: str,
        *,
        key_name: str = None,
        api_key: str = None,
    ) -> ProviderKey | None:
        """Select a key with capacity, filling keys in stable order."""
        self._load_api_keys()
        keys = self._provider_keys.get(provider, [])
        if not keys:
            return None

        if key_name or api_key:
            selected = next((key for key in keys if key.name == key_name or key.key == api_key), None)
            if selected is None:
                raise AgentCapacityError(f"Requested {provider} API key is not registered")
            if self._key_agent_count(provider, selected) >= self.config.max_agents_per_key:
                raise AgentCapacityError(
                    f"API key {selected.name} reached its {self.config.max_agents_per_key}-agent limit"
                )
            return selected

        for key in keys:
            if self._key_agent_count(provider, key) < self.config.max_agents_per_key:
                return key

        raise AgentCapacityError(
            f"All {provider} API keys are full ({self.config.max_agents_per_key} agents per key)"
        )

    def get_key_usage(self, provider: str = None) -> dict[str, list[dict[str, Any]]]:
        """Return safe per-key assignment counts for the dashboard."""
        self._load_api_keys()
        providers = [provider] if provider else sorted(self._provider_keys)
        result: dict[str, list[dict[str, Any]]] = {}
        for provider_id in providers:
            result[provider_id] = [
                {
                    "name": key.name,
                    "assigned_agents": self._key_agent_count(provider_id, key),
                    "capacity": self.config.max_agents_per_key,
                    "available_slots": max(0, self.config.max_agents_per_key - self._key_agent_count(provider_id, key)),
                }
                for key in self._provider_keys.get(provider_id, [])
            ]
        return result

    async def start(self):
        if self._running:
            return
        """Start the agent pool."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        await self._restore_agents()
        logger.info(f"🏊 Agent pool started (max: {self.config.max_agents}, concurrent: {self.config.max_concurrent})")

    async def stop(self):
        """Stop the agent pool and all agents."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # Destroy all agents
        for agent_id, agent in list(self._agents.items()):
            agent.destroy()
        self._agents.clear()

        logger.info("🛑 Agent pool stopped")

    def _read_state(self) -> list[dict]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _write_state(self, records: list[dict]) -> None:
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(records, indent=2), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _safe_record(self, agent: Agent) -> dict:
        """Persist configuration, never the raw credential."""
        cfg = agent.config
        return {
            "agent_id": agent.agent_id,
            "name": cfg.name,
            "provider": cfg.provider,
            "model": cfg.model,
            "role": agent.role.value,
            "key_name": cfg.key_name,
            "base_url": cfg.base_url,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "timeout": cfg.timeout,
            "retry_attempts": cfg.retry_attempts,
            "idle_timeout": cfg.idle_timeout,
            "max_concurrent": cfg.max_concurrent,
        }

    def _persist_agent(self, agent: Agent) -> None:
        records = [r for r in self._read_state() if r.get("agent_id") != agent.agent_id]
        records.append(self._safe_record(agent))
        self._write_state(records)

    def _forget_agent(self, agent_id: str) -> None:
        records = [r for r in self._read_state() if r.get("agent_id") != agent_id]
        self._write_state(records)

    def _credentials_for(self, provider: str, key_name: str = None) -> tuple[str | None, str | None, str | None]:
        if provider in {"ollama", "nollama", "lmstudio"}:
            return None, None, None
        try:
            from core.multi_key import multi_key_manager

            entry = multi_key_manager.get_entry(provider, key_name) if key_name else None
            entry = entry or multi_key_manager.get_entry(provider)
            if entry:
                return entry.get("key"), entry.get("base_url"), entry.get("name")
        except Exception:
            pass
        return None, None, key_name

    async def _restore_agents(self) -> None:
        for record in self._read_state():
            if len(self._agents) >= self.config.max_agents:
                break
            if record.get("agent_id") in self._agents:
                continue
            provider = record.get("provider") or "ollama"
            key, stored_base_url, stored_key_name = self._credentials_for(provider, record.get("key_name"))
            try:
                role = AgentRole(record.get("role", AgentRole.GENERAL.value))
            except ValueError:
                role = AgentRole.GENERAL
            agent_config = AgentConfig(
                name=record.get("name"),
                provider=provider,
                model=record.get("model") or self._get_default_model(provider, role),
                role=role,
                api_key=key,
                key_name=stored_key_name,
                base_url=stored_base_url or record.get("base_url"),
                max_tokens=record.get("max_tokens", 4096),
                temperature=record.get("temperature", 0.7),
                timeout=record.get("timeout", 120),
                retry_attempts=record.get("retry_attempts", 3),
                idle_timeout=record.get("idle_timeout", 900),
                max_concurrent=record.get("max_concurrent", 1),
            )
            agent = Agent(agent_id=record.get("agent_id"), config=agent_config)
            self._agents[agent.agent_id] = agent

    def _create_agent_locked(self, config: AgentConfig) -> Agent:
        agent = Agent(config=config)
        self._agents[agent.agent_id] = agent
        self._persist_agent(agent)
        return agent

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
                    agent.destroy()
                    destroyed.append(agent_id)
                    del self._agents[agent_id]
                    self._forget_agent(agent_id)

        if destroyed:
            logger.info(f"🧹 Cleaned up {len(destroyed)} idle agents")

        return destroyed

    async def create_agent(
        self,
        name: str = None,
        role: AgentRole = AgentRole.GENERAL,
        provider: str = None,
        model: str = None,
        config: AgentConfig = None,
        key_name: str = None,
        **kwargs,
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
            if config is not None:
                if not isinstance(config, AgentConfig):
                    raise TypeError("config must be an AgentConfig")
                if len(self._agents) >= self.config.max_agents:
                    for agent_id, existing in list(self._agents.items()):
                        if existing.is_idle(self.config.idle_timeout) and existing.state != AgentState.WORKING:
                            existing.destroy()
                            del self._agents[agent_id]
                            self._forget_agent(agent_id)
                    if len(self._agents) >= self.config.max_agents:
                        raise Exception(f"Agent pool full ({self.config.max_agents} agents)")
                provider = config.provider or "ollama"
                if provider not in {"ollama", "nollama", "lmstudio"}:
                    if config.api_key:
                        self._load_api_keys()
                        selected = next(
                            (
                                item
                                for item in self._provider_keys.get(provider, [])
                                if item.key == config.api_key or item.name == config.key_name
                            ),
                            None,
                        )
                        assigned = self._key_agent_count(provider, selected) if selected else self._direct_key_agent_count(
                            provider, key_name=config.key_name, api_key=config.api_key
                        )
                        if assigned >= self.config.max_agents_per_key:
                            label = selected.name if selected else (config.key_name or "provided key")
                            raise AgentCapacityError(
                                f"API key {label} reached its {self.config.max_agents_per_key}-agent limit"
                            )
                        if selected:
                            config.key_name = selected.name
                            config.base_url = config.base_url or selected.base_url
                    else:
                        selected = self._select_provider_key(provider, key_name=config.key_name)
                        if selected:
                            config.api_key = selected.key
                            config.base_url = config.base_url or selected.base_url
                            config.key_name = selected.name
                        else:
                            key, base_url, selected_name = self._credentials_for(provider, config.key_name or key_name)
                            config.api_key = key
                            config.base_url = config.base_url or base_url
                            config.key_name = config.key_name or selected_name
                return self._create_agent_locked(config)
            # Check pool limit
            if len(self._agents) >= self.config.max_agents:
                for agent_id, existing in list(self._agents.items()):
                    if existing.is_idle(self.config.idle_timeout) and existing.state != AgentState.WORKING:
                        existing.destroy()
                        del self._agents[agent_id]
                        self._forget_agent(agent_id)
                if len(self._agents) >= self.config.max_agents:
                    raise Exception(f"Agent pool full ({self.config.max_agents} agents)")

            # Select provider if not specified
            provider = provider or self._select_provider(role)

            # Get API key if needed
            api_key = None
            base_url = None

            if provider != "ollama" and provider != "nollama":
                selected_key = self._select_provider_key(provider, key_name=key_name)
                if selected_key:
                    api_key = selected_key.key
                    base_url = selected_key.base_url
                    key_name = selected_key.name
                else:
                    key, configured_base_url, selected_name = self._credentials_for(provider, key_name)
                    if key:
                        api_key = key
                        base_url = configured_base_url
                        key_name = selected_name
                    elif provider not in {"lmstudio"}:
                        # Fallback to Ollama only when the requested provider is
                        # genuinely unavailable.
                        provider = "ollama"

            # Create agent config
            config = AgentConfig(
                name=name,
                provider=provider,
                model=model or self._get_default_model(provider, role),
                role=role,
                api_key=api_key,
                key_name=key_name,
                base_url=base_url,
                **kwargs,
            )

            # Create the agent
            agent = self._create_agent_locked(config)

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
                self._forget_agent(agent_id)
                return True
            return False

    async def destroy_all(self) -> int:
        """Destroy all agents."""
        count = 0
        async with self._lock:
            for agent_id in list(self._agents.keys()):
                self._agents[agent_id].destroy()
                del self._agents[agent_id]
                self._forget_agent(agent_id)
                count += 1
        return count

    async def start_agent(self, agent_id: str) -> bool:
        agent = self._agents.get(agent_id)
        if not agent or agent.state == AgentState.DESTROYED:
            return False
        await agent.wake()
        return True

    async def stop_agent(self, agent_id: str) -> bool:
        agent = self._agents.get(agent_id)
        if not agent or agent.state == AgentState.DESTROYED:
            return False
        await agent.sleep()
        return True

    async def start_all(self) -> int:
        count = 0
        for agent in self._agents.values():
            if agent.state == AgentState.SLEEPING:
                await agent.wake()
                count += 1
        return count

    async def stop_all(self) -> int:
        count = 0
        for agent in self._agents.values():
            if agent.state not in {AgentState.SLEEPING, AgentState.DESTROYED}:
                await agent.sleep()
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
            "max_agents_per_key": self.config.max_agents_per_key,
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

            if not candidates and len(self._agents) < self.config.max_agents:
                return self._create_agent_locked(AgentConfig(role=role or AgentRole.GENERAL))
            if not candidates:
                raise Exception("All agents busy, try again later")

            # Return the best candidate
            return candidates[0]

    def get_available_keys(self, provider: str) -> list[str]:
        """Get list of available API keys for a provider."""
        return [k.key for k in self._provider_keys.get(provider, [])]

    def add_api_key(self, provider: str, key: str, base_url: str = None, name: str = None) -> bool:
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

        if any(existing.key == key for existing in self._provider_keys[provider]):
            return False
        self._provider_keys[provider].append(
            ProviderKey(
                provider_id=provider,
                key=key,
                name=name or f"{provider}_runtime_{len(self._provider_keys[provider]) + 1}",
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
            if pk.key == key or pk.name == key:
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
