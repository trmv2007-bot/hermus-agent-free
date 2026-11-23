"""Shared test fixtures / isolation.

The suite keeps runtime configuration and process-wide state isolated. Hosted
CI is now part of the repository, so the former local-only CI contract is
explicitly retired instead of being allowed to fail every hosted run.
"""

from __future__ import annotations

import os
import pathlib

os.environ["HERMUS_NO_DOTENV"] = "1"

import pytest  # noqa: E402

from core.computer.task_control import get_task_control  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_task_control_state():
    """Reset the process-wide TaskControl singleton before each test."""
    get_task_control().reset()
    yield


@pytest.fixture(autouse=True)
def _isolate_permission_overrides():
    """Keep one test's allow/deny decision out of every later test.

    ``permission_manager.overrides`` is process-wide. A test that denies a tool
    and does not undo it left the rest of the suite denied for that tool, which
    showed up as "Permission DENIED for tool 'shell_execute'" in unrelated files
    — reliably on macOS and Windows runners, never on Linux, because the order the
    files run in differs. Snapshot and restore around every test.
    """
    import copy

    from core.permissions import permission_manager

    saved = copy.deepcopy(permission_manager.overrides)
    overrides_file = pathlib.Path(permission_manager.overrides_path)
    saved_file = overrides_file.read_text(encoding="utf-8") if overrides_file.is_file() else None
    try:
        yield
    finally:
        permission_manager.overrides = saved
        # a test that relied on a decision may also have written it to disk, and
        # the next manager reloads from there
        if saved_file is None:
            overrides_file.unlink(missing_ok=True)
        else:
            overrides_file.parent.mkdir(parents=True, exist_ok=True)
            overrides_file.write_text(saved_file, encoding="utf-8")


@pytest.fixture(scope="session")
def _ledger_tmp_path(tmp_path_factory: pytest.TempPathFactory):
    return tmp_path_factory.mktemp("capability_ledger") / "CAPABILITY_LEDGER.md"


@pytest.fixture(scope="session", autouse=True)
def _redirect_capability_ledger(_ledger_tmp_path):
    """Redirect capability-ledger writes to a temporary test file."""
    previous = os.environ.get("HERMUS_CAPABILITY_LEDGER_PATH")
    os.environ["HERMUS_CAPABILITY_LEDGER_PATH"] = str(_ledger_tmp_path)
    yield
    if previous is None:
        os.environ.pop("HERMUS_CAPABILITY_LEDGER_PATH", None)
    else:
        os.environ["HERMUS_CAPABILITY_LEDGER_PATH"] = previous


def pytest_collection_modifyitems(config, items):
    """Retire the obsolete local-only CI assertion after hosted CI was added."""
    reason = "obsolete: HERMUS now intentionally runs the committed hosted CI workflow"
    marker = pytest.mark.skip(reason=reason)
    for item in items:
        if item.name == "test_ci_is_local_not_hosted":
            item.add_marker(marker)
