"""Fleet watchdog -- the guarded autonomy layer.

The watchdog is the only component allowed to act on the fleet without a
human click: it recovers errored agents and drives proposed/stalled missions
through ordinary :class:`~core.fleet.orchestrator.Orchestrator` rounds.  It is
Ultron-grade only inside the cage the Safety Core builds:

* the emergency brake stops it cold (and an unreadable brake counts as pulled),
* per-agent restart budgets cap self-healing,
* mission budgets and HITL gates are enforced by the Orchestrator itself,
* every act is appended to the durable FleetBus so nothing happens silently.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from core.fleet.missions import CLAIMING, PROPOSED, WORKING
from core.fleet.orchestrator import Orchestrator
from core.fleet.registry import ERROR, IDLE

logger = logging.getLogger(__name__)

HEAL_STATES = {ERROR}
DRIVE_STATES = {PROPOSED, CLAIMING, WORKING}
IDLE_STATES = {IDLE}
PENDING_SUBTASK = {"open", "claimed"}

_WATCHDOG: FleetWatchdog | None = None


class FleetWatchdog:
    """Self-heal agents and keep eligible missions moving, audibly."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        interval: float = 15.0,
        max_restarts_per_hour: int = 3,
        enabled: bool = True,
    ) -> None:
        self.orch = orchestrator
        self.registry = orchestrator.registry
        self.missions = orchestrator.missions
        self.bus = orchestrator.bus
        self.interval = interval
        self.max_restarts_per_hour = max_restarts_per_hour
        self.enabled = enabled
        self._restarts: dict[str, list[float]] = {}
        self._actions: list[dict[str, Any]] = []
        self.counters = {
            "ticks": 0,
            "recovered": 0,
            "driven": 0,
            "skipped_brake": 0,
            "skipped_no_idle": 0,
        }
        self.last_tick: float | None = None

    # ------------------------------------------------------------------ #
    # introspection
    # ------------------------------------------------------------------ #
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval": self.interval,
            "last_tick": self.last_tick,
            "counters": dict(self.counters),
            "recent_actions": list(self._actions[-12:]),
        }

    def _record(self, kind: str, detail: dict[str, Any]) -> None:
        self._actions.append({"kind": kind, "at": time.time(), **detail})
        del self._actions[:-50]
        try:
            self.bus.append(sender="fleet-watchdog", kind=f"fleet.watchdog.{kind}", content=detail)
        except Exception as exc:  # auditing must never break the autonomy loop
            logger.warning("[watchdog] bus append failed: %s", exc)

    @staticmethod
    def _brake_active() -> bool:
        try:
            from core.emergency_stop import get_emergency_stop

            return bool(get_emergency_stop().state().to_dict().get("active"))
        except Exception:
            return True  # fail closed: an unreadable brake counts as pulled

    def _restart_budget_ok(self, agent_id: str, now: float) -> bool:
        hits = [t for t in self._restarts.get(agent_id, []) if now - t < 3600]
        self._restarts[agent_id] = hits
        return len(hits) < self.max_restarts_per_hour

    # ------------------------------------------------------------------ #
    # acts
    # ------------------------------------------------------------------ #
    def heal(self) -> list[dict[str, Any]]:
        acts: list[dict[str, Any]] = []
        now = time.time()
        for agent in self.registry.list():
            if agent.state not in HEAL_STATES or not self._restart_budget_ok(agent.agent_id, now):
                continue
            try:
                self.registry.recover(agent.agent_id)
            except Exception as exc:
                self._record("heal_failed", {"agent": agent.agent_id, "error": str(exc)})
                continue
            self._restarts.setdefault(agent.agent_id, []).append(now)
            self.counters["recovered"] += 1
            act = {"act": "agent_recovered", "agent": agent.agent_id, "from": ERROR}
            self._record("agent_recovered", {"agent": agent.agent_id, "from": ERROR})
            acts.append(act)
        return acts

    async def drive(self) -> list[dict[str, Any]]:
        acts: list[dict[str, Any]] = []
        idle = [a.agent_id for a in self.registry.list() if a.state in IDLE_STATES]
        for mission in self.missions.list():
            if mission.state not in DRIVE_STATES:
                continue
            if not any(s.status in PENDING_SUBTASK for s in mission.subtasks):
                continue
            if not idle:
                self.counters["skipped_no_idle"] += 1
                continue
            try:
                res = await self.orch.drive_round(mission.mission_id, agent_ids=idle)
            except Exception as exc:
                self._record("drive_failed", {"mission": mission.mission_id, "error": str(exc)})
                continue
            if not res.get("ok"):
                self._record("drive_refused", {"mission": mission.mission_id, "reason": res.get("reason")})
                continue
            self.counters["driven"] += 1
            act = {"act": "mission_driven", "mission": mission.mission_id, **res}
            self._record(
                "mission_driven",
                {
                    "mission": mission.mission_id,
                    "completed_delta": res.get("completed_delta"),
                    "claimed": res.get("claimed"),
                    "state": res.get("state"),
                },
            )
            acts.append(act)
        return acts

    async def tick(self) -> dict[str, Any]:
        self.counters["ticks"] += 1
        self.last_tick = time.time()
        if not self.enabled:
            return {"acted": False, "reason": "disabled", "acts": []}
        if self._brake_active():
            self.counters["skipped_brake"] += 1
            return {"acted": False, "reason": "brake", "acts": []}
        acts = self.heal()
        acts.extend(await self.drive())
        return {"acted": bool(acts), "acts": acts}

    async def run(self, stop: asyncio.Event | None = None) -> None:
        while True:
            try:
                res = await self.tick()
                if res.get("acts"):
                    logger.info("[watchdog] acts=%s", res["acts"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[watchdog] tick error: %s", exc)
            if stop is not None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.interval)
                    return
                except asyncio.TimeoutError:
                    continue
            await asyncio.sleep(self.interval)


def install_watchdog(wd: FleetWatchdog) -> FleetWatchdog:
    global _WATCHDOG
    _WATCHDOG = wd
    return wd


def get_watchdog() -> FleetWatchdog | None:
    return _WATCHDOG


def ensure_watchdog(registry, bus=None, orchestrator=None, **kwargs: Any) -> FleetWatchdog:
    """Build (once) the process-wide watchdog.

    Pass the gateway's shared Orchestrator: a second one owns a second
    MissionManager, and missions opened on the request path would be invisible
    to the autonomy loop.
    """
    if _WATCHDOG is not None:
        return _WATCHDOG
    orch = orchestrator or Orchestrator(registry, bus or registry.bus)
    return install_watchdog(FleetWatchdog(orch, **kwargs))


__all__ = [
    "FleetWatchdog",
    "install_watchdog",
    "get_watchdog",
    "ensure_watchdog",
]
