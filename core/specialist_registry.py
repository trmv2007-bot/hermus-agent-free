"""Specialist capability registry for HERMUS.

Profiles are explicit contracts: capabilities, input/output expectations,
risk and resource limits. The registry selects capabilities; execution remains
owned by the existing delegation and Mission Runtime layers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SpecialistContract:
    role: str
    capabilities: tuple[str, ...]
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ("evidence",)
    risk: str = "standard"
    max_steps: int = 12
    requires_verification: bool = True
    permissions: tuple[str, ...] = ()


DEFAULT_CONTRACTS = (
    SpecialistContract("researcher", ("research", "requirements", "sources"), ("task",), ("findings", "sources")),
    SpecialistContract("architect", ("architecture", "design", "planning"), ("task", "findings"), ("design", "evidence")),
    SpecialistContract("coder", ("coding", "implementation", "tests"), ("task", "design"), ("changes", "test_results")),
    SpecialistContract("code_reviewer", ("review", "correctness", "maintainability"), ("changes",), ("review", "evidence")),
    SpecialistContract("security_auditor", ("security", "threat_model", "audit"), ("changes",), ("audit", "evidence"), "elevated", 10),
    SpecialistContract("integrator", ("integration", "build", "release"), ("changes", "test_results"), ("integration", "evidence")),
    SpecialistContract("verifier", ("verification", "testing", "proof"), ("changes", "test_results"), ("verification", "evidence")),
)


class SpecialistRegistry:
    def __init__(self, contracts: tuple[SpecialistContract, ...] = DEFAULT_CONTRACTS, max_active: int = 7):
        self._contracts = {item.role: item for item in contracts}
        self.max_active = max(1, int(max_active))

    def get(self, role: str) -> SpecialistContract | None:
        return self._contracts.get(str(role))

    def list(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._contracts.values()]

    def validate_selection(self, roles: list[str]) -> dict[str, Any]:
        raw = [str(role) for role in roles]
        duplicates = sorted({role for role in raw if raw.count(role) > 1})
        if duplicates:
            return {"ok": False, "reason": "duplicate_specialist", "duplicates": duplicates, "roles": raw}
        unknown = [role for role in raw if role not in self._contracts]
        if len(raw) > self.max_active:
            return {"ok": False, "reason": "max_active_specialists_exceeded", "unknown": unknown}
        if unknown:
            return {"ok": False, "reason": "unknown_specialist", "unknown": unknown}
        return {"ok": True, "roles": raw}

    def contracts_for(self, roles: list[str]) -> list[dict[str, Any]]:
        validation = self.validate_selection(roles)
        if not validation["ok"]:
            raise ValueError(validation["reason"])
        return [asdict(self._contracts[role]) for role in roles]


specialist_registry = SpecialistRegistry()

__all__ = ["DEFAULT_CONTRACTS", "SpecialistContract", "SpecialistRegistry", "specialist_registry"]
