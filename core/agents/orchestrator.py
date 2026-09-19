"""Durable multi-agent orchestration for the control room.

The pool owns persistent specialists. This module only coordinates a run:
parallel specialist work, visible handoffs on the shared message bus, and one
final synthesis pass. No credentials are written to the orchestration store.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.config import config
from core.log import get_logger

from .agent import Agent, AgentRole
from .messaging import MessageType, get_bus
from .pool import get_pool

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentOrchestrator:
    """Coordinate persistent agents and keep a restart-readable run ledger."""

    DEFAULT_ROLES = (
        AgentRole.RESEARCHER,
        AgentRole.CODER,
        AgentRole.VERIFIER,
        AgentRole.SYNTHESIZER,
    )

    def __init__(self, state_path: str = None):
        self.state_path = Path(state_path or config.resolve_path("data/orchestrations.json"))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _write(self, rows: list[dict]) -> None:
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _save(self, record: dict) -> dict:
        with self._lock:
            rows = [r for r in self._read() if r.get("orchestration_id") != record["orchestration_id"]]
            rows.append(record)
            self._write(rows[-100:])
        return record

    def list(self, limit: int = 30) -> list[dict]:
        return list(reversed(self._read()[-max(1, min(limit, 100)) :]))

    def get(self, orchestration_id: str) -> dict | None:
        return next((r for r in self._read() if r.get("orchestration_id") == orchestration_id), None)

    def create(self, goal: str, agent_ids: list[str] | None = None, max_agents: int = 4) -> dict:
        record = {
            "orchestration_id": f"orch_{uuid.uuid4().hex[:12]}",
            "goal": goal,
            "status": "queued",
            "agent_ids": list(agent_ids or []),
            "max_agents": max(1, min(int(max_agents or 4), 8)),
            "assignments": [],
            "handoffs": [],
            "results": [],
            "final": "",
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        return self._save(record)

    async def _ensure_team(self, record: dict) -> list[Agent]:
        pool = get_pool()
        if not pool._running:
            await pool.start()

        agents = [pool.get_agent(agent_id) for agent_id in record.get("agent_ids", [])]
        agents = [agent for agent in agents if agent is not None]
        if not agents:
            existing = pool.get_all_agents()
            agents = [agent for agent in existing if agent.state.value != "destroyed"]

        wanted = [role for role in self.DEFAULT_ROLES]
        for role in wanted:
            if len(agents) >= record["max_agents"]:
                break
            if any(agent.role == role for agent in agents):
                continue
            agents.append(await pool.create_agent(role=role, name=f"Hermus {role.value.title()}"))

        return agents[: record["max_agents"]]

    async def run(self, orchestration_id: str) -> dict:
        record = self.get(orchestration_id)
        if not record:
            raise KeyError(orchestration_id)

        try:
            record["status"] = "planning"
            record["updated_at"] = _now()
            agents = await self._ensure_team(record)
            record["agent_ids"] = [agent.agent_id for agent in agents]
            record["assignments"] = [
                {"agent_id": agent.agent_id, "name": agent.config.name, "role": agent.role.value}
                for agent in agents
            ]
            self._save(record)

            bus = get_bus()
            goal = record["goal"]
            record["status"] = "executing"
            record["updated_at"] = _now()
            self._save(record)

            async def work(agent: Agent) -> dict:
                prompt = (
                    f"Team goal: {goal}\n\n"
                    f"You are the {agent.role.value} specialist. Own the part of this goal that best matches your role. "
                    "Do not wait for another agent. Produce evidence, concrete recommendations, and next handoffs."
                )
                try:
                    answer = await agent.run_task(prompt, task_id=orchestration_id)
                    if answer.startswith("Error:"):
                        return {
                            "agent_id": agent.agent_id,
                            "name": agent.config.name,
                            "role": agent.role.value,
                            "answer": "",
                            "error": answer,
                        }
                    await bus.send(
                        sender_id=agent.agent_id,
                        target_id=orchestration_id,
                        content=answer[:8000],
                        message_type=MessageType.RESPONSE,
                        metadata={"orchestration_id": orchestration_id, "role": agent.role.value},
                    )
                    return {"agent_id": agent.agent_id, "name": agent.config.name, "role": agent.role.value, "answer": answer}
                except Exception as exc:
                    return {
                        "agent_id": agent.agent_id,
                        "name": agent.config.name,
                        "role": agent.role.value,
                        "answer": "",
                        "error": str(exc),
                    }

            results = await asyncio.gather(*(work(agent) for agent in agents))
            record["results"] = results
            record["handoffs"] = [
                {
                    "from": result.get("name"),
                    "to": "team",
                    "summary": (result.get("answer") or result.get("error") or "")[:500],
                }
                for result in results
            ]

            successful = [result for result in results if result.get("answer")]
            if successful:
                synthesizer = next((agent for agent in agents if agent.role in {AgentRole.CHAIR, AgentRole.SYNTHESIZER}), agents[0])
                packet = "\n\n".join(
                    f"[{result['role']} / {result['name']}]\n{result['answer'][:6000]}" for result in successful
                )
                final = await synthesizer.run_task(
                    f"You are the team lead. Synthesize the following specialist handoffs into one actionable answer for the user. "
                    f"Call out uncertainty and unresolved work.\n\nGoal: {goal}\n\nHandoffs:\n{packet}",
                    task_id=f"{orchestration_id}:synthesis",
                )
                record["final"] = final
                record["status"] = "completed"
                await bus.broadcast(
                    sender_id=synthesizer.agent_id,
                    content=final[:8000],
                    message_type=MessageType.BROADCAST,
                    metadata={"orchestration_id": orchestration_id, "phase": "synthesis"},
                )
            else:
                record["status"] = "failed"
                record["error"] = "No specialist returned a result"
            record["updated_at"] = _now()
            return self._save(record)
        except Exception as exc:
            logger.exception("Orchestration %s failed", orchestration_id)
            record["status"] = "failed"
            record["error"] = str(exc)
            record["updated_at"] = _now()
            return self._save(record)


agent_orchestrator = AgentOrchestrator()
