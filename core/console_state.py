"""One server-rendered state bundle for the Control Room's first screen.

The Control Room used to ask for its opening state with ~20 separate requests.
Every one of them was a real probe, several were slow on a cold gateway, and
Chrome's six-sockets-per-host cap turned that fan-out into a queue: a typed
question waited 15 seconds behind diagnostics nobody had asked for.

This module is the single place that assembles what the first screen and the
tab rail read. It exists so that

* ``GET /api/v1/console/state`` answers in one pass instead of twenty, and
* nothing user-facing ever runs a one-time initialisation inline.

Each section is produced by the *same* handler the individual endpoint serves,
so the bundle can never become a second source of truth. Sections are rebuilt
by one background refresher at their own cadence — fast-moving state every few
seconds, expensive diagnostics rarely — and the route returns the latest
snapshot. A snapshot that does not exist yet is built from the cheap sections
only; the expensive ones read ``{"warming": true}`` and become available within
seconds, which is also what ``/readyz`` reports.

The refresher is a plain daemon thread with its own event loop, so it cannot
starve the gateway's loop and dies with the process.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("hermus.core.console_state")

#: Bumped when a section is added, removed, or changes shape. The client reads
#: it so an old cached page cannot render a payload it does not understand.
BUNDLE_VERSION = 1

#: How often each tier is rebuilt, in seconds.
FAST_SECONDS = 3.0
SLOW_SECONDS = 20.0
HEAVY_SECONDS = 60.0


async def _section_readiness() -> Any:
    from gateway.lifecycle import readiness

    return readiness()[1]


async def _section_health() -> Any:
    from gateway.routes_canonical import system_health

    return await system_health()


async def _section_capabilities() -> Any:
    from gateway.routes_canonical import system_capabilities

    return await system_capabilities()


async def _section_presence() -> Any:
    from gateway.routes_presence import presence_snapshot

    return await asyncio.to_thread(presence_snapshot, None)


async def _section_presence_kernel() -> Any:
    from core.presence_kernel import presence_kernel

    return await asyncio.to_thread(presence_kernel.snapshot, user_id="default", include_events=True)


async def _section_jobs() -> Any:
    from gateway.realtime import list_jobs

    return await list_jobs(limit=50)


async def _section_queue() -> Any:
    from gateway.realtime import queue_status

    return await asyncio.to_thread(queue_status)


async def _section_missions() -> Any:
    from gateway.realtime import mission_list_api

    return await mission_list_api()


async def _section_events() -> Any:
    from gateway.routes_engine import events_recent

    return await asyncio.to_thread(events_recent, 50)


async def _section_emergency() -> Any:
    from gateway.routes_subsystems import emergency_status

    return await emergency_status()


async def _section_agents() -> Any:
    from gateway.routes_agents import list_agents

    # The handler takes a Request it never reads; the bundle has no request.
    return await list_agents(None)  # type: ignore[arg-type]


async def _section_agent_messages() -> Any:
    from gateway.routes_agents import get_messages

    return await get_messages(None, limit=100)  # type: ignore[arg-type]


async def _section_api_keys() -> Any:
    from gateway.routes_agents import list_api_keys

    return await list_api_keys(None)  # type: ignore[arg-type]


async def _section_personal_space() -> Any:
    from gateway.routes_personal_space import personal_space_snapshot

    return await personal_space_snapshot(6)


async def _section_voice() -> Any:
    from gateway.routes_voice import voice_status

    return await voice_status()


async def _section_console() -> Any:
    from gateway.routes_console import console_manifest

    return await console_manifest()


async def _section_harness() -> Any:
    from gateway.routes_harness import harness_overview

    return await harness_overview()


async def _section_computer() -> Any:
    from gateway.routes_computer import computer_status

    return await computer_status()


async def _section_approvals() -> Any:
    from gateway.routes_computer import remote_approvals

    return await remote_approvals()


async def _section_jarvis() -> Any:
    from gateway.routes_jarvis import jarvis_status

    return await jarvis_status()


async def _section_vram() -> Any:
    from gateway.routes_agents import get_vram_status

    return await get_vram_status(None)  # type: ignore[arg-type]


#: ``name -> (builder, cadence tier)``. Fast sections are what the first screen
#: paints; slow ones are tab content; heavy ones fork processes or probe
#: localhost and must never be in a person's request path.
_SECTIONS: dict[str, tuple[Callable[[], Awaitable[Any]], float]] = {
    "readiness": (_section_readiness, FAST_SECONDS),
    "health": (_section_health, FAST_SECONDS),
    "capabilities": (_section_capabilities, FAST_SECONDS),
    "presence": (_section_presence, FAST_SECONDS),
    "presence_kernel": (_section_presence_kernel, FAST_SECONDS),
    "jobs": (_section_jobs, FAST_SECONDS),
    "queue": (_section_queue, FAST_SECONDS),
    "missions": (_section_missions, FAST_SECONDS),
    "events": (_section_events, FAST_SECONDS),
    "emergency": (_section_emergency, FAST_SECONDS),
    "agents": (_section_agents, FAST_SECONDS),
    "agent_messages": (_section_agent_messages, FAST_SECONDS),
    "api_keys": (_section_api_keys, FAST_SECONDS),
    "personal_space": (_section_personal_space, FAST_SECONDS),
    "voice": (_section_voice, FAST_SECONDS),
    "console": (_section_console, SLOW_SECONDS),
    "harness": (_section_harness, SLOW_SECONDS),
    "computer": (_section_computer, SLOW_SECONDS),
    "approvals": (_section_approvals, SLOW_SECONDS),
    "jarvis": (_section_jarvis, SLOW_SECONDS),
    "vram": (_section_vram, HEAVY_SECONDS),
}

#: Sections that wait for the refresher instead of being built inside a
#: request. Every one of them either forks a process or probes localhost.
BACKGROUND_ONLY = frozenset({"jarvis", "vram", "computer"})

#: Sections a cold first screen must never block on. They answer
#: ``{"warming": true}`` until the first background pass lands.
_WARMING = {"warming": True}


class ConsoleState:
    """Holds the newest snapshot and rebuilds it off the request path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._built_at: dict[str, float] = {}
        self._build_ms: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self._started_at = time.monotonic()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._first_pass_done = False
        self._generation = 0

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Start the background refresher once (idempotent)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="hermus-console-state", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        # A private loop keeps the refresher independent of the gateway loop:
        # a slow section can never delay a person's request.
        asyncio.run(self._loop())

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.refresh(force=False)
            except Exception as exc:  # noqa: BLE001 - the refresher must survive
                logger.warning(f"console state refresh failed: {type(exc).__name__}: {exc}")
            await asyncio.sleep(1.0)

    # ------------------------------------------------------------------ builds
    async def _build_one(self, name: str) -> tuple[Any, float]:
        builder = _SECTIONS[name][0]
        started = time.monotonic()
        try:
            value = await asyncio.wait_for(builder(), timeout=45.0)
            return value, (time.monotonic() - started) * 1000
        except Exception as exc:  # noqa: BLE001 - one section must not sink the bundle
            message = f"{type(exc).__name__}: {exc}"
            logger.debug(f"console state section {name} failed: {message}")
            return {"available": False, "error": message[:300]}, (time.monotonic() - started) * 1000

    async def refresh(self, *, force: bool, only: set[str] | None = None, include_background: bool = True) -> None:
        """Rebuild every section whose cadence has elapsed."""
        now = time.monotonic()
        due: list[str] = []
        with self._lock:
            built = dict(self._built_at)
        for name, (_builder, cadence) in _SECTIONS.items():
            if only is not None and name not in only:
                continue
            if not include_background and name in BACKGROUND_ONLY:
                continue
            last = built.get(name)
            if force or last is None or (now - last) >= cadence:
                due.append(name)
        if not due:
            return

        results = await asyncio.gather(*(self._build_one(name) for name in due))
        finished = time.monotonic()
        with self._lock:
            for name, (value, ms) in zip(due, results, strict=True):
                self._data[name] = value
                self._built_at[name] = finished
                self._build_ms[name] = round(ms, 1)
                if isinstance(value, dict) and value.get("available") is False and "error" in value:
                    self._errors[name] = str(value["error"])[:300]
                else:
                    self._errors.pop(name, None)
            if all(name in self._built_at for name in _SECTIONS):
                self._first_pass_done = True
            self._generation += 1

    # ------------------------------------------------------------------ reads
    def snapshot(self) -> dict[str, Any]:
        """This moment's bundle, with per-section age and honesty about warming."""
        with self._lock:
            sections: dict[str, Any] = {}
            ages: dict[str, float] = {}
            for name in _SECTIONS:
                value = self._data.get(name)
                at = self._built_at.get(name)
                if value is None:
                    # Never built: say so instead of inventing a payload.
                    sections[name] = dict(_WARMING)
                else:
                    sections[name] = value
                if at is not None:
                    ages[name] = round((time.monotonic() - at) * 1000, 1)
            degraded = sorted(self._errors)
            first_pass = self._first_pass_done
            build_ms = dict(self._build_ms)
            generation = self._generation
        return {
            "v": BUNDLE_VERSION,
            "warm": first_pass,
            "generation": generation,
            "generated_at": time.time(),
            "section_age_ms": ages,
            "section_build_ms": build_ms,
            "degraded": degraded,
            "sections": sections,
        }

    def progress(self) -> dict[str, Any]:
        """Honest warm-up progress for ``/readyz``."""
        with self._lock:
            built = dict(self._built_at)
            errors = dict(self._errors)
            first_pass = self._first_pass_done
            builds = dict(self._build_ms)
        steps = []
        for name in _SECTIONS:
            at = built.get(name)
            steps.append(
                {
                    "name": name,
                    "state": "warming" if at is None else ("error" if name in errors else "done"),
                    "age_ms": None if at is None else round((time.monotonic() - at) * 1000, 1),
                    "ms": builds.get(name),
                }
            )
        done = sum(1 for step in steps if step["state"] != "warming")
        return {
            "state": "warm" if first_pass else "warming",
            "done": done,
            "total": len(steps),
            "uptime_ms": round((time.monotonic() - self._started_at) * 1000, 1),
            "steps": steps,
        }

    async def ensure_sections(self, names: set[str]) -> None:
        """Build the named sections if they have never been built (cheap ones only)."""
        with self._lock:
            missing = {name for name in names if name in _SECTIONS and name not in self._built_at}
            missing -= BACKGROUND_ONLY
        if missing:
            await self.refresh(force=False, only=missing)


console_state = ConsoleState()


__all__ = ["BUNDLE_VERSION", "ConsoleState", "console_state"]
