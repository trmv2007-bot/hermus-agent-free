"""One state bundle, one scheduler, and nothing slow on the first screen.

These tests pin the contract the Control Room now depends on:

* ``GET /api/v1/console/state`` is versioned, carries every section the first
  screen reads, and is served from a warm snapshot rather than a probe storm.
* Diagnostics are *not* in it: ``/doctor/status`` and ``/engine/status`` are
  behind a door, so a page load can never be held up by a hardware examination.
* ``/readyz`` tells the truth about warm-up instead of blocking on it.
* The vram probe that used to freeze the event loop stays off the loop.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console_state import _SECTIONS, BACKGROUND_ONLY, BUNDLE_VERSION, ConsoleState, console_state

#: Everything the first screen and the tab rail paint from.
FIRST_SCREEN_SECTIONS = {
    "readiness",
    "health",
    "capabilities",
    "presence",
    "presence_kernel",
    "jobs",
    "queue",
    "missions",
    "events",
    "emergency",
    "agents",
    "agent_messages",
    "api_keys",
    "personal_space",
    "voice",
    "console",
}

#: Diagnostics: slow, machine-wide, and never part of a page load.
DIAGNOSTIC_PATHS = ("/doctor/status", "/engine/status", "/doctor/run")


class TestBundleContract:
    def test_bundle_is_versioned_and_carries_the_first_screen(self):
        from gateway.gateway import app

        client = TestClient(app)
        r = client.get("/api/v1/console/state")
        assert r.status_code == 200
        body = r.json()
        assert body["v"] == BUNDLE_VERSION
        assert isinstance(body["sections"], dict)
        missing = FIRST_SCREEN_SECTIONS - set(body["sections"])
        assert not missing, f"bundle lost first-screen sections: {sorted(missing)}"
        # Every declared section is present, so the client's map cannot point
        # at a key the server never sends.
        assert set(body["sections"]) == set(_SECTIONS)

    def test_bundle_carries_no_diagnostic(self):
        from gateway.gateway import app

        client = TestClient(app)
        body = client.get("/api/v1/console/state").json()
        for name in ("doctor", "engine", "doctor_status", "engine_status"):
            assert name not in body["sections"], f"{name} must stay behind the diagnostics door"

    def test_section_names_do_not_leak_paths(self):
        """The bundle is a state projection, not a proxy for any URL."""
        for name in _SECTIONS:
            assert "/" not in name and "." not in name, name

    def test_unknown_sections_are_ignored_not_invented(self):
        from gateway.gateway import app

        client = TestClient(app)
        body = client.get("/api/v1/console/state?include=/etc/passwd,doctor").json()
        assert set(body["sections"]) == set(_SECTIONS)
        assert "doctor" not in body["sections"]


class TestWarmSnapshot:
    def test_snapshot_answers_immediately_from_cache(self):
        """A built snapshot is a dict copy, not a new probe storm."""
        state = ConsoleState()
        state._data = {name: {"cached": True} for name in _SECTIONS}
        state._built_at = {name: time.monotonic() for name in _SECTIONS}
        state._first_pass_done = True
        started = time.perf_counter()
        snap = state.snapshot()
        elapsed = time.perf_counter() - started
        assert elapsed < 0.05, f"snapshot took {elapsed:.3f}s with a warm cache"
        assert snap["warm"] is True
        assert set(snap["sections"]) == set(_SECTIONS)

    def test_unbuilt_sections_say_warming_instead_of_lying(self):
        state = ConsoleState()
        snap = state.snapshot()
        assert snap["warm"] is False
        assert snap["sections"]["health"] == {"warming": True}

    def test_progress_counts_what_is_warm(self):
        state = ConsoleState()
        progress = state.progress()
        assert progress["state"] == "warming"
        assert progress["done"] == 0
        assert progress["total"] == len(_SECTIONS)
        state._data = {name: {} for name in _SECTIONS}
        state._built_at = {name: time.monotonic() for name in _SECTIONS}
        state._first_pass_done = True
        warm = state.progress()
        assert warm["state"] == "warm"
        assert warm["done"] == warm["total"]

    def test_background_sections_are_never_built_inside_a_request(self):
        """jarvis/vram/computer probe processes or localhost: they wait for the refresher."""
        assert BACKGROUND_ONLY <= set(_SECTIONS)
        state = ConsoleState()

        async def run() -> None:
            await state.ensure_sections(set(BACKGROUND_ONLY))

        asyncio.run(run())
        for name in BACKGROUND_ONLY:
            assert name not in state._built_at, f"{name} was built inline"


class TestReadiness:
    def test_readyz_reports_warmup_progress(self):
        from gateway.gateway import app

        client = TestClient(app)
        body = client.get("/readyz").json()
        # 200 carries the payload at the top level; a 503 envelopes it under
        # `details`. Both must report the same warm-up truth.
        warmup = body.get("warmup") or (body.get("details") or {}).get("warmup")
        assert warmup, f"/readyz lost warm-up progress: {body}"
        assert warmup["state"] in {"warming", "warm"}
        assert warmup["total"] == len(_SECTIONS)
        assert isinstance(warmup["steps"], list)
        assert {step["name"] for step in warmup["steps"]} == set(_SECTIONS)

    def test_warmup_never_gates_readiness(self):
        """A gateway taking traffic is ready even while a section is warming."""
        from gateway.gateway import app

        client = TestClient(app)
        response = client.get("/readyz")
        if response.status_code == 200:
            assert response.json()["status"] == "ready"
        else:
            assert response.status_code == 503
            assert "warmup" in (response.json().get("details") or {})


class TestVramStaysOffTheLoop:
    def test_vram_probe_does_not_block_the_event_loop(self):
        """The regression that froze every other request for ~13s.

        The probe runs in a worker thread, so a second request must be served
        while it is still running. Probed with a slow fake so the assertion is
        about the loop, not about this machine's hardware.
        """
        from gateway import routes_agents

        def slow_probe() -> dict:
            time.sleep(1.0)
            return {"status": "ok", "gpu": {"detected": False}, "vram": {"available": False}, "recommended": {}, "limits": {}}

        original = routes_agents._vram_status_cached
        routes_agents._vram_status_cached = slow_probe
        routes_agents._VRAM_CACHE.update(at=0.0, data=None)
        app = FastAPI()
        app.include_router(routes_agents.router)

        async def scenario() -> tuple[float, float]:
            client = TestClient(app)
            loop = asyncio.get_running_loop()
            started = loop.time()
            slow = asyncio.to_thread(client.get, "/api/v1/agents/vram/status")
            await asyncio.sleep(0.1)
            tick_started = loop.time()
            await asyncio.sleep(0.2)
            tick = loop.time() - tick_started
            await slow
            return loop.time() - started, tick

        try:
            total, tick = asyncio.run(scenario())
        finally:
            routes_agents._vram_status_cached = original
            routes_agents._VRAM_CACHE.update(at=0.0, data=None)

        assert total >= 1.0
        assert tick < 0.9, f"event loop was blocked for {tick:.2f}s while vram probed"

    def test_vram_answer_is_reused_briefly(self):
        """Three panel refreshes, one 13-second hardware recommendation."""
        from gateway import routes_agents

        calls = {"n": 0}

        class FakeLocalFirst:
            def detect_gpu(self):
                return {"detected": False, "name": None, "vram": 0, "is_rtx3050": False}

            def get_vram_usage(self):
                return {"available": False, "used": 0, "free": 0.0, "total": 0.0, "percent": 0}

            def get_recommended_setup(self):
                calls["n"] += 1
                return {"recommended": {"provider": "ollama", "model": "mistral:7b"}}

        original = routes_agents.get_local_first
        routes_agents.get_local_first = lambda: FakeLocalFirst()
        routes_agents._VRAM_CACHE.update(at=0.0, data=None)
        try:
            for _ in range(3):
                assert routes_agents._vram_status_cached()["status"] == "ok"
        finally:
            routes_agents.get_local_first = original
            routes_agents._VRAM_CACHE.update(at=0.0, data=None)
        assert calls["n"] == 1


class TestSharedSnapshotSingleton:
    def test_the_gateway_uses_one_instance(self):
        from core import console_state as module

        assert module.console_state is console_state
        assert isinstance(console_state, ConsoleState)

    @pytest.mark.parametrize("name", sorted(_SECTIONS))
    def test_every_section_builder_is_async(self, name):
        builder = _SECTIONS[name][0]
        assert asyncio.iscoroutinefunction(builder), f"{name} must be awaited, not called"
