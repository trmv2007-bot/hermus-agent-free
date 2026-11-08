"""Cascade memory recall — cheap lexical first, then embeddings, then typed memory.

The agent used to run its own ``embedding_store.hybrid_search`` block *and*
this cascade on the same turn: two independent recall paths, two latencies,
duplicated snippets in the prompt. This module is now the single per-turn
recall owner; the agent consumes its result instead of searching again.

Cost control: each stage has a deadline (``harness_recall_timeout_ms``), the
summary is trimmed to ``memory_budget_tokens`` (~4 chars/token), and the result
reports which stages actually ran so the Control Room can show it honestly.
"""

from __future__ import annotations

import time
from typing import Any

from ..config import config


def _deadline_reached(deadline: float) -> bool:
    return time.monotonic() > deadline


def _trim(summary: str, budget_chars: int) -> str:
    if budget_chars <= 0 or len(summary) <= budget_chars:
        return summary
    lines: list[str] = []
    used = 0
    for line in summary.splitlines():
        if used + len(line) + 1 > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if lines else summary[:budget_chars]


def cascade_recall(
    query: str,
    limit: int = 5,
    project: str = "",
    *,
    timeout_ms: int | None = None,
    budget_tokens: int | None = None,
) -> dict[str, Any]:
    """Return ``{hits, summary, mode, count, elapsed_ms, degraded, stages}``.

    Never raises: a broken backend degrades the recall, it does not break the
    turn. ``mode`` names the strongest stage that contributed a hit.
    """
    started = time.monotonic()
    limit = max(1, min(int(limit or 5), 12))
    timeout_ms = int(timeout_ms if timeout_ms is not None else getattr(config, "harness_recall_timeout_ms", 800))
    budget_tokens = int(budget_tokens if budget_tokens is not None else getattr(config, "memory_budget_tokens", 600))
    budget_chars = max(400, budget_tokens * 4)
    deadline = started + max(0.05, timeout_ms / 1000.0)

    hits: list[dict[str, Any]] = []
    stages: list[str] = []
    degraded = False
    source = "empty"
    query = str(query or "").strip()

    if not query:
        return {
            "query": query,
            "hits": [],
            "count": 0,
            "mode": "empty",
            "summary": "",
            "elapsed_ms": 0,
            "degraded": False,
            "stages": [],
        }

    # Stage 1 — lexical session search (SQLite FTS5, cheapest).
    if not _deadline_reached(deadline):
        stages.append("fts")
        try:
            from ..memory import memory

            for row in memory.search_sessions(query, limit=limit, project=project or None) or []:
                content = row.get("content") or row.get("value") or str(row)
                hits.append({"content": str(content)[:400], "score": row.get("score", 0.5), "source": "fts"})
            if hits:
                source = "fts"
        except Exception:
            degraded = True
    else:
        degraded = True

    # Stage 2 — embeddings, only when lexical hits are weak.
    if len(hits) < max(2, limit // 2) and not _deadline_reached(deadline):
        stages.append("embed")
        try:
            from ..embeddings import embedding_store

            if embedding_store.available():
                for row in embedding_store.search(query, limit=limit).get("results") or []:
                    hits.append(
                        {
                            "content": str(row.get("content") or "")[:400],
                            "score": row.get("score", 0.0),
                            "source": "embed",
                        }
                    )
                if hits:
                    source = "cascade"
        except Exception:
            degraded = True
    elif len(hits) < max(2, limit // 2):
        degraded = True

    # Stage 3 — typed memory (Memory2 lessons/skills/facts).
    if len(hits) < limit and not _deadline_reached(deadline):
        stages.append("memory2")
        try:
            from ..memory import memory

            for row in memory.recall(query, limit=limit, project=project or None) or []:
                hits.append(
                    {
                        "content": str(row.get("content") or "")[:400],
                        "score": row.get("score", 0.0),
                        "source": f"memory2:{row.get('kind', '')}",
                    }
                )
            if len(stages) == 1 and hits:
                source = "memory2"
            elif hits:
                source = "cascade"
        except Exception:
            degraded = True
    elif len(hits) < limit:
        degraded = True

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for hit in hits:
        key = hit["content"][:80]
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    unique = unique[:limit]

    summary = _trim(
        "\n".join(f"- ({h.get('source')}) {h['content'][:180]}" for h in unique),
        budget_chars,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "query": query,
        "hits": unique,
        "count": len(unique),
        "mode": source,
        "summary": summary,
        "elapsed_ms": elapsed_ms,
        "degraded": degraded or bool(_deadline_reached(deadline)),
        "stages": stages,
    }


__all__ = ["cascade_recall"]
