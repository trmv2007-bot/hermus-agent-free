"""Tests for bootstrap.validate_critical_config (P0-2) — fail-fast startup config.

The validator must report every check (name, ok, message), never fabricate
success, and honor the skip flag. All checks are offline: Ollama is probed on
loopback only, port probing is a connect_ex on 127.0.0.1, and directory writes
go to tmp_path via the workspace seam.
"""

from __future__ import annotations

import pytest

import bootstrap


def _has_check(results: dict, name: str) -> bool:
    return any(c.get("name") == name for c in results.get("checks", []))


def test_skip_validation_returns_skipped_without_checks():
    results = bootstrap.validate_critical_config(skip_validation=True)
    assert results.get("skipped") is True
    assert results.get("checks", []) == []


def test_validation_reports_all_four_checks(monkeypatch):
    # Force a non-ollama model so the Ollama probe is not required to pass.
    import core.config as config_module

    monkeypatch.setattr(config_module.config, "model", "mock/mock", raising=False)
    # Do not inherit another test module singleton mutation; this check owns
    # the healthy default budget explicitly.
    monkeypatch.setattr(config_module.config, "max_tool_steps", 32, raising=False)
    results = bootstrap.validate_critical_config()
    names = {c["name"] for c in results["checks"]}
    assert names == {"max_tool_steps", "directories_writable", "port_8000_free"}
    # Every check carries an actionable message.
    assert all(isinstance(c.get("message"), str) and c["message"] for c in results["checks"])
    # ok reflects the checks; errors is present either way.
    assert results["ok"] == (len(results["errors"]) == 0)


def test_validation_fails_low_step_budget(monkeypatch, capsys):
    import core.config as config_module

    monkeypatch.setattr(config_module.config, "model", "mock/mock", raising=False)
    monkeypatch.setattr(config_module.config, "max_tool_steps", 2, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        bootstrap.validate_critical_config()
    assert excinfo.value.code == 1
    # The failure is actionable: names the check, the value and the minimum.
    err = capsys.readouterr().err
    assert "max_tool_steps" in err
    assert "minimum required" in err


def test_validation_ollama_probe_runs_for_ollama_models(monkeypatch, capsys):
    """model=ollama/* must trigger the reachability probe (loopback, offline-safe)."""
    import core.config as config_module

    monkeypatch.setattr(config_module.config, "model", "ollama/llama3.1:8b", raising=False)
    monkeypatch.setattr(config_module.config, "ollama_base_url", "http://127.0.0.1:9", raising=False)
    with pytest.raises(SystemExit):
        bootstrap.validate_critical_config()
    # Nothing listens on port 9 — the probe must honestly report unreachable
    # with the URL in the message, never a fabricated success.
    err = capsys.readouterr().err
    assert "ollama_reachable" in err
    assert "http://127.0.0.1:9" in err
