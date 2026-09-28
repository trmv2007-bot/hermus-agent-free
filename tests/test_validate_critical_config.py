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
    assert names == {"max_tool_steps", "directories_writable", "gateway_port_free"}
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


def test_the_port_check_probes_the_configured_port_not_a_literal(monkeypatch) -> None:
    """A check that fires on a port you never asked for is an outage, not a safety check.

    The check used to probe a hardcoded 8000. Run under uvicorn on any other
    port, the gateway would still die because something unrelated held 8000 --
    `uvicorn gateway.gateway:app --port 8077` never reached "startup complete".

    Asserted by recording which port was actually dialled, rather than by
    running a server: the gateway cannot bind 8000 in this test, and the
    behaviour under test is the argument, not the socket.
    """
    import socket as socket_mod

    import bootstrap

    probed: list[int] = []

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, _t):
            pass

        def connect_ex(self, addr):
            probed.append(addr[1])
            return 1  # looks free

    # bootstrap does `import socket` inside the function, so the stdlib module
    # attribute is the only seam there is.
    monkeypatch.setattr(socket_mod, "socket", FakeSocket)

    # Same conditions the other tests use to get past the earlier checks --
    # otherwise validation exits before the port probe and the assertion below
    # would pass vacuously.
    import core.config as config_module

    monkeypatch.setattr(config_module.config, "model", "mock/mock", raising=False)
    monkeypatch.setattr(config_module.config, "gateway_port", 8077, raising=False)

    bootstrap.validate_critical_config(skip_validation=False, check_port=True)

    assert probed, "no port was probed at all"
    assert 8000 not in probed, f"probed the hardcoded 8000 instead of the configured port: {probed}"
    assert 8077 in probed, f"did not probe the configured port: {probed}"


def test_the_port_check_can_be_skipped_for_an_already_bound_process(monkeypatch) -> None:
    """Under uvicorn the bind already happened; re-checking is a race, not a guard.

    The lifespan runs after the socket is bound, so a busy configured port is
    not a reason to exit -- the process is the thing holding it.
    """
    import bootstrap

    results = bootstrap.validate_critical_config(skip_validation=False, check_port=False)
    assert results.get("skipped") is None
    checks = results.get("checks", [])
    assert any(c.get("name", "").startswith("gateway_port_free") for c in checks), checks
