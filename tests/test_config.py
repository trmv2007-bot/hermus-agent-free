"""Foundation: typed settings (core/config.py BaseSettings migration).

Pins the exact legacy semantics: env names, defaults, boolean-flag quirks,
CSV splitting, and the singleton behavior — plus the new capabilities (a
fresh ``Config()`` honors the current environment; invalid values raise a
``ValidationError`` instead of crashing the import with a bare ValueError).
"""

import pytest


def test_singleton_exposes_all_settings():
    from core.config import Config, config

    assert len(Config.model_fields) == 179
    assert config.model
    assert isinstance(config.nollama_port, int)
    assert isinstance(config.presence_enabled, bool)
    assert isinstance(config.voice_wake_aliases, list)


def test_fresh_config_honors_current_environment(monkeypatch):
    from core.config import Config

    monkeypatch.setenv("HERMUS_MODEL", "mock/mock")
    monkeypatch.setenv("HERMUS_NOLLAMA_PORT", "8011")
    monkeypatch.setenv("HERMUS_QUEUE_ENABLED", "0")
    fresh = Config()
    assert fresh.model == "mock/mock"
    assert fresh.nollama_port == 8011
    assert fresh.step_budget_full is False


def test_env_flag_legacy_semantics(monkeypatch):
    """``not in ("0", "false", "False")`` — including its quirks."""
    from core.config import Config

    for raw, expected in [
        ("1", True),
        ("0", False),
        ("false", False),
        ("False", False),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("2", True),
        ("", True),  # quirk: empty string is truthy, same as before
    ]:
        monkeypatch.setenv("HERMUS_QUEUE_ENABLED", raw)
        assert Config().gateway_queue_enabled is expected, raw


def test_csv_settings_drop_blank_entries(monkeypatch):
    from core.config import Config

    monkeypatch.setenv("HERMUS_VOICE_WAKE_ALIASES", " jervis , ,jarviss ,")
    assert Config().voice_wake_aliases == ["jervis", "jarviss"]
    monkeypatch.setenv("HERMUS_WEB_ALLOWED_DOMAINS", "")
    assert Config().web_allowed_domains == []


def test_ack_mode_normalized(monkeypatch):
    from core.config import Config

    monkeypatch.setenv("HERMUS_VOICE_ACK_MODE", "LIVE")
    assert Config().voice_ack_mode == "live"
    monkeypatch.setenv("HERMUS_VOICE_ACK_MODE", "")
    assert Config().voice_ack_mode == "canned"


def test_non_prefixed_provider_keys_keep_exact_names(monkeypatch):
    from core.config import Config

    monkeypatch.setenv("GROQ_API_KEY", "real_key")
    monkeypatch.setenv("HERMUS_GROQ_API_KEY", "must_be_ignored")
    fresh = Config()
    assert fresh.groq_api_key == "real_key"


def test_env_lookup_is_case_sensitive(monkeypatch):
    from core.config import Config

    monkeypatch.setenv("HERMUS_MODEL", "mock/mock")
    monkeypatch.setenv("hermus_model", "must_be_ignored")
    assert Config().model == "mock/mock"


def test_invalid_values_raise_validation_error(monkeypatch):
    import pydantic

    from core.config import Config

    monkeypatch.setenv("HERMUS_NOLLAMA_PORT", "not-a-port")
    with pytest.raises(pydantic.ValidationError):
        Config()


def test_singleton_mutation_still_allowed_and_restorable():
    """Tests/tools flip knobs on the singleton; assignment keeps working."""
    from core.config import config

    old = config.step_budget_full
    try:
        config.step_budget_full = not old
        assert config.step_budget_full is (not old)
    finally:
        config.step_budget_full = old
