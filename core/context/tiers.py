"""Context tiers: the model gets the minimum sufficient context, not the repo."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Tier(str, enum.Enum):
    CORE = "core"
    TASK = "task"
    ON_DEMAND = "on_demand"


@dataclass(frozen=True)
class ContextBlock:
    """One labeled piece of prompt text with the budget rules that govern it.

    ``truncate_at`` marks a block that may be shortened rather than dropped;
    blocks without it are kept whole or omitted entirely, because a partial
    contract (half a rule list, half a tool line) reads as a full one.
    """

    kind: str
    tier: Tier
    text: str
    source: str
    priority: int = 50
    truncate_at: int | None = None

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class ContextBudget:
    core_chars: int = 1600
    task_chars: int = 4400

    def for_tier(self, tier: Tier) -> int:
        if tier is Tier.CORE:
            return self.core_chars
        if tier is Tier.TASK:
            return self.task_chars
        return 1 << 30


@dataclass
class ContextPlan:
    """Assembled blocks plus the record of what was left out, and why."""

    blocks: list[ContextBlock] = field(default_factory=list)
    omitted: list[tuple[str, str, int]] = field(default_factory=list)
    budget: ContextBudget = field(default_factory=ContextBudget)

    def render(self) -> str:
        parts = [b.text.strip() for b in self.blocks if b.text.strip()]
        return "\n\n".join(parts) + "\n"

    def chars_by_tier(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for block in self.blocks:
            totals[block.tier.value] = totals.get(block.tier.value, 0) + block.chars
        return totals

    def report(self) -> dict:
        """Sizes per block/tier plus omissions — emitted as telemetry and used
        by the workspace, so a context regression is measurable instead of
        assumed."""
        return {
            "tiers": self.chars_by_tier(),
            "total_chars": sum(self.chars_by_tier().values()),
            "budget": {"core": self.budget.core_chars, "task": self.budget.task_chars},
            "blocks": [
                {
                    "kind": b.kind,
                    "tier": b.tier.value,
                    "chars": b.chars,
                    "source": b.source,
                    "priority": b.priority,
                }
                for b in self.blocks
            ],
            "omitted": [{"kind": k, "reason": r, "chars": c} for k, r, c in self.omitted],
            "on_demand": ["architecture", "endpoints", "tools", "memory", "docs", "evidence"],
        }


def fit(blocks: list[ContextBlock], budget: ContextBudget) -> ContextPlan:
    """Pack each tier by priority, trimming only blocks that allow trimming.

    Priority ordering is stable: equal priorities keep collection order, so the
    rendered prompt does not shuffle between turns and break provider caches.
    """
    kept: list[ContextBlock] = []
    omitted: list[tuple[str, str, int]] = []
    for tier in (Tier.CORE, Tier.TASK):
        limit = budget.for_tier(tier)
        used = 0
        ranked = sorted(
            (b for b in blocks if b.tier is tier),
            key=lambda b: -b.priority,
        )
        ordered = _stable_by_priority(ranked)
        for block in ordered:
            remaining = limit - used
            if remaining <= 0:
                omitted.append((block.kind, "tier budget exhausted", block.chars))
                continue
            if block.chars <= remaining:
                kept.append(block)
                used += block.chars
                continue
            if block.truncate_at and remaining >= MIN_TRUNCATION:
                cut = min(block.truncate_at, remaining)
                trimmed = _truncate(block, cut)
                kept.append(trimmed)
                used += trimmed.chars
                omitted.append((block.kind, f"truncated {block.chars}→{trimmed.chars}", block.chars - trimmed.chars))
                continue
            omitted.append((block.kind, "no room (block is not truncatable)", block.chars))
    kept.sort(key=lambda b: (list(Tier).index(b.tier), -b.priority))
    return ContextPlan(blocks=kept, omitted=omitted, budget=budget)


MIN_TRUNCATION = 240


def _stable_by_priority(blocks: list[ContextBlock]) -> list[ContextBlock]:
    seen: dict[int, list[ContextBlock]] = {}
    for b in blocks:
        seen.setdefault(b.priority, []).append(b)
    out: list[ContextBlock] = []
    for priority in sorted(seen, reverse=True):
        out.extend(seen[priority])
    return out


def _truncate(block: ContextBlock, limit: int) -> ContextBlock:
    if block.kind in _LINE_BLOCKS:
        keep_lines: list[str] = []
        used = 0
        for line in block.text.splitlines():
            if used + len(line) + 1 > limit:
                break
            keep_lines.append(line)
            used += len(line) + 1
        text = "\n".join(keep_lines) or block.text[:limit]
    else:
        text = block.text[:limit]
    return ContextBlock(
        kind=block.kind,
        tier=block.tier,
        text=text,
        source=block.source,
        priority=block.priority,
        truncate_at=block.truncate_at,
    )


#: Blocks where cutting mid-line would change the meaning of a rule.
_LINE_BLOCKS = frozenset({"constraints", "capabilities", "identity", "task"})
