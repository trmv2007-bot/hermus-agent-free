"""Canonical Learning / Skill Forge subsystem (Rebuild spec §16).

Unifies lessons/evolution/skill_forge/trajectories into one evidence-driven
learning service. A procedure is **never** learned from a single plausible run and
**never** promoted unless it achieved a configured number of independently
verified successful repetitions. Skills are versioned artifacts with provenance,
tests and rollback; a skill can be quarantined after repeated failures.

``LearningFacade`` wraps the existing ``SkillForge`` (which owns the success
ledger, distillation, validation and install) and enforces the promotion gate at a
single point so no caller can promote a skill that has not proven itself.
"""

from .facade import LearningFacade, get_learning

__all__ = ["LearningFacade", "get_learning"]
