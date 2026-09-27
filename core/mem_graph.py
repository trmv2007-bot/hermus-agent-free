"""
Derive a graph from the memory store.

`core/memory2.py` stores NO link or edge table — there is no `link`, `edge`,
`neighbor` or `graph` method anywhere in it, and the schema has only
`memories`, `memory_access` and `memory_tombstones`. So an Obsidian-style
constellation cannot be read from disk. It has to be derived, and anything
derived has to say so.

Three signals, all real and all cheap:

  same project  — memories scoped to the same project belong together
  same kind     — working/episodic/semantic/procedural/project are real
                  categories the store itself defines
  shared tokens — the store already tokenizes for dedupe (_dedupe_key), so
                  content overlap is the store's own notion of sameness

No embeddings, no similarity model, no invented neighbours. Two memories with
nothing in common get no edge, and the UI is told edges are inferred.
"""

from __future__ import annotations

import re
from typing import Any

# Words that appear in almost every memory and would otherwise turn the graph
# into a hairball. These are dropped before any overlap is measured.
_STOP = frozenset(
    """
    a an the and or but if then than that this these those is are was were be
    been being to of in on at for with from by as it its it's do does did done
    not no so such can could should would will shall may might must about into
    over under again further once here there when where why how all any both
    each few more most other some only own same too very just also has have
    had having you your yours he him his she her hers they them their we our
    us i me my mine
    """.split()
)

_TOKEN = re.compile(r"[a-z0-9_]{3,}")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP}


def _shared(a: set[str], b: set[str]) -> int:
    return len(a & b)


def build_graph(
    memories: list[dict[str, Any]],
    *,
    max_nodes: int = 400,
    max_edges: int = 1200,
) -> dict[str, Any]:
    """Turn a flat memory list into nodes + inferred edges.

    Node radius is driven by `importance` (the store's own field) clamped into a
    readable range, and node colour by `kind` so the five typed stores are
    visually separable the way Obsidian separates by tag.
    """
    rows = memories[:max_nodes]
    kinds = sorted({str(r.get("kind") or "semantic") for r in rows})

    nodes: list[dict[str, Any]] = []
    token_sets: list[set[str]] = []

    for row in rows:
        content = str(row.get("content") or row.get("text") or "")
        toks = _tokens(content)
        token_sets.append(toks)

        importance = row.get("importance")
        try:
            importance = float(importance)
        except (TypeError, ValueError):
            importance = 5.0
        # 0..10 in the store; map to a 5..15px radius.
        radius = 5.0 + (max(0.0, min(10.0, importance)) / 10.0) * 10.0

        nodes.append(
            {
                "id": row.get("id"),
                "label": (content[:80] or "(empty)"),
                "full": content,
                "kind": str(row.get("kind") or "semantic"),
                "project": row.get("project") or "default",
                "importance": importance,
                "radius": round(radius, 2),
                "pinned": bool(row.get("pinned")),
                "ts": row.get("ts"),
                "tokens": len(toks),
            }
        )

    edges: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]

            same_project = (a.get("project") or "default") == (b.get("project") or "default")
            same_kind = (a.get("kind") or "") == (b.get("kind") or "")
            overlap = _shared(token_sets[i], token_sets[j])

            # A single shared word is noise, not a relationship.
            if overlap < 2 and not (same_project and same_kind):
                continue

            pair = (nodes[i]["id"], nodes[j]["id"])
            if pair in seen:
                continue
            seen.add(pair)

            # weight 0..1 — how strong the claim that these belong together is
            weight = min(1.0, (overlap / 12.0) * 0.7 + (0.3 if same_kind else 0.0))
            reasons = []
            if same_project:
                reasons.append("project")
            if same_kind:
                reasons.append("kind")
            if overlap:
                reasons.append(f"{overlap} shared words")

            edges.append(
                {
                    "source": pair[0],
                    "target": pair[1],
                    "weight": round(weight, 3),
                    "reasons": reasons,
                }
            )

    # Strongest first, then trimmed, so the cap keeps the meaningful edges.
    edges.sort(key=lambda e: e["weight"], reverse=True)
    truncated_edges = len(edges) > max_edges
    edges = edges[:max_edges]

    return {
        "nodes": nodes,
        "edges": edges,
        "kinds": kinds,
        # Said out loud so the UI never draws inference as fact.
        "edges_are_inferred": True,
        "note": (
            "The memory store keeps no link table. Edges are derived from "
            "shared project, shared kind and shared content words."
        ),
        "truncated_nodes": max(0, len(memories) - len(rows)),
        "truncated_edges": truncated_edges,
    }
