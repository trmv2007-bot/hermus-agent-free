"""Shared test fixtures / isolation.

The suite keeps runtime configuration and process-wide state isolated. Hosted
CI is now part of the repository, so the former local-only CI contract is
explicitly retired instead of being allowed to fail every hosted run.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ["HERMUS_NO_DOTENV"] = "1"

# Model availability is probed by real requests in production (see
# core/model_health.py). A test must never reach a provider, so probing is off
# for the whole suite; the tests that exercise it inject a fake transport and
# switch it back on themselves.
os.environ["HERMUS_MODEL_PROBE"] = "0"

import tempfile  # noqa: E402

import pytest  # noqa: E402
from _real_home_guard import refuse_real_home  # noqa: E402

# No test may run against the person's own installation. `_real_home_guard`
# refuses a session whose home resolves into it; this hands a bare `pytest` a
# temporary home as well, so the safe case needs no discipline from whoever runs
# it while the unsafe one is still refused in `pytest_sessionstart`.
if os.environ.get("HERMUS_ALLOW_REAL_HOME") != "1":
    if not os.environ.get("HERMUS_HOME"):
        os.environ["HERMUS_HOME"] = tempfile.mkdtemp(prefix="hermus-test-home-")
    os.environ.setdefault("HERMUS_DATA_DIR", str(Path(os.environ["HERMUS_HOME"]) / "data"))


from core.computer.task_control import get_task_control  # noqa: E402


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse a session that would write into the person's own home.

    The suite destroyed a real ``~/.hermus/onboarding.json`` on 2026-11-23,
    because nothing here checked where the home resolved to. This does: an
    unset ``HERMUS_HOME`` means the product's own ``~/.hermus``, which is not a
    place a test may run, so the session stops before any test does.
    """
    refuse_real_home(os.environ.get("HERMUS_HOME") or str(Path.home() / ".hermus"), "HERMUS_HOME")
    data_dir = os.environ.get("HERMUS_DATA_DIR")
    if data_dir:
        refuse_real_home(data_dir, "HERMUS_DATA_DIR")


@pytest.fixture()
def no_model_backend(monkeypatch):
    """Make "there is no model backend" true instead of inherited.

    Several tests pin the honest no-backend contract: a mission reports the
    blocker instead of faking work, and a turn still answers. On the machine
    this product is developed on, Ollama is usually running, so the same code
    path ran a real inference and the test measured the developer's PC instead
    of the contract. A closed port makes the precondition explicit and
    machine-independent; the empty key store above finishes the job.
    """
    from core.config import config

    monkeypatch.setattr(config, "ollama_base_url", "http://127.0.0.1:1")
    monkeypatch.setattr(config, "nollama_port", 1)


@pytest.fixture(autouse=True)
def _isolate_permission_policy(tmp_path_factory, monkeypatch):
    """Every test gets its own tool policy, in memory and on disk.

    The policy store outlives a test: an onboarding profile or a granted approval
    written by one test was read by the next in the same process, which then saw
    `web_search` denied or `credential_access` allowed. Eleven tests failed in a
    full run and passed alone for exactly this reason.

    The store lives in its own directory rather than the test's `tmp_path`, so a
    test that asserts what it wrote there still sees only its own files.
    """
    from core.approval import ApprovalStore
    from core.permissions import permission_manager

    store = tmp_path_factory.mktemp("policy")
    monkeypatch.setattr(permission_manager, "overrides_path", store / "permissions.json", raising=False)
    monkeypatch.setattr(
        permission_manager, "overrides", {"tools": {}, "agents": {}, "allowlist": [], "denylist": []}, raising=False
    )
    monkeypatch.setattr(permission_manager, "approvals", ApprovalStore(store / "approval_grants.json"), raising=False)


@pytest.fixture(autouse=True)
def _no_key_store(monkeypatch):
    """No test may read a key that belongs to whoever's machine it runs on.

    Model resolution asks every configured key a real question in production; in
    a test run that would both reach a provider and make the result depend on the
    machine, so the store is empty for every test and each one opts in instead.

    The same holds for a provider key exported in the developer's shell. A host
    carrying ``OPENAI_API_KEY`` for unrelated work outranks the ``GROQ_API_KEY``
    a test pins, so six provider-resolver and custom-key cases failed on Rishi's
    PC while staying green in CI. Every credential the presets know about is
    removed here, and the ``Config`` singleton is cleared of the same keys: it is
    built when the package is first imported, which is before this fixture can
    clean the environment, and the resolver still reads ``config.openai_api_key``
    as a fallback. A test that wants a key sets it in its own body or fixture,
    after this runs, and gets it back untouched.
    """
    from core import key_sources
    from core.config import config
    from core.providers import PROVIDER_PRESETS

    monkeypatch.setattr(key_sources, "store_keys", lambda provider: [])
    for preset in PROVIDER_PRESETS.values():
        env_key = preset.get("env_key")
        if env_key:
            monkeypatch.delenv(env_key, raising=False)
    for name in type(config).model_fields:
        if name.endswith(("_api_key", "_token")):
            monkeypatch.setattr(config, name, "", raising=False)
    key_sources.reset_cache()


@pytest.fixture(autouse=True)
def _isolate_task_control_state():
    """Reset the process-wide TaskControl singleton before each test."""
    get_task_control().reset()
    yield


@pytest.fixture(scope="session")
def _ledger_tmp_path(tmp_path_factory: pytest.TempPathFactory):
    return tmp_path_factory.mktemp("capability_ledger") / "CAPABILITY_LEDGER.md"


@pytest.fixture(scope="session", autouse=True)
def _isolated_key_store(tmp_path_factory: pytest.TempPathFactory):
    """The control-room key store is a test's own file, never the checkout's.

    ``data/api_keys.json`` lives in the repository, so from a checkout every test
    that adds a key writes into the same store the next test reads — one custom
    key left behind by an earlier case is enough to change which provider a
    fallback picks. It is also the store a person's own keys would sit in. A
    session-wide temporary file keeps both claims intact: tests that want a key
    add it and see it, and no test can see one another did not add.
    """
    from core.multi_key import multi_key_manager

    previous = multi_key_manager.db_path
    multi_key_manager.db_path = tmp_path_factory.mktemp("key-store") / "api_keys.json"
    multi_key_manager._load_queues()
    yield
    multi_key_manager.db_path = previous
    multi_key_manager._load_queues()


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
