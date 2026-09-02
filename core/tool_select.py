"""Per-turn tool selection: send the tools this task could plausibly use.

Why this exists
---------------
Every agent call ships the whole tool catalog. Measured on a default install:

    full agent call : 19,876 prompt tokens
    tools-free call :  1,605 prompt tokens

179 tool schemas are ~18,300 of those tokens — **93% of every request**, re-sent
on every single step of the ReAct loop. That is the dominant cost in the system,
and it is paid even to answer "what time is it". It is also the dominant
*latency*: prefill scales with prompt size, so a 20K-token prompt is what makes
time-to-first-token several seconds instead of ~1s.

Selecting a relevant subset per turn removes most of it.

Why the escape hatch matters
----------------------------
Subsetting is a bet: we guess which tools a task needs. When the guess is wrong
the model cannot call a tool it was never offered, and it will not complain — it
will just quietly produce a worse answer. That is a silent capability loss, which
is the worst failure mode available here.

So every restricted offer includes ``expand_tools``. If the model decides it
needs something it cannot see, it calls that, and the agent immediately re-offers
the full catalog for the rest of the turn. Guessing wrong then costs one round
trip instead of a silently degraded result.
"""
from __future__ import annotations

import re
from typing import Any, Optional

#: The tool the model calls when the offered subset is missing something.
EXPAND_TOOL_NAME = "expand_tools"

EXPAND_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": EXPAND_TOOL_NAME,
        "description": (
            "Call this ONLY if you need a capability that is not in the tool list you "
            "were given. It immediately makes the full tool catalog available for the "
            "rest of this turn. Do not call it if you can already do the task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string",
                           "description": "One short line: which capability you need."},
            },
            "required": ["reason"],
        },
    },
}

#: Always offered, whatever the task looks like. These are the tools an agent
#: needs to reason, read, write and report — dropping them would break ordinary
#: turns, and they are cheap relative to the catalog.
CORE_TOOLS: frozenset[str] = frozenset({
    "file_read", "file_write", "file_edit", "file_search",
    "shell_execute", "web_search", "web_read",
    "memory_search", "memory_remember", "memory_recall",
    "task_status", "delegate_tasks",
})

#: Extra weight for a match in the tool *name* rather than its description. A
#: name match ("browser_click" vs "click the button") is much stronger evidence
#: than a common word appearing in a long description.
_NAME_WEIGHT = 3

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Words that appear in almost every tool description and therefore carry no
#: discriminating signal. Without filtering these, everything matches everything.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "be", "this", "that", "it", "its", "as", "by", "at", "from", "into",
    "tool", "tools", "use", "using", "used", "returns", "return", "get", "gets",
    "current", "status", "value", "values", "any", "all", "can", "will", "when",
    "you", "your", "hermus", "result", "results", "data", "via", "or the",
})


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall(str(text or "").lower())
            if len(t) > 2 and t not in _STOPWORDS}


def _name_tokens(name: str) -> set[str]:
    """Split snake_case tool names into matchable words."""
    return {t for t in _WORD_RE.findall(str(name or "").lower()) if len(t) > 2}


def _tool_name(tool: dict[str, Any]) -> str:
    return str(((tool or {}).get("function") or {}).get("name") or "")


def _tool_text(tool: dict[str, Any]) -> str:
    fn = (tool or {}).get("function") or {}
    return f"{fn.get('name') or ''} {fn.get('description') or ''}"


def score_tool(tool: dict[str, Any], tokens: set[str]) -> int:
    """Relevance of one tool to a request. 0 means no evidence either way."""
    if not tokens:
        return 0
    name = _tool_name(tool)
    name_hits = tokens & _name_tokens(name)
    desc_hits = tokens & _tokens(_tool_text(tool))
    return len(name_hits) * _NAME_WEIGHT + max(0, len(desc_hits) - len(name_hits))


def select_tools(
    tools: list[dict[str, Any]],
    text: str,
    *,
    limit: Optional[int] = None,
    core: Optional[frozenset[str]] = None,
    include_expander: bool = True,
) -> list[dict[str, Any]]:
    """Return the tool schemas worth sending for this request.

    Falls back to the full catalog rather than guessing whenever a guess would be
    unsafe: no tools at all, no usable words in the request, or a limit that
    would not actually restrict anything.
    """
    if not tools:
        return []
    from core.config import config

    if limit is None:
        # Caller did not pin a limit: honour the config, and treat "disabled" as
        # "send everything" rather than applying a stale default.
        if not getattr(config, "tool_subset_enabled", False):
            return list(tools)
        limit = int(getattr(config, "tool_subset_limit", 0) or 0)
    if limit <= 0 or limit >= len(tools):
        # 0 means "off", and a limit at/above the catalog size is a no-op.
        return list(tools)

    tokens = _tokens(text)
    if not tokens:
        # Nothing to match on. Restricting here would be a coin flip, and a wrong
        # coin flip silently removes capability — so send everything.
        return list(tools)

    core_names = core if core is not None else CORE_TOOLS
    scored = sorted(
        ((score_tool(t, tokens), _tool_name(t), t) for t in tools),
        key=lambda row: (-row[0], row[1]),
    )

    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(tool: dict[str, Any]) -> None:
        name = _tool_name(tool)
        if name and name not in seen:
            seen.add(name)
            chosen.append(tool)

    # Core tools first: they must survive the cut even at score 0.
    for _score, name, tool in scored:
        if name in core_names:
            add(tool)
    # Then everything with any evidence of relevance.
    for score, _name, tool in scored:
        if score > 0:
            add(tool)

    if len(chosen) > limit:
        # Still too many. Split the budget: the core set can never take more than
        # half the slots, or a small limit would crowd out the very tools the
        # request is about. Whatever is left goes to the highest scorers, and any
        # unused room is topped up from the leftovers.
        core_list = [t for t in chosen if _tool_name(t) in core_names]
        scored_list = [t for t in chosen if _tool_name(t) not in core_names]
        core_cap = min(len(core_list), max(1, limit // 2))
        take_core = core_list[:core_cap]
        take_scored = scored_list[: limit - len(take_core)]
        chosen = take_core + take_scored
        if len(chosen) < limit:
            rest = core_list[core_cap:] + scored_list[len(take_scored):]
            chosen += rest[: limit - len(chosen)]

    if include_expander and len(chosen) < len(tools):
        chosen = [*chosen, EXPAND_TOOL_SCHEMA]
    return chosen


def selection_report(tools: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    """Small telemetry payload: how much of the catalog we actually sent."""
    return {
        "offered": len(selected),
        "available": len(tools),
        "reducible": len(selected) < len(tools),
        "expander_offered": any(_tool_name(t) == EXPAND_TOOL_NAME for t in selected),
    }
