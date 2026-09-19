"""Persistent agent allocation across multiple provider API keys."""

from __future__ import annotations

import asyncio

import pytest

from core.agents.pool import AgentCapacityError, AgentPool, PoolConfig, ProviderKey


def test_provider_keys_fill_two_agent_slots_before_advancing(tmp_path):
    async def scenario():
        pool = AgentPool(
            PoolConfig(
                max_agents=10,
                max_agents_per_key=2,
                max_agents_per_provider={"openrouter": 10},
            ),
            state_path=str(tmp_path / "agents.json"),
        )
        # Keep this test independent of the user's environment and key vault.
        pool._load_api_keys = lambda: None
        pool._provider_keys["openrouter"] = [
            ProviderKey("openrouter", "key-a", name="router-a", base_url="https://example.test/v1"),
            ProviderKey("openrouter", "key-b", name="router-b", base_url="https://example.test/v1"),
        ]
        try:
            agents = [
                await pool.create_agent(provider="openrouter", model=f"model-{index}")
                for index in range(4)
            ]
            assert [agent.config.key_name for agent in agents] == [
                "router-a",
                "router-a",
                "router-b",
                "router-b",
            ]
            assert pool.get_key_usage("openrouter") == {
                "openrouter": [
                    {"name": "router-a", "assigned_agents": 2, "capacity": 2, "available_slots": 0},
                    {"name": "router-b", "assigned_agents": 2, "capacity": 2, "available_slots": 0},
                ]
            }
            with pytest.raises(AgentCapacityError, match="All openrouter API keys are full"):
                await pool.create_agent(provider="openrouter", model="model-overflow")
        finally:
            await pool.destroy_all()

    asyncio.run(scenario())


def test_explicit_key_respects_its_own_capacity(tmp_path):
    async def scenario():
        pool = AgentPool(
            PoolConfig(max_agents=10, max_agents_per_key=2, max_agents_per_provider={"openrouter": 10}),
            state_path=str(tmp_path / "agents.json"),
        )
        pool._load_api_keys = lambda: None
        pool._provider_keys["openrouter"] = [
            ProviderKey("openrouter", "key-a", name="router-a"),
            ProviderKey("openrouter", "key-b", name="router-b"),
        ]
        try:
            await pool.create_agent(provider="openrouter", model="model-1", key_name="router-a")
            await pool.create_agent(provider="openrouter", model="model-2", key_name="router-a")
            with pytest.raises(AgentCapacityError, match="router-a reached"):
                await pool.create_agent(provider="openrouter", model="model-3", key_name="router-a")
            agent = await pool.create_agent(provider="openrouter", model="model-4", key_name="router-b")
            assert agent.config.key_name == "router-b"
        finally:
            await pool.destroy_all()

    asyncio.run(scenario())
