"""The memory constellation must not invent relationships.

`core/mem_graph.py` produces the nodes and edges for the Obsidian-style 3D
memory view. The store keeps no link table, so every edge is inferred. These
tests pin the two properties that keep that honest: related memories connect,
and unrelated ones do not.
"""

from __future__ import annotations

from core.mem_graph import build_graph


def _row(mid, content, kind="semantic", project="default", importance=5.0):
    return {
        "id": mid,
        "content": content,
        "kind": kind,
        "project": project,
        "importance": importance,
    }


def test_related_memories_get_an_edge():
    graph = build_graph(
        [
            _row(1, "the trading bot uses xgboost with walk forward validation", project="trading"),
            _row(2, "xgboost walk forward validation for the trading signals", project="trading"),
        ]
    )
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert {edge["source"], edge["target"]} == {1, 2}
    assert "project" in edge["reasons"]
    assert edge["weight"] > 0


def test_unrelated_memories_stay_isolated():
    graph = build_graph(
        [
            _row(1, "sqlite fts5 index powers the knowledge search", kind="project", project="hermus"),
            _row(2, "ordered lunch from the corner shop", kind="episodic", project="life"),
        ]
    )
    assert len(graph["nodes"]) == 2
    assert graph["edges"] == []


def test_edges_are_declared_inferred():
    """§4: a graph that looks stored but is inferred is a false success."""
    graph = build_graph([_row(1, "hello world"), _row(2, "hello there friend")])
    assert graph["edges_are_inferred"] is True
    assert "no link table" in graph["note"]


def test_single_shared_word_is_not_a_relationship():
    """One common word is noise. Two is the floor."""
    rows = [
        _row(1, "deploy the service to production", kind="procedural", project="infra"),
        _row(2, "rotate the production certificate tonight", kind="episodic", project="life"),
    ]
    assert build_graph(rows)["edges"] == []


def test_importance_drives_radius():
    small = build_graph([_row(1, "a", importance=0.0)])["nodes"][0]["radius"]
    large = build_graph([_row(1, "a", importance=10.0)])["nodes"][0]["radius"]
    assert large > small


def test_kinds_are_reported_for_colouring():
    graph = build_graph(
        [_row(1, "a b c", kind="working"), _row(2, "d e f", kind="episodic")]
    )
    assert graph["kinds"] == ["episodic", "working"]
