"""Cascade memory recall — cheap lexical first, then embeddings.

jcode queries a memory graph each turn and only spends embedding cost
when lexical hits are weak. Results from turn N are ready at turn N+1.
"""
from __future__ import annotations

from typing import Any


def cascade_recall(query: str, limit: int = 5, project: str = "") -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    source = "empty"

    try:
        from ..memory import memory

        fts = memory.search_sessions(query, limit=limit, project=project or None)
        for row in fts or []:
            content = row.get("content") or row.get("value") or str(row)
            hits.append({"content": str(content)[:400], "score": row.get("score", 0.5), "source": "fts"})
        if hits:
            source = "fts"
    except Exception:
        pass

    if len(hits) < max(2, limit // 2):
        try:
            from ..embeddings import embedding_store

            if embedding_store.available():
                sem = embedding_store.search(query, limit=limit)
                for row in (sem.get("results") or []):
                    hits.append({
                        "content": str(row.get("content") or "")[:400],
                        "score": row.get("score", 0.0),
                        "source": "embed",
                    })
                source = "cascade" if hits else source
        except Exception:
            pass

    if len(hits) < limit:
        try:
            from ..memory2 import memory2

            extra = memory2.recall(query, limit=limit, project=project or None)
            for row in extra or []:
                hits.append({
                    "content": str(row.get("content") or "")[:400],
                    "score": row.get("score", 0.0),
                    "source": f"memory2:{row.get('kind', '')}",
                })
            if extra:
                source = "cascade"
        except Exception:
            pass

    # de-dupe by prefix
    seen = set()
    unique = []
    for h in hits:
        key = h["content"][:80]
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    unique = unique[:limit]
    summary = "\n".join(f"- ({h.get('source')}) {h['content'][:180]}" for h in unique)
    return {"query": query, "hits": unique, "count": len(unique), "mode": source, "summary": summary}
