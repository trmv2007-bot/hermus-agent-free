"""Dependency-aware delegation across persistent Hermus agents.

Desktop input is intentionally serialized through one ``computer-operator``;
research/coding work may run in parallel.  This prevents multiple agents from
fighting over the pointer while still allowing genuine multi-agent work.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from gateway.queue import job_queue

from ..agent_handlers import register_agent_handlers
from ..fleet.bus import FleetBus
from ..fleet.registry import FleetRegistry, chat_via_freellm
from ..llm import FreeLLM
from ..models import get_model_gateway
from .planner import TaskGraph


@dataclass
class WorkUnit:
    unit_id: str
    role: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    agent: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DelegationPlan:
    task: str
    units: list[WorkUnit]
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def validate(self) -> dict[str, Any]:
        names = [unit.unit_id for unit in self.units]
        errors: list[str] = []
        if len(names) != len(set(names)):
            errors.append("work unit ids must be unique")
        known = set(names)
        for unit in self.units:
            missing = [dep for dep in unit.depends_on if dep not in known]
            if missing:
                errors.append(f"unit '{unit.unit_id}' has unknown dependencies: {missing}")
            if unit.unit_id in unit.depends_on:
                errors.append(f"unit '{unit.unit_id}' depends on itself")
        # Generic DAG cycle detection.
        pending = {unit.unit_id: set(unit.depends_on) for unit in self.units}
        while pending:
            ready = {name for name, deps in pending.items() if not deps}
            if not ready:
                errors.append("delegation dependency graph contains a cycle")
                break
            pending = {name: deps - ready for name, deps in pending.items() if name not in ready}
        return {"ok": not errors, "errors": errors}

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task": self.task,
            "created_at": self.created_at,
            "units": [unit.to_dict() for unit in self.units],
            "validation": self.validate(),
        }


class MultiAgentDelegator:
    """Plan, queue, and collect a DAG of persistent-agent work."""

    def __init__(
        self,
        registry: FleetRegistry | None = None,
        llm: FreeLLM | None = None,
        root: str = "data/delegations",
    ):
        if registry is None:
            bus = FleetBus()
            registry = FleetRegistry(bus, chat_fn=chat_via_freellm)
            registry.boot()
        self.registry = registry
        self.llm = llm or get_model_gateway().llm()
        root_path = Path(root).expanduser()
        if not root_path.is_absolute() and root == "data/delegations":
            root_path = Path(__file__).resolve().parents[2] / root_path
        self.root = root_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _role_for(text: str) -> str:
        lowered = text.lower()
        if re.search(r"\b(?:research|find out|compare|investigate|sources?)\b", lowered):
            return "researcher"
        if re.search(r"\b(?:code|implement|program|script|debug|test suite)\b", lowered):
            return "coder"
        if re.search(r"\b(?:click|open|download|install|browser|window|type|press|desktop|unzip|launch)\b", lowered):
            return "computer-operator"
        return "generic"

    def plan(self, task: str, graph: TaskGraph | None = None) -> DelegationPlan:
        if graph is not None and graph.nodes:
            grouped: list[WorkUnit] = []
            current_role: str | None = None
            current_nodes: list[str] = []
            previous_id: str | None = None
            for node in graph.nodes:
                role = node.agent or "computer-operator"
                if role != current_role and current_nodes:
                    unit_id = f"unit-{len(grouped) + 1}"
                    grouped.append(
                        WorkUnit(
                            unit_id=unit_id,
                            role=current_role or "computer-operator",
                            task=f"{task}\nExecute graph states: {', '.join(current_nodes)}",
                            depends_on=[previous_id] if previous_id else [],
                            payload={"states": list(current_nodes)},
                        )
                    )
                    previous_id = unit_id
                    current_nodes = []
                current_role = role
                current_nodes.append(node.name)
            if current_nodes:
                unit_id = f"unit-{len(grouped) + 1}"
                grouped.append(
                    WorkUnit(
                        unit_id=unit_id,
                        role=current_role or "computer-operator",
                        task=f"{task}\nExecute graph states: {', '.join(current_nodes)}",
                        depends_on=[previous_id] if previous_id else [],
                        payload={"states": list(current_nodes)},
                    )
                )
            return DelegationPlan(task=task, units=grouped)

        # Deterministic clause decomposition remains useful without an LLM.
        clauses = [part.strip() for part in re.split(r"\b(?:and then|then|after that)\b|[;]", task, flags=re.I) if part.strip()]
        if not clauses:
            clauses = [task]
        units: list[WorkUnit] = []
        previous: str | None = None
        for index, clause in enumerate(clauses):
            role = self._role_for(clause)
            unit_id = f"unit-{index + 1}"
            # Explicit sequencing from "then" is retained. Independent work
            # can be submitted by callers with a custom DelegationPlan.
            units.append(
                WorkUnit(
                    unit_id=unit_id,
                    role=role,
                    task=clause,
                    depends_on=[previous] if previous else [],
                )
            )
            previous = unit_id
        return DelegationPlan(task=task, units=units)

    def _ensure_agent(self, role: str) -> str:
        safe_role = re.sub(r"[^a-z0-9-]+", "-", role.lower()).strip("-") or "generic"
        name = f"hermus-{safe_role}"
        # Find agent by name in FleetRegistry
        agent = None
        for a in self.registry.list():
            if a.name.casefold() == name.casefold():
                agent = a
                break
        if agent is None:
            # Create agent via FleetRegistry spawn
            spec = {
                "name": name,
                "persona": role,
                "model": "",
                "provider": "groq",
            }
            agent = self.registry.spawn(spec, allow_suffix=True)
        # FleetRegistry agents are spawned in IDLE state - no separate "start" needed
        return agent.agent_id

    def execute(
        self,
        plan: DelegationPlan,
        wait: bool = True,
        timeout_per_unit: float = 180.0,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        validation = plan.validate()
        if not validation["ok"]:
            return {"success": False, "plan": plan.to_dict(), "error": "; ".join(validation["errors"])}
        if dry_run:
            return {"success": True, "dry_run": True, "plan": plan.to_dict(), "jobs": []}

        pending = {unit.unit_id: unit for unit in plan.units}
        results: dict[str, dict[str, Any]] = {}
        jobs: list[dict[str, Any]] = []
        while pending:
            ready = [unit for unit in pending.values() if all(dep in results for dep in unit.depends_on)]
            if not ready:
                return {
                    "success": False,
                    "plan": plan.to_dict(),
                    "jobs": jobs,
                    "error": "delegation stalled waiting for dependencies",
                }

            # Queue one dependency layer. Multiple non-GUI agents may run in
            # parallel; there is only one computer-operator name, serializing GUI input.
            submitted = []
            for unit in ready:
                agent_id = unit.agent or self._ensure_agent(unit.role)
                dependency_context = {dep: results[dep].get("result") for dep in unit.depends_on}
                # Submit job via canonical Job queue
                role = unit.role
                kind = "agent.computer" if role == "computer-operator" else "agent.general"
                try:
                    register_agent_handlers(job_queue)
                    payload = {
                        "task": unit.task,
                        "role": unit.role,
                        "delegation_id": plan.plan_id,
                        "unit_id": unit.unit_id,
                        "dependencies": dependency_context,
                        "agent": agent_id,
                        "name": agent_id,
                        **unit.payload,
                    }
                    queued = job_queue.submit(kind, payload, session_key=f"agent:{agent_id}")
                except KeyError as exc:
                    return {"success": False, "plan": plan.to_dict(), "jobs": jobs, "error": str(exc)}
                except Exception as exc:
                    return {"success": False, "plan": plan.to_dict(), "jobs": jobs, "error": f"canonical queue unavailable: {exc}"}

                record = {"success": True, "name": agent_id, "job_id": queued.id, "queued": True, "status": queued.status,
                          "unit_id": unit.unit_id, "role": unit.role, "agent": agent_id}
                jobs.append(record)
                submitted.append(record)

            for record in submitted:
                if wait:
                    import time
                    deadline = time.monotonic() + max(0.0, float(timeout_per_unit))
                    while time.monotonic() <= deadline:
                        st = job_queue.status(record["job_id"])
                        status = st.get("status") or "unknown"
                        if status in ("succeeded", "failed", "cancelled", "blocked"):
                            status = {"success": True, "status": status, "name": record["agent"], "job_id": record["job_id"], "result": st.get("result"), "error": st.get("error")}
                            break
                        time.sleep(0.2)
                    else:
                        status = {"success": False, "status": "timeout", "name": record["agent"], "job_id": record["job_id"], "error": f"job did not finish within {timeout_per_unit:g}s"}
                else:
                    status = {"success": True, "status": "queued", "job_id": record["job_id"]}
                results[record["unit_id"]] = status
                pending.pop(record["unit_id"], None)
                if wait and (not status.get("success") or status.get("status") != "succeeded"):
                    output = {
                        "success": False,
                        "plan": plan.to_dict(),
                        "jobs": jobs,
                        "results": results,
                        "error": status.get("error") or "delegated job failed",
                    }
                    self._save(plan.plan_id, output)
                    return output

        success = all(
            result.get("success") and (not wait or bool(result.get("result", {}).get("success", True)))
            for result in results.values()
        )
        output = {"success": success, "plan": plan.to_dict(), "jobs": jobs, "results": results}
        self._save(plan.plan_id, output)
        return output

    def _save(self, plan_id: str, output: dict[str, Any]) -> str:
        path = self.root / f"{plan_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
        return str(path)
