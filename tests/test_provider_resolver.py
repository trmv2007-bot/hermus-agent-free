"""Tests for the unified provider/credential resolver.

The core regression this guards: a provider configured only in ``.env``
(OPENROUTER_API_KEY, GEMINI_API_KEY, NVIDIA_API_KEY, ...) must be visible to
the fallback, fleet and auto-selection paths even when nothing was added to the
multikey store.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_env_providers_visible_without_stored_keys(tmp_path, monkeypatch):
    import core.multi_key as mk
    from core.provider_resolver import list_available_providers

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setattr(mk.multi_key_manager, "db_path", tmp_path / "keys.json")

    providers = {p["provider"]: p for p in list_available_providers()}

    openrouter = providers["openrouter"]
    assert openrouter["configured"] is True
    assert openrouter["credential_source"] == "env"
    assert openrouter["has_credentials"] is True
    assert openrouter["supports_tools"] is True
    assert openrouter["models_available"] is True

    gemini = providers["gemini"]
    assert gemini["configured"] is True
    assert gemini["credential_source"] == "env"

    # A provider we never configured stays visibly known-but-not-configured.
    assert providers["groq"]["configured"] is False


def test_first_available_bundle_finds_env_credential(tmp_path, monkeypatch):
    import core.multi_key as mk
    from core.multi_key import MultiKeyManager

    monkeypatch.setenv("GROQ_API_KEY", "gsk-env-test")
    monkeypatch.setenv("HF_TOKEN", "hf-env-test")
    monkeypatch.setattr(mk.multi_key_manager, "db_path", tmp_path / "keys.json")

    mgr = MultiKeyManager(db_path=str(tmp_path / "keys2.json"))

    bundle = mgr.first_available_bundle(require_tools=True)
    assert bundle is not None
    assert bundle["provider"] == "groq"
    assert bundle["key"] == "gsk-env-test"
    assert bundle["base_url"] == "https://api.groq.com/openai/v1"

    # Non-tool providers are skipped when tools are required, even if they are
    # the only *credential* present.
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.setenv("HF_TOKEN", "hf-env-test")
    bundle = mgr.first_available_bundle(require_tools=True)
    assert bundle is None

    # But they remain an acceptable fallback for a tool-less chat.
    bundle = mgr.first_available_bundle(require_tools=False)
    assert bundle is not None
    assert bundle["provider"] in ("hf", "huggingface")


def test_fallback_recovers_without_depending_on_config_default(monkeypatch):
    """FreeLLM must fall back even when an explicit non-default model was used."""
    from core.provider_resolver import select_usable_bundle

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-recovery")
    bundle = select_usable_bundle(require_tools=True)
    assert bundle is not None
    assert bundle["provider"] == "openrouter"

    # Explicitly use a non-default model/provider: fallback should still find
    # the .env-only OpenRouter credential. This is the exact failure mode where
    # the old code required ``self.model == config.model``.
    monkeypatch.setenv("HERMUS_MODEL", "openrouter/auto")
    assert bundle["provider"] == "openrouter"


def test_fallback_uses_env_provider_for_explicit_non_default_model(monkeypatch):
    """Tool fallback must not depend on ``self.model == config.model``."""
    from types import SimpleNamespace
    import core.openai_compat as compat
    import core.multi_key as mk
    from core.llm import FreeLLM

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-recovery")
    monkeypatch.setenv("HERMUS_MODEL", "ollama/llama3.1:8b")
    monkeypatch.setattr(mk.multi_key_manager, "db_path", "/tmp/nonexistent-keys.json")

    calls = {}

    def fake_chat(provider, model, messages, api_key, base_url, tools=None, timeout=None, **kwargs):
        calls["provider"] = provider
        calls["model"] = model
        calls["api_key"] = api_key
        calls["base_url"] = base_url
        calls["tools"] = bool(tools)
        return SimpleNamespace(
            content="recovered",
            tool_calls=[],
            usage={"total_tokens": 3},
            latency_ms=1,
            headers={},
        )

    monkeypatch.setattr(compat, "chat_completions", fake_chat)

    # Explicit known provider + model that is NOT the configured default.
    llm = FreeLLM("nvidia/nvidia/llama-3.3-nemotron-super-49b-v1.5")
    resp = llm.chat(
        [{"role": "user", "content": "search and summarize"}],
        tools=[{"type": "function", "function": {"name": "web_search", "parameters": {}}}],
    )
    assert resp.content == "recovered"
    assert calls["provider"] == "openrouter"
    assert calls["api_key"] == "sk-or-recovery"
    assert calls["tools"] is True
    assert llm.last_fallback and llm.last_fallback["to_provider"] == "openrouter"


def test_model_fleet_lists_env_provider_workers(tmp_path, monkeypatch):
    import core.multi_key as mk
    from core.model_fleet import _available_workers

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fleet")
    monkeypatch.setattr(mk.multi_key_manager, "db_path", tmp_path / "keys.json")

    workers = _available_workers(limit=16)
    env_workers = [w for w in workers if w.get("provider") == "openrouter"]
    assert env_workers, "model fleet did not discover .env-configured provider"
    assert env_workers[0]["key"] == "sk-or-fleet"
    assert env_workers[0]["model"] == "openrouter/auto"


def test_select_compatible_model_uses_env_provider(monkeypatch):
    from core.model_capabilities import select_compatible_model

    monkeypatch.setenv("HERMUS_CAPABILITY_PROBE", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-select-test")

    model, info = select_compatible_model(["tools"])
    assert model is not None
    assert "gemini" in info["reports"] or (model or "").startswith("gemini/")


def test_diagnose_reports_no_tool_capable_cleanly(tmp_path, monkeypatch):
    import core.multi_key as mk
    from core.provider_resolver import diagnose

    # Only a tool-incapable provider is configured.
    monkeypatch.setenv("HF_TOKEN", "hf-token")
    monkeypatch.setattr(mk.multi_key_manager, "db_path", tmp_path / "keys.json")

    diag = diagnose(require_tools=True)
    assert diag["ok"] is False
    # Local/retired providers do not make an *API* fallback available, but a
    # tool-incapable HF credential must still be reported as configured.
    assert "huggingface" in [p["provider"] for p in diag["configured"]]
    assert "huggingface" not in diag["tools_capable"]
