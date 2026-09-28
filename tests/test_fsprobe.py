"""The filesystem probe must agree with the volume, not with one stat call.

Measured on this machine, on a file Get-ChildItem -Recurse lists without
trouble: `Path.is_file()` returned False 40 times out of 40, and
`[System.IO.File]::Exists()` returned False, while enumeration found it every
time. A single stat is not evidence.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import fsprobe

ROOT = Path(__file__).resolve().parents[1]


def test_a_missing_file_is_reported_missing() -> None:
    assert fsprobe.file_exists(ROOT / "definitely-not-here-9f3a.onnx") is False


def test_a_present_file_is_reported_present() -> None:
    assert fsprobe.file_exists(ROOT / "pyproject.toml") is True


def test_a_directory_is_not_a_file() -> None:
    assert fsprobe.file_exists(ROOT / "core") is False
    assert fsprobe.dir_exists(ROOT / "core") is True


def test_a_present_file_survives_a_volatile_stat(monkeypatch) -> None:
    """The whole point: stat failing must not mean the file is absent.

    Patches `is_file` to return False for the first three attempts, exactly
    like the burst failure this module was written for. Enumeration, the
    fallback, still finds the real file.
    """
    real = ROOT / "pyproject.toml"
    calls = {"n": 0}

    def flaky(_self):
        calls["n"] += 1
        return False

    monkeypatch.setattr(Path, "is_file", flaky, raising=False)
    assert fsprobe.file_exists(real) is True, f"gave up after {calls['n']} stat calls"
    assert calls["n"] >= 2, "retried rather than trusting the first answer"


def test_the_probe_never_raises(monkeypatch) -> None:
    """A probe that throws answers a yes/no question with an exception."""

    def explode(_self):
        raise PermissionError("volume is having a moment")

    monkeypatch.setattr(Path, "is_file", explode, raising=False)
    monkeypatch.setattr(Path, "is_dir", explode, raising=False)
    monkeypatch.setattr(os, "scandir", lambda _p: (_ for _ in ()).throw(OSError("nope")))

    assert fsprobe.file_exists(ROOT / "pyproject.toml") is False
    assert fsprobe.dir_exists(ROOT / "core") is False


def test_the_real_voice_model_is_detected_when_present() -> None:
    """Regression guard for the reason this module exists.

    The Piper model was installed and the probe still reported the voice as
    unavailable, because it asked a flaky volume exactly once.
    """
    import re

    env = ROOT / ".env"
    if not env.is_file():
        pytest.skip("no .env in this checkout")

    m = re.search(r"^HERMUS_PIPER_MODEL=(.+)$", env.read_text(encoding="utf-8"), re.M)
    if not m:
        pytest.skip("HERMUS_PIPER_MODEL is not set")

    target = Path(m.group(1).strip())
    if not target.is_absolute():
        target = ROOT / target
    if not fsprobe.dir_exists(target.parent):
        pytest.skip("voice models are not staged in this checkout")

    assert fsprobe.file_exists(target) is True, f"{target} is staged but not detected"


def test_speech_uses_the_resilient_probe(monkeypatch) -> None:
    """A regression here silently disables the voice again."""
    import inspect

    from core import speech

    src = inspect.getsource(speech)
    assert "fsprobe.file_exists" in src, "the TTS availability probe is single-shot again"
    assert "Path(piper_model).expanduser().exists()" not in src, "the old single stat is back"
