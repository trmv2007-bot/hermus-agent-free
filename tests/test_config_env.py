"""The dashboard's Settings panel: the safe `.env` whitelist behind it.

The Control Room could observe every subsystem but not change the settings those
subsystems run on, so configuring anything meant editing `.env` by hand. These pin
the two rules that make the panel safe to expose: only whitelisted keys are
writable, and anything credential-shaped is refused no matter what else is asked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import config_env


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Point the settings file at a temp path and start from a clean environment."""
    config = config_env.config
    monkeypatch.setattr(type(config), "base_dir", property(lambda self: tmp_path), raising=False)
    for key in config_env.CHANGEABLE:
        monkeypatch.delenv(key, raising=False)
    return tmp_path / ".env"


def test_read_reports_every_changeable_setting(env_file):
    data = config_env.read_settings()
    assert data["file"].endswith(".env")
    assert {row["key"] for row in data["settings"]} == set(config_env.CHANGEABLE)
    assert all(row["source"] == "default" for row in data["settings"])
    assert data["restart_required"] is True


def test_write_creates_the_file_and_reads_back(env_file):
    result = config_env.write_setting("HERMUS_MODEL", "ollama/lfm2.5:8b")
    assert result["success"] is True
    assert result["restart_required"] is True
    assert env_file.read_text(encoding="utf-8").strip() == "HERMUS_MODEL=ollama/lfm2.5:8b"

    row = next(r for r in config_env.read_settings()["settings"] if r["key"] == "HERMUS_MODEL")
    assert row["value"] == "ollama/lfm2.5:8b"


def test_write_replaces_in_place_and_preserves_other_lines(env_file):
    env_file.write_text("# my keys\nOPENAI_API_KEY=sk-keep\nHERMUS_MODEL=old/model\n", encoding="utf-8")
    config_env.write_setting("HERMUS_MODEL", "ollama/lfm2.5:8b")

    text = env_file.read_text(encoding="utf-8")
    assert "# my keys" in text, "comments and unrelated keys must survive a dashboard write"
    assert "OPENAI_API_KEY=sk-keep" in text
    assert "HERMUS_MODEL=ollama/lfm2.5:8b" in text
    assert "old/model" not in text


def test_credentials_are_refused_even_if_they_were_listed(env_file, monkeypatch):
    monkeypatch.setitem(config_env.CHANGEABLE, "OPENAI_API_KEY", "would be very bad")
    with pytest.raises(config_env.SettingError):
        config_env.validate("OPENAI_API_KEY", "sk-does-not-matter")
    assert not env_file.exists(), "a refused write must not touch the file"


def test_unknown_keys_are_refused(env_file):
    with pytest.raises(config_env.SettingError) as excinfo:
        config_env.validate("PATH", "/usr/bin")
    assert "not a dashboard setting" in str(excinfo.value)


def test_closed_choice_settings_reject_free_text(env_file):
    assert config_env.validate("HERMUS_SANDBOX", "docker") == "docker"
    with pytest.raises(config_env.SettingError):
        config_env.validate("HERMUS_SANDBOX", "whatever-i-like")


def test_numeric_settings_require_a_number(env_file):
    assert config_env.validate("HERMUS_GATEWAY_PORT", "8000") == "8000"
    with pytest.raises(config_env.SettingError):
        config_env.validate("HERMUS_GATEWAY_PORT", "eight thousand")


def test_empty_value_is_refused(env_file):
    with pytest.raises(config_env.SettingError):
        config_env.validate("HERMUS_MODEL", "  ")


def test_path_is_under_the_repository_root(env_file):
    assert Path(config_env.read_settings()["file"]).parent == env_file.parent
