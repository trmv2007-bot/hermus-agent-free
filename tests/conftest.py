"""Shared test fixtures.

The autonomy control plane intentionally records "powers Hermus would need"
into the human-readable capability ledger (Red Line 11) whenever a red or
ungranted-yellow action is checked (``PermissionManager.check``) or an unknown
tool is requested (``ToolGateway``). Those checks happen all over the suite, so
without a redirect the tests would append rows to the tracked
``CAPABILITY_LEDGER.md`` in the repository and leave the working tree dirty.

This fixture redirects the runtime ``get_capability_ledger`` seam to a
session-scoped temp file so test side effects never touch the real ledger, while
tests that explicitly construct ``CapabilityLedger(tmp_path / ...)`` keep using
their own path.
"""

from __future__ import annotations

import pytest

from core.capability_ledger import CapabilityLedger


@pytest.fixture(scope="session")
def _ledger_tmp_path(tmp_path_factory: pytest.TempPathFactory):
    return tmp_path_factory.mktemp("capability_ledger") / "CAPABILITY_LEDGER.md"


@pytest.fixture(autouse=True)
def _redirect_capability_ledger(_ledger_tmp_path, monkeypatch):
    ledger_path = _ledger_tmp_path

    def _get_ledger(path=None):
        return CapabilityLedger(path or ledger_path)

    monkeypatch.setattr("core.capability_ledger.get_capability_ledger", _get_ledger)
