"""The store has to be semantic, current, and silent when it has nothing.

Three separate failures are covered here, and the third is the one that is
easiest to ship by accident.

1. A hash "embedding" is not an embedding. It scored a filler-heavy query
   *above* the correct document, because the longer query carried more tokens
   that collided. Retrieval that confidently returns the wrong thing is worse
   than no retrieval, because it looks like it is working.
2. A backend change strands every stored vector. Cosine across mismatched
   dimensions returns 0.0, not an error, so a half-migrated store still answers
   via BM25 and only the vector half goes quietly dead.
3. Stale rows must produce silence, not context. If a store that cannot compare
   vectors still injects documents, the model quotes notes the search never
   found -- the exact failure that made the earlier answers cite the internet
   for something sitting in the local database.
"""

from __future__ import annotations

import pytest

from core.embeddings import EmbeddingStore, _hash_embed
from gateway.chat_turn import _retrieve_context


@pytest.fixture()
def store(tmp_path) -> EmbeddingStore:
    return EmbeddingStore(db_path=str(tmp_path / "e.db"), model="definitely-not-installed")


def test_a_hash_fallback_is_not_pretending_to_be_semantic(store):
    """Pinned so the fallback stays labelled a fallback."""
    assert _hash_embed("anything") is not None
    info = store.backend_info()
    assert info["backend"] in ("ollama", "hash")
    if info["backend"] == "hash":
        assert info["model"] == "hash-fallback", "a hash backend must never be reported as a model"


def test_required_vram_budget_still_holds_after_the_reindex_change():
    """The recommender's honesty rules must not be quietly relaxed elsewhere."""
    from core.model_recommender import load_catalog

    for e in load_catalog():
        assert e["required_vram_gb"] > e["download_gb"]


def test_cosine_across_mismatched_dimensions_is_zero_not_a_crash():
    from core.embeddings import _cosine

    assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0]) == 0.0


def test_stale_rows_are_counted_and_reported(store):
    """A backend swap must be visible, not inferred from odd results."""
    store.add_text("a thing worth remembering", {"source": "t"})
    assert store.stale_count() == 0, "fresh rows are not stale"

    # Simulate a dimension change the way a backend upgrade would.
    from core.db_registry import using

    with using(store.db_path, owner="test") as conn:
        conn.execute("UPDATE embeddings SET dim = 999")
        conn.commit()

    assert store.stale_count() == 1
    info = store.backend_info()
    assert info["needs_reindex"] is True
    assert info["stale"] == 1, "a half-migrated store must say so in its own status"


def test_reindex_restores_rows_without_deleting_content(store):
    store.add_text("the launch codes are mango-seven-ninety", {"source": "t"})
    from core.db_registry import using

    with using(store.db_path, owner="test") as conn:
        conn.execute("UPDATE embeddings SET dim = 999")
        conn.commit()

    out = store.reindex()
    assert out["reindexed"] == 1
    assert out["remaining_stale"] == 0
    assert store.stale_count() == 0


def test_retrieval_is_silent_when_the_store_is_stale(store, monkeypatch):
    """The failure mode that produced confidently wrong answers.

    Stale rows are unfindable, so quoting them is quoting something the search
    never matched. Return nothing instead.
    """
    store.add_text("the launch codes are mango-seven-ninety", {"source": "t"})

    class Fake:
        @staticmethod
        def hybrid_search(q, limit=3):
            return {"results": [{"content": "the launch codes are mango-seven-ninety", "score": 0.9}]}

        @staticmethod
        def stale_count():
            return 7

    monkeypatch.setitem(__import__("sys").modules, "core.embeddings", type("m", (), {"embedding_store": Fake}))
    assert _retrieve_context("what are the launch codes?") == ""


def test_retrieval_is_silent_on_a_weak_match(store, monkeypatch):
    """Below the bar, say nothing. Stuffing low-scoring context in teaches the
    model that everything it is handed is relevant."""
    class Fake:
        @staticmethod
        def hybrid_search(q, limit=3):
            return {"results": [{"content": "something unrelated", "score": 0.05}]}

        @staticmethod
        def stale_count():
            return 0

    monkeypatch.setitem(__import__("sys").modules, "core.embeddings", type("m", (), {"embedding_store": Fake}))
    assert _retrieve_context("what are the launch codes?") == ""


def test_retrieval_injects_a_real_hit(store, monkeypatch):
    class Fake:
        @staticmethod
        def hybrid_search(q, limit=3):
            return {"results": [{"content": "the launch codes are mango-seven-ninety", "score": 0.81}]}

        @staticmethod
        def stale_count():
            return 0

    monkeypatch.setitem(__import__("sys").modules, "core.embeddings", type("m", (), {"embedding_store": Fake}))
    out = _retrieve_context("what are the launch codes?")
    assert "mango-seven-ninety" in out
    assert "not the web" in out, "the model must know where this came from"


def test_a_trivially_short_question_skips_retrieval(store, monkeypatch):
    """'ok' should not cost a database round trip."""
    calls = []

    class Fake:
        @staticmethod
        def hybrid_search(q, limit=3):
            calls.append(q)
            return {"results": []}

        @staticmethod
        def stale_count():
            return 0

    monkeypatch.setitem(__import__("sys").modules, "core.embeddings", type("m", (), {"embedding_store": Fake}))
    assert _retrieve_context("ok") == ""
    assert calls == []
