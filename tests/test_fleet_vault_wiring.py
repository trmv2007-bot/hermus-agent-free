"""Tests for Vault wiring (roadmap step 2): DEFAULT_CHAT_FN wiring, chat_via_freellm
with Vault key resolution, free-tier boot warm-up, and unhealthy-binding detection.

All tests use deterministic stubs - no network, no LLM.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.fleet.registry import (
    DEFAULT_CHAT_FN,
    FleetRegistry,
    IDLE,
    chat_via_freellm,
)
from core.fleet.bus import FleetBus


# Helpers


class StubChat:
    """Deterministic stub chat_fn for offline tests."""

    def __init__(self, content: str = '[stub] reply', tokens: int = 10, error: str = None):
        self._content = content
        self._tokens = tokens
        self._error = error

    def __call__(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if self._error:
            return {"error": self._error, "content": self._content}
        return {"content": self._content, "tokens": self._tokens}


def _bus(tmp_path):
    return FleetBus(base_dir=str(tmp_path / "fleet"), fsync=False, snapshot_every_events=0, snapshot_interval_s=0)


# DEFAULT_CHAT_FN wiring


def test_default_chat_fn_is_chat_via_freellm():
    """DEFAULT_CHAT_FN must be the exact same object as chat_via_freellm."""
    from core.fleet.registry import DEFAULT_CHAT_FN, chat_via_freellm

    assert DEFAULT_CHAT_FN is chat_via_freellm, "DEFAULT_CHAT_FN must be the same object as chat_via_freellm"


def test_registry_uses_default_chat_fn_when_none_passed(tmp_path):
    """Registry with chat_fn=None uses DEFAULT_CHAT_FN (chat_via_freellm)."""
    from core.fleet.registry import DEFAULT_CHAT_FN, FleetRegistry

    bus = FleetBus(base_dir=str(tmp_path / "fleet"), fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    reg = FleetRegistry(bus, chat_fn=None)
    assert reg._chat_fn is DEFAULT_CHAT_FN

# chat_via_freellm with stubbed Vault + LLM


def test_chat_via_freellm_stubbed_vault_and_llm(tmp_path, monkeypatch):
    """chat_via_freellm uses Vault key bundle + FreeLLM; returns content + tokens."""
    from core.fleet.registry import chat_via_freellm, LiveAgent

    bundle = {"key": "sk-test-1234567890", "base_url": "http://fake/v1", "default_model": "fake-model"}
    # Note: llm_resp intentionally omits tool_calls (or has non-empty) so the code path adds it to result
    llm_resp = {"content": "hello from fake llm", "tool_calls": [{"id": "call_1", "function": {"name": "do_thing", "arguments": "{}"}}], "usage": {"total_tokens": 42}}

    monkeypatch.setattr("core.multi_key.multi_key_manager.get_key_bundle", lambda provider: bundle)

    import core.llm as llm_module

    class StubLLM:
        def __init__(self, model, api_key=None, base_url=None, provider=None):
            pass

        def chat(self, messages):
            return type("obj", (), llm_resp)()

    monkeypatch.setattr(llm_module, "FreeLLM", lambda *a, **kw: StubLLM(*a, **kw))

    agent = LiveAgent(
        agent_id="test-agent",
        name="Tester",
        persona="test persona",
        provider="custom",
        model="fake-model",
    )

    result = chat_via_freellm(agent, "say hello")

    assert result["content"] == "hello from fake llm"
    assert result["tokens"] == 42
    assert "tool_calls" in result


def test_chat_via_freellm_no_key_falls_back_to_autodetect(tmp_path, monkeypatch):
    """When Vault returns no bundle, chat_via_freellm falls back to FreeLLM auto-detection."""
    from core.fleet.registry import chat_via_freellm, LiveAgent

    # Patch Vault to return no bundle (simulating no keys configured)
    monkeypatch.setattr(
        "core.multi_key.multi_key_manager.get_key_bundle",
        lambda provider: None,
    )

    # Patch FreeLLM to return a successful response (simulating auto-detection working)
    import core.llm as llm_module

    class StubLLM:
        def __init__(self, model, api_key=None, base_url=None, provider=None):
            pass

        def chat(self, messages):
            return type("obj", (), {
                "content": "fallback response",
                "tool_calls": [{"id": "call_1", "function": {"name": "do_thing", "arguments": "{}"}}],
                "usage": {"total_tokens": 10},
            })()

    monkeypatch.setattr(llm_module, "FreeLLM", lambda *a, **kw: StubLLM(*a, **kw))

    agent = LiveAgent(
        agent_id="test-agent",
        name="Tester",
        persona="test persona",
        provider="custom",
        model="fake-model",
    )

    result = chat_via_freellm(agent, "say hello")

    # Should fall back to auto-detection and succeed
    assert result["content"] == "fallback response"
    assert result["tokens"] == 10
    assert "tool_calls" in result


# Boot warm-up runs once per registry instance


def test_boot_warmup_runs_once_per_instance(tmp_path, monkeypatch):
    """_ensure_free_tier runs once per registry instance (guarded by flag)."""
    counter = {"calls": 0}

    def fake_discover(auto_register=True):
        counter["calls"] += 1
        return {"discovered": ["openrouter", "groq"]}

    monkeypatch.setattr("core.free_keys.discover_and_provision_free_models", fake_discover)

    bus = FleetBus(base_dir=str(tmp_path / "fleet"), fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    reg1 = FleetRegistry(bus, chat_fn=lambda m: {"content": "x", "tokens": 1})

    # First access: spawn triggers warm-up
    reg1.spawn({"name": "a1", "provider": "groq", "model": "llama-3.3-70b"})
    assert counter["calls"] == 1

    # Second spawn on SAME registry: no extra call
    reg1.spawn({"name": "a2", "provider": "groq", "model": "llama-3.1-8b"})
    assert counter["calls"] == 1

    # Fresh registry instance: warm-up runs again
    bus2 = FleetBus(base_dir=str(tmp_path / "fleet2"), fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    reg2 = FleetRegistry(bus2, chat_fn=lambda m: {"content": "x", "tokens": 1})
    reg2.spawn({"name": "b1", "provider": "groq", "model": "llama-3.3-70b"})
    assert counter["calls"] == 2


def test_warmup_logs_discovered_providers(tmp_path, monkeypatch, caplog):
    """Free-tier warm-up logs discovered providers at INFO."""
    monkeypatch.setattr(
        "core.free_keys.discover_and_provision_free_models",
        lambda auto_register: {"discovered": ["openrouter", "groq", "mistral"]},
    )

    bus = FleetBus(base_dir=str(tmp_path / "fleet"), fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    reg = FleetRegistry(bus, chat_fn=lambda m: {"content": "x", "tokens": 1})
    reg.spawn({"name": "test", "provider": "groq", "model": "llama"})

    # Check logs for warm-up message
    warmup_logs = [r.message for r in caplog.records if "free-tier warm-up" in r.message]
    assert any("3 provider(s) discovered" in msg for msg in warmup_logs)

# Spawn records no-healthy-bindings state

def test_spawn_records_no_healthy_bindings(tmp_path, monkeypatch, caplog):
    from core.multi_key import multi_key_manager
    monkeypatch.setattr(multi_key_manager, 'get_dispatchable_entries', lambda provider: [])
    bus = FleetBus(base_dir=str(tmp_path / 'fleet'), fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    reg = FleetRegistry(bus, chat_fn=lambda m: {'content': 'x', 'tokens': 1})
    agent = reg.spawn({'name': 'sick', 'provider': 'groq', 'model': 'llama'})
    assert agent._warmup_status == 'no_healthy_bindings'
    assert any('cannot reach provider groq' in r.message for r in caplog.records if r.levelname == 'WARNING')

def test_spawn_ok_when_vault_has_keys(tmp_path, monkeypatch):
    """When Vault has healthy keys, agent gets _warmup_status='ok'."""
    from core.multi_key import multi_key_manager

    # Mock the free-tier warm-up to return a healthy provider
    monkeypatch.setattr(
        "core.free_keys.discover_and_provision_free_models",
        lambda auto_register: {"discovered": ["groq"]},
    )
    # Use a dict instead of a class so that .get("key") works
    monkeypatch.setattr(
        "core.multi_key.multi_key_manager.get_dispatchable_entries",
        lambda provider: [{"key": "sk-test-123"}],
    )
    monkeypatch.setattr(
        "core.multi_key.multi_key_manager.check_key_health",
        lambda provider, key: True,
    )

    bus = FleetBus(base_dir=str(tmp_path / "fleet"), fsync=False, snapshot_every_events=0, snapshot_interval_s=0)
    reg = FleetRegistry(bus, chat_fn=lambda m: {"content": "x", "tokens": 1})
    agent = reg.spawn({"name": "healthy", "provider": "groq", "model": "llama"})

    assert agent._warmup_status == "ok"

def test_chat_via_freellm_surfaces_tool_calls(tmp_path, monkeypatch):
    """When LLM returns tool_calls, chat_via_freellm surfaces them."""
    from core.fleet.registry import chat_via_freellm, LiveAgent

    monkeypatch.setattr('core.multi_key.multi_key_manager.get_key_bundle', lambda provider: {'key': 'sk-test', 'base_url': 'http://fake/v1', 'default_model': 'fake-model'})
    class StubLLM:
        def __init__(self, model, api_key=None, base_url=None, provider=None): pass
        def chat(self, messages): return type('obj', (), {'content': 'I will call a tool', 'tool_calls': [{'id': 'call_1', 'function': {'name': 'do_thing', 'arguments': '{}'}}], 'usage': {'total_tokens': 55}})()
    import core.llm as llm_module
    monkeypatch.setattr(llm_module, 'FreeLLM', lambda *a, **kw: StubLLM(*a, **kw))
    monkeypatch.setattr('core.multi_key.multi_key_manager.get_key_bundle', lambda provider: {'key': 'sk-test', 'base_url': 'http://fake/v1', 'default_model': 'fake-model'})
    agent = LiveAgent(agent_id='test', name='Tester', persona='p', provider='custom', model='m')
    result = chat_via_freellm(agent, 'do it')
    assert 'tool_calls' in result
    assert result['tool_calls'][0]['id'] == 'call_1'
    assert result['tokens'] == 55
