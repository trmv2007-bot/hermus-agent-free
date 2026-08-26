"""Context compaction — keep long sessions under a token budget.

jcode-style: truncate tool / observation payloads first so the model keeps
the plan and recent reasoning. Default trigger is 90% of the budget.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _len(text: Any) -> int:
    return len(str(text or ""))


def compact_messages(
    messages: List[Dict[str, Any]],
    budget_chars: int = 24_000,
    keep_recent: int = 6,
    tool_limit: int = 1200,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return compacted messages plus a report.

    System + last ``keep_recent`` turns stay intact except tool payloads
    are capped. Older tool/user observations are truncated harder.
    """
    if not messages:
        return messages, {"compacted": False, "chars": 0, "budget": budget_chars}

    total = sum(_len(m.get("content")) for m in messages)
    if total < int(budget_chars * 0.9):
        return messages, {"compacted": False, "chars": total, "budget": budget_chars}

    out: List[Dict[str, Any]] = []
    n = len(messages)
    cutoff = max(1, n - keep_recent)
    dropped = 0
    for i, msg in enumerate(messages):
        copy = dict(msg)
        content = str(copy.get("content") or "")
        role = copy.get("role")
        limit = tool_limit if i >= cutoff else max(240, tool_limit // 3)
        is_obs = role in ("tool", "user") and (
            content.startswith("Tool ") or "Tool results" in content[:40]
        )
        if is_obs and len(content) > limit:
            copy["content"] = content[:limit] + f"\n...(compacted {len(content) - limit} chars)"
            dropped += len(content) - limit
        elif i < cutoff and role != "system" and len(content) > 400:
            copy["content"] = content[:400] + f"\n...(compacted {len(content) - 400} chars)"
            dropped += len(content) - 400
        out.append(copy)

    new_total = sum(_len(m.get("content")) for m in out)
    return out, {
        "compacted": True,
        "chars_before": total,
        "chars_after": new_total,
        "dropped": dropped,
        "budget": budget_chars,
    }
