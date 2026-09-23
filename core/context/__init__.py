"""Tiered runtime context: core / task / on-demand. One owner for prompt size."""

from __future__ import annotations

from .assembler import ContextRequest, build_system_prompt, user_model_digest
from .ondemand import DOC_ALLOWLIST, MAX_CHARS, TOPICS, read_context
from .tiers import ContextBlock, ContextBudget, ContextPlan, Tier, fit

__all__ = [
    "ContextBlock",
    "ContextBudget",
    "ContextPlan",
    "ContextRequest",
    "DOC_ALLOWLIST",
    "MAX_CHARS",
    "TOPICS",
    "Tier",
    "build_system_prompt",
    "fit",
    "read_context",
    "user_model_digest",
]
