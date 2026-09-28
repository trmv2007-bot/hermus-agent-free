"""Packaging is a contract, not a convention.

The failure this file exists to prevent: `pip install -e .` works on day one,
someone adds a dependency to requirements.txt (or a top-level package), and the
installable artifact silently drifts from the repo until a fresh clone on a new
machine fails with ImportError.

These are cheap, offline, and read only pyproject/requirements/the package tree.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _requirement_names(text: str) -> set[str]:
    """Package names from a requirements-style file, comments/blanks dropped."""
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def test_pyproject_declares_build_system_and_metadata():
    data = _pyproject()
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"
    project = data["project"]
    for key in ("name", "version", "description", "requires-python", "readme"):
        assert project.get(key), f"project.{key} is required for a publishable package"
    assert re.match(r"^\d+\.\d+\.\d+", project["version"]), "version must be semver-ish"


def test_hermes_console_script_is_importable_and_callable():
    data = _pyproject()
    scripts = data["project"].get("scripts", {})
    assert "hermes" in scripts, "the product entrypoint must be a console script"
    target = scripts["hermes"]
    module_name, _, attr = target.partition(":")
    module = __import__(module_name, fromlist=[attr])
    entry = getattr(module, attr)
    assert callable(entry), f"{target} is not callable"


def test_pytest_config_lives_in_one_place():
    """pytest.ini silently wins over pyproject and hides lost config."""
    assert not (ROOT / "pytest.ini").exists(), "config belongs in pyproject [tool.pytest.ini_options]"
    cfg = _pyproject().get("tool", {}).get("pytest", {}).get("ini_options", {})
    assert cfg.get("testpaths") == ["tests"]
    assert "pythonpath" in cfg, "a bare checkout must import core without PYTHONPATH"
    assert {"fast", "slow", "perf"} <= set(cfg.get("markers", [{}])[0] if False else
                                            [m.split(":")[0].strip() for m in cfg.get("markers", [])])


def test_declared_dependencies_cover_requirements_runtime():
    data = _pyproject()
    declared = {re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0].strip().lower().replace("_", "-")
                for d in data["project"]["dependencies"]}
    required = _requirement_names((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    # requirements.txt also lists helpers that are optional or dev-only. Those
    # live in [project.optional-dependencies]; a name appearing in neither is
    # the actual drift this test exists to catch.
    optional = {re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0].strip().lower().replace("_", "-")
                for group in ("optional", "dev")
                for d in data["project"].get("optional-dependencies", {}).get(group, [])}
    missing = {name for name in required if name not in declared and name not in optional}
    assert not missing, (
        f"in requirements.txt but in neither dependencies nor optional-dependencies: {sorted(missing)}"
    )


def test_every_top_level_package_is_declared():
    """Every importable top-level package must reach the wheel.

    This used to compare the on-disk directories against a literal
    `packages = [...]` list, and it was green while the wheel was missing 20 of
    its 29 packages. Two blind spots in one assertion: setuptools does not
    recurse into a package merely because it is named, so an exact list of nine
    top-level names ships nine directories and none of their contents; and
    `p.is_dir()` on a flapping C: volume is not a reliable answer either.

    The invariant that actually matters is coverage: is every top-level package
    matched by an include pattern? tests/test_wheel_contents.py then checks the
    built artefact, because a correct-looking declaration still has to produce
    a correct wheel.
    """
    import fnmatch

    data = _pyproject()
    setuptools_cfg = data["tool"]["setuptools"]
    find = setuptools_cfg.get("packages", {}).get("find")
    assert find is not None, (
        r"no [tool.setuptools.packages.find]; a literal package list does not "
        r"ship subpackages and `hermes` will die on `No module named 'core.fleet'`"
    )

    patterns = find.get("include", [])
    on_disk = {p.name for p in ROOT.iterdir()
               if p.is_dir() and (p / "__init__.py").exists() and not p.name.startswith(".")}

    uncovered = {
        name for name in on_disk
        if not any(fnmatch.fnmatch(name, pat.rstrip("*")) for pat in patterns)
    }
    assert not uncovered, f"top-level packages no include pattern covers: {sorted(uncovered)}"


def test_version_file_matches_pyproject():
    version_file = ROOT / "VERSION"
    assert version_file.exists(), "VERSION is the single human-readable version source"
    assert version_file.read_text(encoding="utf-8").strip() == _pyproject()["project"]["version"]


def test_changelog_exists_and_mentions_current_version():
    version = _pyproject()["project"]["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert version in changelog, f"CHANGELOG.md has no entry for {version}"


@pytest.mark.parametrize("marker", ["fast", "slow", "perf"])
def test_lane_markers_are_registered_and_used(marker):
    cfg = _pyproject()["tool"]["pytest"]["ini_options"]
    registered = " ".join(cfg.get("markers", []))
    assert f"{marker}:" in registered


def _bootstrap_list(name: str) -> list:
    """Read a bootstrap list literal, bracket-aware.

    A regex like `\[.*?\]` stops at the first `]`, which lands inside
    "uvicorn[standard]" and silently truncates the list - the kind of test that
    passes because it compared the wrong data.
    """
    lines = (ROOT / "bootstrap.py").read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith(f"{name} = ["))
    except StopIteration:
        raise AssertionError(f"bootstrap.{name} not found - update this guard if it moved")
    body = []
    for line in lines[start + 1:]:
        if line.strip() == "]":
            break
        body.append(line)
    return re.findall(r'"([^"]+)"', chr(10).join(body))


def test_bootstrap_required_pip_list_is_parallel_to_imports() -> None:
    """REQUIRED_IMPORTS and REQUIRED_PIP are documented as parallel lists.

    Doctor checks the first; bootstrap installs the second. If they drift, Doctor
    demands an import the installer never provides and reports it as missing.
    """
    imports = _bootstrap_list("REQUIRED_IMPORTS")
    pips = _bootstrap_list("REQUIRED_PIP")
    assert len(imports) == len(pips), (
        f"REQUIRED_IMPORTS has {len(imports)} entries but REQUIRED_PIP has {len(pips)}"
    )


def _dist_name(spec: str) -> str:
    """Normalise a requirement to a bare distribution name.

    PEP 503 normalisation: drop extras, drop any version/specifier suffix,
    lowercase, and treat '_' and '.' as '-'. Both sides of every comparison in
    this file go through here, because normalising only one side turns
    'uvicorn[standard]' and 'prompt_toolkit' into phantom drift.
    """
    name = re.split(r"[<>=!~;\[ ]", spec, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def test_bootstrap_required_pip_is_installable_from_pyproject() -> None:
    """Every distribution bootstrap must install has to be a real dependency.

    Regression: scrapling sat in [project.optional-dependencies] while bootstrap
    demanded it, so a clean `pip install -e .` left 38 web tests failing and 6 of
    the 8 canonical web tools inert. An extra is a choice; REQUIRED_PIP is a contract.
    """
    data = _pyproject()
    declared = {_dist_name(d) for d in data["project"].get("dependencies", [])}
    missing = {n for n in (_dist_name(x) for x in _bootstrap_list("REQUIRED_PIP")) if n not in declared}
    assert not missing, (
        "bootstrap.REQUIRED_PIP entries missing from [project.dependencies]: "
        f"{sorted(missing)}"
    )
