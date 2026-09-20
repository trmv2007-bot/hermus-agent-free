"""AgentManagerFacade — legacy ``core.agent_manager`` surface over FleetRegistry.

Pins the facade contract:

* the legacy method names exist with compatible signatures;
* ``create`` routes to ``FleetRegistry.spawn`` (a real, durable roster entry);
* ``submit_job`` routes to ``FleetRegistry.assign`` through an injected stub
  ``chat_fn`` — no network, no model backend;
* ``watchdog_tick`` reports registry state honestly (no fabricated
  ``revived``/``stale`` entries, no fake process restarts);
* where legacy semantics cannot be honored the facade refuses honestly
  (``{'success': False, 'error': 'moved to FleetRegistry: ...'}``) instead of
  fabricating success.

Offline: tmp_path file IO only.
"""

from __future__ import annotations

import pytest

from core.fleet.bus import FleetBus
from core.fleet.facade import AgentManagerFacade, fleet_facade
from core.fleet.registry import FleetRegistry


LEGACY_METHODS = (
    "create",
    "list",
    "start",
    "stop",
    "status",
    "submit_job",
    "job_status",
    "wait_job",
    "watchdog_tick",
)


@pytest.fixture()
def env(tmp_path):
    """A fresh FleetRegistry (stub chat_fn) behind the facade, on tmp storage."""

    def stub_chat_fn(messages):
        task = ""
        for turn in messages:
            if turn.get("role") == "user":
                task = turn.get("content") or ""
        return {"content": f"stub-reply:{task}", "tokens": 7}

    bus = FleetBus(base_dir=tmp_path / "fleet")
    bus.load()
    registry = FleetRegistry(bus, chat_fn=stub_chat_fn, roster_dir=tmp_path / "agents")
    facade = AgentManagerFacade(registry=registry)
    return facade, registry


# --------------------------------------------------------------------------- #
# surface
# --------------------------------------------------------------------------- #
def test_facade_exposes_legacy_method_names():
    facade = AgentManagerFacade()
    for method in LEGACY_METHODS:
        assert callable(getattr(facade, method, None)), f"missing legacy method {method!r}"


def test_module_exports_fleet_facade_singleton():
    assert isinstance(fleet_facade, AgentManagerFacade)
    for method in LEGACY_METHODS:
        assert callable(getattr(fleet_facade, method, None))


# --------------------------------------------------------------------------- #
# create → spawn
# --------------------------------------------------------------------------- #
def test_create_spawns_a_real_roster_agent(env):
    facade, registry = env
    result = facade.create("alfred", role="coder", model="llama3", persona="butler")
    assert result["success"] is True
    assert result["name"] == "alfred"
    assert result["role"] == "coder"
    # The registry itself owns the agent — not a facade-side side effect.
    agent = registry.get(result["agent_id"])
    assert agent is not None
    assert agent.name == "alfred"
    assert agent.model == "llama3"
    assert agent.persona == "butler"
    assert agent.state == "IDLE"
    # It is visible through the legacy list/status surface.
    assert [a["name"] for a in facade.list()] == ["alfred"]
    assert facade.status("alfred")["success"] is True


def test_create_rejects_unknown_role_and_duplicates(env):
    facade, registry = env
    bad_role = facade.create("x", role="wizard")
    assert bad_role["success"] is False
    assert "unknown role" in bad_role["error"]
    assert facade.create("dup")["success"] is True
    again = facade.create("dup")
    assert again["success"] is False
    assert "already exists" in again["error"]
    # nothing was spawned for either refusal
    assert [a.name for a in registry.list()] == ["dup"]



# --------------------------------------------------------------------------- #
# submit_job → assign
# --------------------------------------------------------------------------- #
def test_submit_job_routes_to_assign_via_stub_chat_fn(env):
    facade, registry = env
    facade.create("worker", role="coder")
    result = facade.submit_job("worker", {"task": "build the shed"})
    assert result["success"] is True
    assert result["name"] == "worker"
    assert result["job_id"]
    # Honest: the fleet executes synchronously, nothing is queued.
    assert result["queued"] is False
    assert result["executed"] is True
    assert result["status"] == "succeeded"
    assert result["result"] == "stub-reply:build the shed"
    # The registry actually ran the task (stats + state, not a facade echo).
    agent = facade._resolve("worker")
    assert agent.stats.tasks_done == 1
    assert agent.state == "IDLE"


def test_submit_job_is_idempotent_on_resubmit(env):
    facade, registry = env
    facade.create("worker")
    first = facade.submit_job("worker", {"task": "same task", "id": "job-42"})
    second = facade.submit_job("worker", {"task": "same task", "id": "job-42"})
    assert first["success"] is True
    assert second["success"] is True
    assert second["status"] == "deduplicated"
    assert second["executed"] is False
    assert first["job_id"] == "job-42" == second["job_id"]
    agent = facade._resolve("worker")
    assert agent.stats.tasks_done == 1  # executed exactly once


def test_submit_job_refuses_unknown_agent_and_empty_task(env):
    facade, _ = env
    missing = facade.submit_job("ghost", {"task": "hi"})
    assert missing["success"] is False
    assert "not found" in missing["error"]
    facade.create("worker")
    empty = facade.submit_job("worker", {"task": "  "})
    assert empty["success"] is False
    assert "no task" in empty["error"]


def test_job_status_and_wait_job_refuse_honestly(env):
    facade, _ = env
    facade.create("worker")
    for response in (
        facade.job_status("worker", "job-1"),
        facade.wait_job("worker", "job-1"),
    ):
        assert response["success"] is False
        assert response["error"].startswith("moved to FleetRegistry:")
        assert response["status"] == "unknown"


# --------------------------------------------------------------------------- #
# start/stop: pause/resume mapping, never a lie
# --------------------------------------------------------------------------- #
def test_stop_pauses_and_start_resumes(env):
    facade, registry = env
    facade.create("worker")
    stopped = facade.stop("worker")
    assert stopped["success"] is True
    assert facade._resolve("worker").state == "PAUSED"
    started = facade.start("worker")
    assert started["success"] is True
    assert facade._resolve("worker").state == "IDLE"


def test_start_on_idle_agent_refuses_honestly(env):
    facade, _ = env
    facade.create("worker")
    response = facade.start("worker")
    assert response["success"] is False
    assert response["error"].startswith("moved to FleetRegistry: start")


# --------------------------------------------------------------------------- #
# watchdog: honest report
# --------------------------------------------------------------------------- #
def test_watchdog_tick_reports_registry_state_honestly(env):
    facade, registry = env
    tick_empty = facade.watchdog_tick()
    assert tick_empty["agents"] == []
    assert tick_empty["revived"] == [] and tick_empty["stale"] == []
    facade.create("worker")
    facade.create("second")
    tick = facade.watchdog_tick(stale_seconds=5.0, restart=True)
    # The roster truth, straight from the registry:
    assert sorted(tick["agents"]) == ["second", "worker"]
    assert tick["states"] == {"worker": "IDLE", "second": "IDLE"}
    # No fabricated recovery: nothing was revived, no errors invented.
    assert tick["revived"] == []
    assert tick["stale"] == []
    assert tick["errors"] == []
    assert tick["recovery_owner"] == "fleet-registry"
    assert tick["tick"]  # timestamp present

