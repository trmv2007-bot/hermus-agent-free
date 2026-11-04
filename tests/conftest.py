"""Shared test fixtures / isolation.

Three independent isolation concerns are handled here:

0. ``core/config.py`` calls ``load_dotenv`` at import time, so a developer's
   personal ``.env`` (raised step budgets, doctor caps, verify thresholds, ...)
   would otherwise change the values the suite asserts. ``HERMUS_NO_DOTENV=1``
   is exported at the very top of this module, before any ``core`` import, so
   tests see declared defaults instead of whoever's local config.

1. ``core.computer.task_control.task_control`` is a process-wide singleton
   holding global interrupt (emergency-stop) and task-registration state.
   Without resetting it between tests, an emergency stop or a leaked task from
   one test can bleed into a later one (e.g. ``VisualStateMachine.run`` aborting
   on a stale emergency-stop flag, which is order-dependent and appears only
   under a broad combined order). The autouse fixture below clears that
   singleton before every test so each test starts with a clean slate — a
   test-isolation root-cause fix, not a mock.

2. The autonomy control plane intentionally records "powers Hermus would need"
   into the human-readable capability ledger (Red Line 11) whenever a red or
   ungranted-yellow action is checked (``PermissionManager.check``) or an
   unknown tool is requested (``ToolGateway``). Those checks happen all over the
   suite, so without a redirect the tests would append rows to the tracked
   ``CAPABILITY_LEDGER.md`` in the repository and leave the working tree dirty.
   The fixture below redirects the runtime ``get_capability_ledger`` seam to a
   session-scoped temp file so test side effects never touch the real ledger,
   while tests that explicitly construct ``CapabilityLedger(tmp_path / ...)``
   keep using their own path.

3. Operator state under ``data/`` is not test scratch. ``config.resolve_path``
   has no redirect seam (``base_dir`` is the repo root), so anything reaching a
   singleton — ``multi_key_manager``, ``custom_api_manager``, the counsel
   constitution — reads and writes the real files. One fixture used to delete
   ``data/api_keys.json`` for good. The session fixture below snapshots the
   credential stores and puts them back if a test left them missing or altered,
   so an isolation mistake costs the operator nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

# Must happen before ANY core import: core/config.py calls load_dotenv() at
# import time, so a developer's personal .env would otherwise change the values
# the suite asserts (step budgets, doctor caps, verify thresholds, ...). This
# makes the suite deterministic regardless of local config.
os.environ["HERMUS_NO_DOTENV"] = "1"

# Ambient provider credentials are the developer's, not the suite's. A machine
# that exports OPENAI_API_KEY (or an OpenAI-compatible base URL) silently turned
# offline tests into live provider calls: a fallback test hit the real endpoint,
# got a 401, and failed for reasons unrelated to the code. Clear them before any
# core import so ``Config`` and the key registries start from a known state.
# Tests that need a provider set one explicitly with ``monkeypatch.setenv``.
_AMBIENT_PROVIDER_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MISTRAL_API_KEY",
    "NVIDIA_API_KEY",
    "TOGETHER_API_KEY",
    "HF_TOKEN",
)
for _name in _AMBIENT_PROVIDER_VARS:
    os.environ.pop(_name, None)

import pytest  # noqa: E402

from core.computer.task_control import get_task_control  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_task_control_state():
    """Reset the process-wide TaskControl singleton before each test."""
    get_task_control().reset()
    yield


@pytest.fixture(autouse=True)
def _clear_stale_queue_drain():
    """A test that drained the shared JobQueue must not poison later tests.

    Standalone delegation drives the singleton queue with ``asyncio.run`` and
    stops it afterwards, which leaves ``_draining`` set. In production the
    gateway owns a running queue for the process lifetime; in the suite the
    next test would otherwise be refused with "gateway is shutting down".
    """
    from gateway.queue import job_queue

    job_queue._draining = False
    yield


@pytest.fixture(scope="session", autouse=True)
def _protect_operator_stores():
    """Snapshot the credential stores so no test can lose them.

    Restores byte-for-byte only when a test left the file missing or changed,
    so tests that legitimately read these stores are untouched.
    """
    root = Path(__file__).resolve().parent.parent
    stores = ("data/api_keys.json", "data/custom_apis.json")
    saved = {}
    for rel in stores:
        target = root / rel
        if target.exists():
            saved[rel] = (target, target.read_bytes())
    yield
    for target, blob in saved.values():
        try:
            if target.exists() and target.read_bytes() == blob:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        except OSError:
            pass


@pytest.fixture(scope="session")
def _ledger_tmp_path(tmp_path_factory: pytest.TempPathFactory):
    return tmp_path_factory.mktemp("capability_ledger") / "CAPABILITY_LEDGER.md"


@pytest.fixture(scope="session", autouse=True)
def _redirect_capability_ledger(_ledger_tmp_path):
    """Redirect the capability ledger to a temp file for the whole session.

    Setting the env var (rather than monkeypatching) also covers tests that
    shell out to subprocesses (e.g. the delegation worker tests): the child
    inherits ``os.environ``, so its ToolGateway unknown-tool path writes to the
    temp ledger instead of the tracked ``CAPABILITY_LEDGER.md``.
    """
    previous = os.environ.get("HERMUS_CAPABILITY_LEDGER_PATH")
    os.environ["HERMUS_CAPABILITY_LEDGER_PATH"] = str(_ledger_tmp_path)
    yield
    if previous is None:
        os.environ.pop("HERMUS_CAPABILITY_LEDGER_PATH", None)
    else:
        os.environ["HERMUS_CAPABILITY_LEDGER_PATH"] = previous
