"""Executive-controlled specialist agent delegation for HERMUS.

This module converts high-level mission intent into a bounded specialist DAG.
It only plans assignments; MissionEngine remains responsible for execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agent_dag import AgentDAG
from .specialist_registry import SpecialistRegistry, specialist_registry


@dataclass(frozen=True)
class SpecialistProfile:
    role: str
    capabilities: tuple[str, ...]
    description: str
    risk: str = "standard"


DEFAULT_SPECIALISTS: tuple[SpecialistProfile, ...] = (
    SpecialistProfile("researcher", ("research", "requirements", "sources"), "Find and synthesize relevant facts"),
    SpecialistProfile("architect", ("architecture", "design", "planning"), "Design implementation and integration structure"),
    SpecialistProfile("coder", ("coding", "implementation", "tests"), "Implement and test software changes"),
    SpecialistProfile("code_reviewer", ("review", "correctness", "maintainability"), "Review implementation quality"),
    SpecialistProfile("security_auditor", ("security", "threat_model", "audit"), "Audit changes for security risks"),
    SpecialistProfile("integrator", ("integration", "build", "release"), "Integrate compatible outputs"),
    SpecialistProfile("verifier", ("verification", "testing", "proof"), "Verify that the mission actually succeeded"),
)


@dataclass
class DelegationPlan:
    mission: str
    selected_roles: list[str]
    dag: AgentDAG
    rationale: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission": self.mission,
            "selected_roles": list(self.selected_roles),
            "rationale": dict(self.rationale),
            "dag": self.dag.to_dict(),
        }


class AgentDelegator:
    """Select a bounded specialist team without directly executing agents."""

    def __init__(
        self,
        specialists: tuple[SpecialistProfile, ...] = DEFAULT_SPECIALISTS,
        max_agents: int = 7,
        registry: SpecialistRegistry | None = None,
    ) -> None:
        self.specialists = {item.role: item for item in specialists}
        self.max_agents = max(1, max_agents)
        self.registry = registry or specialist_registry

    @staticmethod
    def _signals(task: str) -> set[str]:
        text = task.lower()
        signals: set[str] = set()
        mapping = {
            "research": ("research", "investigate", "compare", "find out", "sources"),
            "architecture": ("architect", "design", "system", "api", "backend", "architecture"),
            "coding": ("build", "implement", "code", "fix", "feature", "bug", "refactor", "develop"),
            "security": ("security", "secure", "vulnerability", "audit", "threat"),
            "review": ("review", "inspect", "check", "quality"),
            "integration": ("integrate", "deploy", "release", "ship", "migration"),
            "verification": ("test", "verify", "validate", "prove", "working"),
        }
        for signal, words in mapping.items():
            if any(word in text for word in words):
                signals.add(signal)
        return signals

    def select_roles(self, task: str) -> list[str]:
        """Return a deterministic, bounded team appropriate for the task."""
        signals = self._signals(task)
        roles: list[str] = []
        if "research" in signals:
            roles.append("researcher")
        if signals & {"coding", "architecture", "integration"}:
            roles.extend(["architect", "coder"])
        if "security" in signals:
            roles.append("security_auditor")
        if "review" in signals or "coding" in signals:
            roles.append("code_reviewer")
        if "integration" in signals:
            roles.append("integrator")
        if "verification" in signals or roles:
            roles.append("verifier")
        if not roles:
            roles = ["researcher", "verifier"]
        deduped = list(dict.fromkeys(roles))
        selected = deduped[: self.max_agents]
        validation = self.registry.validate_selection(selected)
        if not validation["ok"]:
            raise ValueError(f"invalid specialist selection: {validation['reason']}")
        return selected

    def build_plan(self, task: str) -> DelegationPlan:
        task = str(task or "").strip()
        if not task:
            raise ValueError("task must not be empty")

        roles = self.select_roles(task)
        dag = AgentDAG(name=f"Executive Team: {task[:60]}")
        rationale: dict[str, str] = {}
        previous: str | None = None

        for index, role in enumerate(roles):
            profile = self.specialists[role]
            node_id = f"specialist_{index + 1}_{role}"
            dependencies = [previous] if previous else []
            # Review/security can run after implementation in the standard chain;
            # this plan remains intentionally conservative and deterministic.
            contract = self.registry.get(role)
            dag.add_node(
                node_id,
                role,
                f"{profile.description} for mission: {task}",
                dependencies=dependencies,
                inputs={
                    "capabilities": list(contract.capabilities) if contract else list(profile.capabilities),
                    "inputs": list(contract.inputs) if contract else [],
                    "outputs": list(contract.outputs) if contract else ["evidence"],
                    "requires_verification": contract.requires_verification if contract else True,
                    "max_steps": contract.max_steps if contract else 12,
                    "permissions": list(contract.permissions) if contract else [],
                },
                max_retries=2,
            )
            rationale[role] = profile.description
            previous = node_id

        if not dag.validate():
            raise ValueError("generated delegation DAG is invalid")
        return DelegationPlan(task, roles, dag, rationale)


agent_delegator = AgentDelegator()

__all__ = ["AgentDelegator", "DelegationPlan", "SpecialistProfile", "DEFAULT_SPECIALISTS", "agent_delegator"]
