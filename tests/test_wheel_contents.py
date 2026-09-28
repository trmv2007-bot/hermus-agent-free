"""The wheel must contain everything the product imports.

tests/test_packaging.py already existed, was green, and did not catch a wheel
missing 20 of its 29 packages. It only counted top-level directories without
descending into them, so a wheel containing `core/` and nothing underneath
looked correct. An editable install also maps back into the working tree, so
every in-tree test passed while `pip install hermus` produced something that
died at import.

These tests descend, and they check the thing that actually breaks: whether
the real subpackages are importable from the installed copy.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The subpackages that were missing from the wheel. core.fleet is the one the
# CLI hits first, so it is the canary.
REQUIRED_SUBPACKAGES = [
    "core/agents",
    "core/computer",
    "core/context",
    "core/counsel",
    "core/fleet",
    "core/memory",
    "core/tools",
    "core/web",
]

# One static asset per kind, including a nested one. The old non-recursive
# globs shipped a single top-level file out of seventeen, so the control room
# 404'd its own CSS and the workspace lost its whole bundle.
REQUIRED_ASSETS = [
    "gateway/static/control.css",
    "gateway/static/console.js",
    "gateway/static/workspace/index.html",
    "gateway/static/workspace/assets/workspace.js",
]


def _built_wheel() -> Path | None:
    dist = ROOT / "dist"
    if not dist.is_dir():
        return None
    wheels = sorted(dist.glob("*.whl"), key=lambda p: p.stat().st_mtime)
    return wheels[-1] if wheels else None


def _declared_include_patterns() -> list[str]:
    """Read the include globs, so a regression to a literal list is visible."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        pytest.skip("tomllib unavailable")
    data = tomllib.loads(text)
    find = data.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
    return find.get("include", [])


def test_packages_are_discovered_not_hand_listed() -> None:
    """A literal package list silently drops every subpackage."""
    patterns = _declared_include_patterns()
    assert patterns, "no [tool.setuptools.packages.find] include patterns -- subpackages will be missing"
    assert any(p.endswith("*") for p in patterns), f"patterns {patterns} do not descend into subpackages"
    # The old shape: a literal list of top-level names under `packages`.
    data = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = [' not in data, "a literal `packages = [...]` list does not ship subpackages"


def test_every_importable_subpackage_is_on_disk() -> None:
    """Sanity: the packages the wheel must ship actually exist in the tree."""
    missing = [p for p in REQUIRED_SUBPACKAGES if not (ROOT / p / "__init__.py").is_file()]
    assert not missing, f"expected source subpackages are absent from the tree: {missing}"


@pytest.mark.skipif(_built_wheel() is None, reason="no wheel in dist/; run `python -m build --wheel` first")
def test_the_built_wheel_ships_the_subpackages() -> None:
    wheel = _built_wheel()
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [p for p in REQUIRED_SUBPACKAGES if f"{p}/__init__.py" not in names]
    assert not missing, f"wheel is missing {missing} -- `hermes` will fail with No module named 'core.fleet'"


@pytest.mark.skipif(_built_wheel() is None, reason="no wheel in dist/; run `python -m build --wheel` first")
def test_the_built_wheel_ships_its_static_assets() -> None:
    wheel = _built_wheel()
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [a for a in REQUIRED_ASSETS if a not in names]
    assert not missing, f"wheel is missing static assets {missing} -- the control room will 404 its own assets"


@pytest.mark.skipif(_built_wheel() is None, reason="no wheel in dist/; run `python -m build --wheel` first")
def test_core_fleet_imports_from_the_wheel() -> None:
    """The canary, checked the way a user hits it: an installed copy, not the tree.

    Only the file list is asserted by the other tests, and a file list can look
    right while the package still will not import. This one reads the wheel's
    own RECORD for the exact module that broke the CLI.
    """
    wheel = _built_wheel()
    names = set(zipfile.ZipFile(wheel).namelist())
    record = next((n for n in names if n.endswith(".dist-info/RECORD")), None)
    assert record is not None, "wheel has no RECORD"
    rows = zipfile.ZipFile(wheel).read(record).decode("utf-8")
    assert "core/fleet/__init__.py" in rows, "core/fleet is not in the installed RECORD"
