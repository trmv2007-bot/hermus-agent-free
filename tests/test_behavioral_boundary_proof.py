"""Behavioral boundary proof gates (spec §15: gates must PROVE usage, not existence).

These run a *real* agent turn (offline mock provider) and instrument the canonical
boundaries — ModelGateway, ToolGateway, MemoryFacade — asserting the runtime
actually routed through them. This is deliberately NOT a structural "does the
module exist" check: if someone plugs in a bypass that constructs a provider or
invokes the registry directly, these fail.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def _agent_env(monkeypatch):
    """Set up an offline, deterministic agent environment."""
    # Force the free/mock stack so no network or API key is needed.
    from core.config import config
    old_model = getattr(config, "model", None)
    old_mode = getattr(config, "agent_mode", None)
    monkeypatch.setattr(config, "model", "mock/mock", raising=False)
    monkeypatch.setattr(config, "agent_mode", "agent", raising=False)
    yield
    if old_model is not None:
        monkeypatch.setattr(config, "model", old_model, raising=False)
    if old_mode is not None:
        monkeypatch.setattr(config, "agent_mode", old_mode, raising=False)


def test_model_gateway_is_used_by_a_real_agent_turn():
    """A real chat turn must obtain its model client through ModelGateway.llm()."""
    from unittest import mock

    from core.models.gateway import ModelGateway, get_model_gateway

    calls = {"llm": 0}
    real_llm = ModelGateway.llm

    def tracking_llm(self, *a, **kw):
        calls["llm"] += 1
        return real_llm(self, *a, **kw)

    gw = get_model_gateway()
    with mock.patch.object(type(gw), "llm", tracking_llm):
        from core.agent import HermusAgent
        agent = HermusAgent(model="mock/mock", max_steps=2)
        out = agent.chat("hello world", stream=False)
        assert out and out.get("response"), "agent turn should produce a response"

    assert calls["llm"] >= 1, "the agent must build its model via the canonical ModelGateway"


def test_memory_facade_is_used_by_a_real_agent_turn():
    """A real turn must recall + persist through the canonical MemoryFacade."""
    from unittest import mock

    from core.memory.store import MemoryFacade

    calls = {"recall": 0, "remember": 0}
    real_recall = MemoryFacade.recall_context
    real_remember = MemoryFacade.remember

    def tracking_recall(self, *a, **kw):
        calls["recall"] += 1
        return real_recall(self, *a, **kw)

    def tracking_remember(self, *a, **kw):
        calls["remember"] += 1
        return real_remember(self, *a, **kw)

    with mock.patch.object(MemoryFacade, "recall_context", tracking_recall), \
         mock.patch.object(MemoryFacade, "remember", tracking_remember):
        from core.agent import HermusAgent
        agent = HermusAgent(model="mock/mock", max_steps=2)
        out = agent.chat("remember that I prefer clean code", stream=False)
        assert out and out.get("response")

    assert calls["recall"] >= 1, "the agent must recall via the canonical MemoryFacade"
    # 'remember that ...' triggers the semantic persistence path in the facade.
    assert calls["remember"] >= 1, "the agent must persist via the canonical MemoryFacade"


def test_tool_gateway_is_used_by_a_real_agent_turn():
    """A real turn that emits a tool call must route it through ToolGateway.execute."""
    from unittest import mock

    from core.tools.gateway import ToolGateway, get_tool_gateway

    calls = {"execute": 0}
    tg = get_tool_gateway()
    real_execute = ToolGateway.execute

    def tracking_execute(self, *a, **kw):
        calls["execute"] += 1
        return real_execute(self, *a, **kw)

    # The mock provider emits a web_search tool call for user text containing
    # "search", so the agent loop invokes _execute_tool -> ToolGateway.execute.
    with mock.patch.object(type(tg), "execute", tracking_execute):
        from core.agent import HermusAgent
        agent = HermusAgent(model="mock/mock", max_steps=4)
        out = agent.chat("search the web for clustering", stream=False)
        assert out and out.get("response")

    assert calls["execute"] >= 1, "a tool-emitted turn must route through ToolGateway.execute"
