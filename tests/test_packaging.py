"""The package contract: what a `pip install` has to carry to be usable.

Hermus reads its Control Room markup, red-line policy, API catalog and builtin
harness suites from `config.base_dir` — the repository root in a checkout, the
install root once pip-installed. A wheel that omits any of them installs an app
that starts and then fails at the first command, which is exactly what the
packaging did before these assertions existed.

Nothing here builds a wheel (CI should not). The checks are the three things
that silently break it: a missing console entry point, a data tree that is not
a package (setuptools cannot carry files for it), and a data tree the manifest
forgot.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]

#: Trees Hermus reads at runtime from `config.base_dir`, and one file each.
RUNTIME_DATA = {
    "gateway": "control.html",
    "policies": "red_lines.json",
    "resources": "public_apis_catalog.json",
    "harness": "suites/smoke.json",
}


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_console_script_is_declared():
    scripts = _pyproject()["project"].get("scripts") or {}
    assert scripts.get("hermus") == "hermus_cli:main", "a wheel without this installs nothing to run"


def test_runtime_dependencies_are_declared():
    deps = _pyproject()["project"].get("dependencies") or []
    joined = " ".join(deps).lower()
    for required in ("fastapi", "pydantic-settings", "uvicorn", "httpx"):
        assert required in joined, f"{required} is a runtime import, not a dev extra"


def test_every_runtime_data_tree_is_a_package_with_its_data():
    project = _pyproject()
    package_data = project["tool"]["setuptools"]["package-data"]
    included = " ".join(project["tool"]["setuptools"]["packages"]["find"]["include"])

    for tree, sample in RUNTIME_DATA.items():
        assert (ROOT / tree / "__init__.py").is_file(), f"{tree}/ must be a package or setuptools cannot ship its data files"
        assert (ROOT / tree / sample).is_file(), f"{tree}/{sample} is missing from the tree"
        assert tree in package_data, f"{tree} is a package but declares no package-data"
        assert f"{tree}*" in included, f"{tree}* is not in the find-packages include list"


def test_gateway_ships_its_control_room_assets():
    project = _pyproject()
    gateway_data = project["tool"]["setuptools"]["package-data"]["gateway"]
    patterns = " ".join(gateway_data)

    for needed in ("control.html", "static/"):
        assert needed in patterns, f"the wheel would ship a Control Room without {needed}"
    for asset in ("control.html", "static/control-room.js", "static/control.css"):
        assert (ROOT / "gateway" / asset).is_file(), f"gateway/{asset} does not exist"
