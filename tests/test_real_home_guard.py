"""The suite refuses to write into a person's own HERMUS home.

These are not style checks. On 2026-11-23 the browser tests wrote into
``~/.hermus`` and a real ``onboarding.json`` was destroyed, so the guard has to be
proved the way a guard is proved: it must fail when pointed at the real home, pass
when pointed at a temporary one, and be wired into a real pytest session rather
than merely available.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _real_home_guard as guard
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_real_home_is_a_profile_the_product_owns():
    assert guard.product_home() == (Path.home() / ".hermus").resolve()
    assert guard.real_home_hit(Path.home()) is not None, "the profile root itself is as bad to write into as ~/.hermus"


def test_a_path_inside_the_real_home_is_caught():
    assert guard.real_home_hit(Path.home() / ".hermus") is not None
    assert guard.real_home_hit(Path.home() / ".hermus" / "onboarding.json") is not None
    assert guard.real_home_hit(Path.home()) is not None


def test_a_temporary_home_and_the_windows_temp_tree_are_left_alone(tmp_path):
    """pytest's own temp tree sits under the profile on Windows; that is fine."""
    assert guard.real_home_hit(tmp_path) is None


def test_the_guard_refuses_the_real_home_by_name():
    with pytest.raises(pytest.UsageError) as refusal:
        guard.refuse_real_home(str(Path.home() / ".hermus"), "HERMUS_HOME")
    assert "real user home" in str(refusal.value)
    assert "HERMUS_HOME" in str(refusal.value)


def test_the_guard_allows_a_temporary_home(tmp_path):
    assert guard.refuse_real_home(tmp_path, "HERMUS_HOME") == tmp_path.resolve()


def test_the_override_is_the_only_way_past_it(monkeypatch):
    monkeypatch.setenv(guard.ALLOW_REAL_HOME, "1")
    assert guard.refuse_real_home(Path.home() / ".hermus", "HERMUS_HOME") == (Path.home() / ".hermus").resolve()


def test_a_test_home_cannot_escape_the_temp_tree(tmp_path_factory, tmp_path):
    """A fixture handed a path outside the session temp root is refused."""
    with pytest.raises(AssertionError, match="escaped the session temp root"):
        guard.temp_home(tmp_path_factory, Path.home() / ".hermus")
    inside = guard.temp_home(tmp_path_factory, tmp_path_factory.mktemp("guarded") / "home")
    assert str(inside).startswith(str(Path(tmp_path_factory.getbasetemp()).resolve()))


def _collect(env_home: str) -> subprocess.CompletedProcess[str]:
    """Collect one cheap test file under a given home, so a session really starts."""
    env = {**os.environ, "HERMUS_HOME": env_home, "PYTHONPATH": str(ROOT), "HERMUS_NO_DOTENV": "1"}
    env.pop(guard.ALLOW_REAL_HOME, None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_config.py", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_a_session_pointed_at_the_real_home_never_starts():
    result = _collect(str(Path.home() / ".hermus"))
    assert result.returncode != 0, "a session on the real home must not even collect"
    assert "real user home" in result.stdout + result.stderr


def test_a_session_pointed_at_a_temporary_home_starts(tmp_path):
    result = _collect(str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
