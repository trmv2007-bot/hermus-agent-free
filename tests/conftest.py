"""Shared test fixtures / isolation.

The suite keeps runtime configuration and process-wide state isolated. Hosted
CI is now part of the repository, so the former local-only CI contract is
explicitly retired instead of being allowed to fail every hosted run.
"""

from __future__ import annotations

import os

os.environ["HERMUS_NO_DOTENV"] = "1"

import pytest  # noqa: E402

from core.computer.task_control import get_task_control  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_task_control_state():
    """Reset the process-wide TaskControl singleton before each test."""
    get_task_control().reset()
    yield


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
