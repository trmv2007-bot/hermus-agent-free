"""Regression tests for the custom URL + custom API chat fixes.

Covers:
- custom key with base_url is actually used in chat mode
- model switches take effect (gateway agent cache bug)
- default model (Ollama down) falls back to the configured custom key
- custom API tools are available in chat/multi-chat modes
"""
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent))
sys.path.append(str(Path(__file__).parent))

from mock_openai_server import serve, LOGS  # noqa: E402


@pytest.fixture()
def mock_server():
    LOGS.clear()
    srv = serve(port=0)  # ephemeral port — no cross-test conflicts
    port = srv.server_address[1]
    yield {"srv": srv, "base": f"http://127.0.0.1:{port}/v1"}
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def clean_keys():
    from core.multi_key import multi_key_manager

    db = Path("data/api_keys.json")
    if db.exists():
        db.unlink()
    multi_key_manager._load_queues()
    yield
    if db.exists():
        db.unlink()
    multi_key_manager._load_queues()


@pytest.fixture()
def clean_custom_apis():
    from core.custom_api import custom_api_manager

    custom_api_manager._save([])
    yield
    custom_api_manager._save([])


def test_custom_key_used_in_chat_mode(mock_server, clean_keys):
    from core.multi_key import multi_key_manager
    from core.agent import HermusAgent

    r = multi_key_manager.add_key(
        "custom",
        "test-key-123",
        name="mycustom",
        base_url=mock_server["base"],
        default_model="test-model",
        auto_discover=False,
    )
    assert r.get("success")

    agent = HermusAgent(model="custom/test-model", mode="chat")
    res = agent.chat("hello there")
    assert "MOCK REPLY" in res.get("response", "")
    assert LOGS and LOGS[-1]["model"] == "test-model"
    assert "test-key-123" in LOGS[-1]["auth"]


def test_gateway_agent_cache_respects_model_change(mock_server, clean_keys):
    from core.multi_key import multi_key_manager
    from gateway.gateway import get_agent_for_user

    multi_key_manager.add_key(
        "custom",
        "test-key-123",
        name="mycustom",
        base_url=mock_server["base"],
        default_model="test-model",
        auto_discover=False,
    )

    a1 = get_agent_for_user("dashboard", "u1", model="custom/test-model", mode="chat")
    a2 = get_agent_for_user("dashboard", "u1", model="custom/test-model-2", mode="chat")
    # Switching the model must create a fresh agent (old bug: cached forever)
    assert a1 is not a2
    assert a2.model_name == "custom/test-model-2"
    res = a2.chat("hello again")
    assert "MOCK REPLY" in res.get("response", "")


def test_default_chat_falls_back_to_custom_key(mock_server, clean_keys):
    from core.multi_key import multi_key_manager
    from core.cache import clear_all_caches
    from core.agent import HermusAgent

    # Avoid cached ollama responses from other tests
    clear_all_caches()

    multi_key_manager.add_key(
        "custom",
        "test-key-123",
        name="mycustom",
        base_url=mock_server["base"],
        default_model="test-model",
        auto_discover=False,
    )
    # Default model is ollama (not running) — must fall back to the custom key.
    # (model=None resolves to config.model, which other tests mutate; pin it.)
    agent = HermusAgent(model="ollama/llama3.1:8b", mode="chat")
    res = agent.chat("unique fallback probe 987123")
    assert "MOCK REPLY" in res.get("response", "")
    assert "Ollama not running" not in res.get("response", "")


def test_unknown_provider_uses_custom_key_pool(mock_server, clean_keys):
    from core.multi_key import multi_key_manager
    from core.providers import parse_model_ref

    multi_key_manager.add_key(
        "custom",
        "test-key-123",
        name="mycustom",
        base_url=mock_server["base"],
        default_model="test-model",
        auto_discover=False,
    )
    # Unknown provider should not be routed to ollama anymore
    assert parse_model_ref("kimi/moonshot-v1") == ("kimi", "moonshot-v1")


def test_full_endpoint_base_url(mock_server, clean_keys):
    from core.openai_compat import chat_completions

    resp = chat_completions(
        provider="custom",
        model="test-model",
        messages=[{"role": "user", "content": "x"}],
        api_key="test-key-123",
        base_url=mock_server["base"] + "/chat/completions",  # full endpoint
    )
    assert "MOCK REPLY" in (resp.content or "")


def test_custom_api_tools_in_chat_modes(mock_server, clean_custom_apis):
    from core.custom_api import custom_api_manager
    from core.agent import HermusAgent

    r = custom_api_manager.add_api(
        {
            "name": "WeatherLookup",
            "description": "Get weather for a city",
            "url": mock_server["base"] + "/weather?city={city}",
            "method": "GET",
            "parameters": {"city": "City name"},
        }
    )
    assert r.get("success")

    for mode in ("chat", "multi-chat", "agent"):
        agent = HermusAgent(model="mock/mock", mode=mode)
        names = [t.get("function", {}).get("name") for t in agent.tools]
        assert "weatherlookup" in names, f"custom API tool missing in {mode} mode"


def test_custom_api_added_later_is_picked_up(mock_server, clean_custom_apis):
    """A cached agent must see custom APIs added via Settings without restart."""
    from core.custom_api import custom_api_manager
    from core.agent import HermusAgent

    agent = HermusAgent(model="mock/mock", mode="chat")
    assert agent.tools == []

    custom_api_manager.add_api(
        {
            "name": "LaterApi",
            "description": "Added after agent creation",
            "url": mock_server["base"] + "/later",
            "method": "GET",
            "parameters": {},
        }
    )
    # Next chat turn reloads tools
    agent.chat("hello")
    names = [t.get("function", {}).get("name") for t in agent.tools]
    assert "laterapi" in names
