"""Context compaction — keep long sessions under a token budget.

The first version counted characters and ran before the agent had assembled the
trajectory, so in production it only ever saw the system message and never
compacted real history. It could also split an ``assistant(tool_calls)`` message
from its ``tool`` results, which makes a provider reject the turn outright.

Rules enforced here:

* the budget is measured in **tokens** (``core.token_counter`` with a char
  heuristic fallback), not characters;
* the leading system message, the final user message and the newest
  ``keep_recent`` messages are never modified;
* ``assistant(tool_calls)`` plus the ``tool`` messages it produced are one
  atomic group — either the whole group survives or the whole group is elided
  down to a stub, never a tool result without its call;
* elision keeps the head and the tail of a payload (the useful context at both
  ends) and is idempotent: a second pass over the same list is a no-op.
"""

from __future__ import annotations

from typing import Any

from ..token_counter import TokenCounter

_MARKER = "\n...(compacted "


def _text(message: dict[str, Any]) -> str:
    return str(message.get("content") or "")


def _elide(text: str, limit: int) -> str:
    """Keep head + tail of ``text`` within roughly ``limit`` characters."""
    if limit <= 0 or len(text) <= limit:
        return text
    head = max(80, int(limit * 0.7))
    tail = max(40, limit - head - 40)
    removed = len(text) - head - tail
    return f"{text[:head]}{_MARKER}{removed} chars — re-read if needed)...\n{text[-tail:]}"


def _groups(messages: list[dict[str, Any]]) -> list[list[int]]:
    """Index groups where an assistant tool call stays with its tool results."""
    groups: list[list[int]] = []
    i = 0
    n = len(messages)
    while i < n:
        group = [i]
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                group.append(j)
                j += 1
            i = j
        else:
            i += 1
        groups.append(group)
    return groups


def _protected(messages: list[dict[str, Any]], keep_recent: int) -> set[int]:
    """Indices that must survive verbatim: system preamble, newest turns, final user."""
    n = len(messages)
    protected: set[int] = set()
    i = 0
    while i < n and messages[i].get("role") == "system":
        protected.add(i)
        i += 1
    for idx in range(max(0, n - max(1, keep_recent)), n):
        protected.add(idx)
    for idx in range(n - 1, -1, -1):
        if messages[idx].get("role") == "user":
            protected.add(idx)
            break
    return protected


def _count(counter: TokenCounter, messages: list[dict[str, Any]]) -> int:
    try:
        return int(counter.count_messages(messages))
    except Exception:  # counting must never break a turn
        return sum(len(_text(m)) for m in messages) // 4


def compact_messages(
    messages: list[dict[str, Any]],
    budget_tokens: int = 6000,
    keep_recent: int = 6,
    tool_limit: int = 1200,
    *,
    counter: TokenCounter | None = None,
    model: str = "",
    budget_chars: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return compacted copies of ``messages`` plus a report.

    The input list and its message dicts are never mutated — the caller keeps
    its original trajectory even when the prompt was compacted.

    ``budget_chars`` is accepted for callers written against the old
    character-based budget; it is converted at the usual ~4 chars/token.
    """
    if budget_chars:
        budget_tokens = max(256, int(budget_chars) // 4)
    copies = [dict(m) for m in messages]
    if not copies:
        return copies, {"compacted": False, "tokens": 0, "budget_tokens": budget_tokens}

    token_counter = counter or TokenCounter(model or "gpt-3.5-turbo")
    budget = max(256, int(budget_tokens))
    before = _count(token_counter, copies)
    if before <= int(budget * 0.9):
        return copies, {
            "compacted": False,
            "tokens": before,
            "tokens_before": before,
            "tokens_after": before,
            "budget_tokens": budget,
            "chars_before": sum(len(_text(m)) for m in copies),
            "chars_after": sum(len(_text(m)) for m in copies),
            "elided": 0,
        }

    protected = _protected(copies, keep_recent)
    elided_chars = 0
    elided_messages = 0
    chars_before = sum(len(_text(m)) for m in copies)

    def shrink(limit: int, force_stub: bool = False) -> None:
        nonlocal elided_chars, elided_messages
        for group in _groups(copies):
            if any(idx in protected for idx in group):
                continue
            for idx in group:
                msg = copies[idx]
                content = _text(msg)
                if not content or _MARKER in content:
                    continue
                if force_stub:
                    cap = 120
                else:
                    cap = tool_limit if msg.get("role") in ("tool", "user") else max(600, tool_limit)
                if len(content) <= cap:
                    continue
                msg["content"] = _elide(content, cap)
                elided_chars += len(content) - len(str(msg["content"]))
                elided_messages += 1

    # Pass 1: normal caps. Pass 2 (only if still over): hard stubs. A second
    # call over an already compacted list elides nothing and reports
    # ``compacted: False``, which is what makes this idempotent.
    shrink(tool_limit)
    after = _count(token_counter, copies)
    if after > budget:
        shrink(120, force_stub=True)
        after = _count(token_counter, copies)

    chars_after = sum(len(_text(m)) for m in copies)
    return copies, {
        "compacted": elided_messages > 0,
        "tokens": after,
        "tokens_before": before,
        "tokens_after": after,
        "budget_tokens": budget,
        "over_budget": after > budget,
        "elided": elided_messages,
        "elided_chars": elided_chars,
        "chars_before": chars_before,
        "chars_after": chars_after,
        "kept_recent": keep_recent,
    }


__all__ = ["compact_messages"]
