"""Practical bootstrap/Doctor contract tests.

These tests keep installer failures honest without downloading packages: the
venv command and browser discovery are mocked deliberately, while the loopback
fixture test is live. The full setup smoke is run separately in CI/operator
verification because it needs a real .venv, Chromium and host libraries.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

import bootstrap
from core import diagnostics


def _fake_venv_python(vdir: Path) -> Path:
    path = vdir / "bin" / "python"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(sys.executable)
    return path


def test_broken_venv_is_preserved_before_fresh_creation(monkeypatch, tmp_path):
    vdir = tmp_path / ".venv"
    vdir.mkdir()
    user_file = vdir / "user-created-data.txt"
    user_file.write_text("preserve me", encoding="utf-8")
    # A present-but-broken executable exercises in-place repair before the
    # timestamped preservation fallback.
    broken_python = vdir / "bin" / "python"
    broken_python.parent.mkdir(parents=True)
    broken_python.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    broken_python.chmod(0o700)
    calls: list[tuple[Path, bool]] = []

    def fake_create(path: Path, base_python: Path | None, *, upgrade: bool = False):
        calls.append((base_python or Path(), upgrade))
        if upgrade:
            return False, "simulated broken in-place repair"
        _fake_venv_python(path)
        return True, "created"

    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "_create_venv", fake_create)
    result = bootstrap.ensure_venv(repair=True, base_python=Path("/selected/python"))

    assert result["repaired"] is True
    assert calls == [(Path("/selected/python"), True), (Path("/selected/python"), False)]
    backup = Path(result["backup"])
    assert (backup / user_file.name).read_text(encoding="utf-8") == "preserve me"
    assert (vdir / "bin" / "python").is_file()


def test_required_install_uses_selected_venv_interpreter(monkeypatch, tmp_path):
    selected = tmp_path / ".venv" / "bin" / "python"
    captured: list[list[str]] = []

    def fake_run(command, **kwargs):
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, "success", "")

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    result = bootstrap.install_required(selected)

    assert result["ok"] is True
    assert captured
    assert captured[0][0] == str(selected)
    assert captured[0][-2:] == ["-r", str(bootstrap.ROOT / "requirements.txt")]


def test_loopback_fixture_is_a_real_fetch_not_a_mock():
    with diagnostics._local_verification_page() as url:
        with urlopen(url, timeout=3) as response:  # noqa: S310 - deterministic loopback fixture
            body = response.read().decode("utf-8")
    assert response.status == 200
    assert "HERMUS_LOCAL_VERIFICATION_OK" in body


def test_mocked_missing_chromium_is_not_reported_as_verified(monkeypatch):
    monkeypatch.setattr("core.web.capabilities._playwright_chromium_path", lambda: None)
    chromium, navigation = diagnostics._verify_browser("http://127.0.0.1:9/", required=True)
    assert chromium["status"] == diagnostics.MISSING_BROKEN
    assert navigation["status"] == diagnostics.MISSING_BROKEN
    assert "launch was not attempted" in navigation["detail"]


def test_mocked_scrapling_fixture_failure_is_critical():
    result = diagnostics._verify_scrapling(None, required=True)
    assert result["status"] == diagnostics.MISSING_BROKEN
    assert result["required"] is True
    assert result["ok"] is False


def test_final_report_never_prints_ready_for_required_failure(capsys):
    report = {
        "platform": {"family": "test"},
        "overall": {"passed": 0, "total": 1, "required": False},
        "subsystems": {
            "dependencies": {
                "name": "Dependencies",
                "status": diagnostics.MISSING_BROKEN,
                "ok": False,
                "detail": "simulated pip failure",
                "hint": "fix it",
                "required": True,
            }
        },
    }
    bootstrap.print_installation_report(report)
    output = capsys.readouterr().out
    assert "HERMUS SETUP: INCOMPLETE — FIX REQUIRED" in output
    assert "HERMUS SETUP: READY" not in output
