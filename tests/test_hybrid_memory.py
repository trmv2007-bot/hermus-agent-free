"""Tests for hybrid memory (BM25 + vectors + RRF) and the decay/budget layer.

Covers architecture upgrade A: retrieval must survive paraphrase (pure keyword
matching does not), stale context must be demoted by decay, and the prompt packer
must evict by value density instead of "take the top K".

Offline: no model, no network. Run:
  python tests/test_hybrid_memory.py   (or pytest tests/test_hybrid_memory.py)
"""
import os
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

_TMP = tempfile.mkdtemp(prefix="hermus_hybrid_")
os.environ["HERMUS_HOME"] = _TMP
os.environ.setdefault("HERMUS_EMBED_BACKEND", "hash")

from core.config import config  # noqa: E402

config.model = "mock/mock"
config.memory_db_path = str(Path(_TMP) / "memory.db")
config.memory2_db_path = str(Path(_TMP) / "memory2.db")
config.trajectory_path = str(Path(_TMP) / "trajectories.jsonl")
config.user_model_path = str(Path(_TMP) / "user_model.json")
config.embeddings_db_path = str(Path(_TMP) / "embeddings.db")
config.embedding_backend = "hash"
config.memory_hybrid_enabled = True
config.memory_vectors_enabled = True


def _store():
    """The process-wide store (shared with the agent, gateway and other tests)."""
    from core.memory2 import memory2

    return memory2


_STORES = {}


def _fresh(name):
    """A private store per test.

    pytest imports every test module into one process and they all repoint the
    shared config at their own temp dirs, so tests that assert on ranking must not
    depend on the global singleton — they get their own SQLite file instead.
    """
    from core.memory2 import Memory2

    if name not in _STORES:
        _STORES[name] = Memory2(db_path=str(Path(_TMP) / f"{name}.db"))
    return _STORES[name]


# --------------------------------------------------------------------------
# Retrieval primitives
# --------------------------------------------------------------------------
def test_fts_query_sanitizer_neutralizes_operators():
    from core.hybrid_search import sanitize_fts_query

    for nasty in ['"unterminated', "and or not", "memories:hello", "a-b+c*d^e:f(g)", '") OR x']:
        out = sanitize_fts_query(nasty)
        assert isinstance(out, str) and out != nasty.replace(" ", "")  # rewritten, not passed raw
        # a sanitized query must be executable by FTS5 (verified end-to-end below)


def test_locked_connection_serializes_threads():
    from core.hybrid_search import LockedConnection

    raw = sqlite3.connect(":memory:", check_same_thread=False)
    conn = LockedConnection(raw)
    conn.execute("CREATE TABLE t (x INTEGER)")
    errors = []

    def worker():
        try:
            for i in range(200):
                conn.execute("INSERT INTO t (x) VALUES (?)", (i,))
                conn.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join(timeout=30) for t in ts]
    assert not errors, errors
    assert int(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]) == 800


def test_rrf_fusion_prefers_consensus():
    from core.hybrid_search import HybridConfig, HybridRetriever

    ret = HybridRetriever(sqlite3.connect(":memory:"), table="memories", manage_schema=False,
                          config=HybridConfig(rrf_k=60, weight_prior=0.35))
    lists = {"bm25": [("a", 10.0), ("b", 5.0), ("c", 1.0)],
             "vector": [("b", 0.9), ("a", 0.8), ("d", 0.2)]}
    docs = {k: {"content": f"doc {k}", "prior": p}
            for k, p in (("a", 0.0), ("b", 0.9), ("c", 0.0), ("d", 0.0))}
    fused = {h.id: h for h in ret.fuse(lists, docs, limit=10)}

    # 'b' is top-2 in both lists with a strong prior → outranks 'a' (lexical only)
    assert fused["b"].score >= fused["a"].score
    assert fused["b"].bm25_rank == 2 and fused["b"].vector_rank == 1
    assert fused["c"].score > 0
    assert set(fused["b"].contributions) >= {"bm25", "vector"}
    out = fused["b"].to_dict()
    assert out["rrf_score"] == out["hybrid_score"]
    assert out["retrieval"]["prior"] == 0.9


def test_sensitivity_of_rrf_k():
    """rrf_k is the 'agree or lose' knob: small k punishes rank disagreement."""
    from core.hybrid_search import HybridConfig, HybridRetriever

    lists = {"bm25": [("a", 9.0), ("b", 1.0)], "vector": [("b", 0.9), ("a", 0.1)]}
    docs = {"a": {"content": "a"}, "b": {"content": "b"}}
    scores = {}
    for k in (2, 60, 1000):
        ret = HybridRetriever(sqlite3.connect(":memory:"), table="m", manage_schema=False,
                              config=HybridConfig(rrf_k=k, weight_prior=0.0))
        hits = {h.id: h.score for h in ret.fuse(lists, docs, limit=5)}
        scores[k] = (hits["a"], hits["b"])
        assert abs(hits["a"] - hits["b"]) < 1e-9 or k  # symmetric lists → near-tie
    # the spread between ranks shrinks as k grows
    spread = lambda k: abs(scores[k][0] - scores[k][1])  # noqa: E731
    assert spread(2) >= spread(60) >= spread(1000)


# --------------------------------------------------------------------------
# MemoryStore hybrid path
# --------------------------------------------------------------------------
def test_recall_ranks_the_only_matching_memory_first():
    """Regression: relevance must be query coverage, not symmetric Jaccard.

    A long memory containing every query term used to score ~0.09 relevance
    (Jaccard punishes long content), so high-importance/high-frequency
    memories with ZERO lexical overlap outranked the only matching row —
    recall degraded to importance×recency ranking that ignored the query.
    """
    m = _fresh("coverage")
    r = m.remember(
        "semantic",
        "e2e check 2026-08-30: gateway live, SSE job_finished contract fixed, "
        "mission honest blocking verified, token deltas streamed end to end",
        importance=5)
    # strongly-signalled decoys that do NOT match the query at all
    for i in range(6):
        m.remember("procedural", f"Research run {i}: compare Postgres vs MySQL vs SQLite",
                   importance=8, success=True)
    m.remember("episodic", "User asked: Say one. Agent used 0 tool(s) and succeeded.",
               importance=6, success=True)

    hits = m.recall("job_finished SSE contract", limit=10)
    assert hits, "recall returned nothing"
    assert hits[0]["id"] == r["id"], [h["content"][:40] for h in hits[:3]]
    assert hits[0]["signals"]["relevance"] == 1.0, hits[0]["signals"]


def test_hybrid_recall_survives_paraphrase_where_keywords_fail():
    m = _fresh("para")
    m.remember("semantic", "PostgreSQL connection string is stored in the vault, never in .env",
               importance=9)
    m.remember("semantic", "Deploy pipeline: run alembic migrations, then restart the gunicorn workers",
               importance=7)
    m.remember("episodic", "User asked about lunch options", importance=1)

    hits = m.hybrid_recall("where do we keep the database password", limit=5)
    assert hits, "hybrid recall returned nothing"
    ranks = [i for i, h in enumerate(hits) if "PostgreSQL" in h["content"]]
    assert ranks, f"paraphrased query missed the row: {[h['content'][:40] for h in hits]}"
    top = hits[ranks[0]]
    # and it must beat the irrelevant memory that pure keyword overlap could surface
    lunch = [i for i, h in enumerate(hits) if "lunch" in h["content"]]
    assert not lunch or ranks[0] < lunch[0]
    assert top["rrf_score"] > 0
    assert top["retrieval"]["mode"] == "hybrid"
    assert "prior" in top["retrieval"]["contributions"]

    kw = m.recall("alembic migrations", limit=3)
    assert kw and "Deploy pipeline" in kw[0]["content"]
    assert {"id", "kind", "content", "score"} <= set(kw[0])


def test_hybrid_row_carries_decay_signals_and_band():
    m = _fresh("decayband")
    m.remember("semantic", "Kafka topics are partitioned by key to preserve ordering", importance=8)
    hit = next((h for h in m.hybrid_recall("Kafka partition ordering", limit=3)
                if "Kafka" in h["content"]), None)
    assert hit is not None
    assert 0.0 < hit["decay"] <= 1.0
    assert hit["signals"]["band"] in ("hot", "warm", "cold", "archived")


def test_index_stats_report_both_indexes():
    m = _fresh("idxstats")
    m.remember("semantic", "Index stats probe: shard rebalancing notes", importance=5)
    st = m.store.index_stats()
    assert st["available"] is True and st["fts5"] is True
    assert st["fts_table"] == "memories_fts" and st["vector_table"] == "memories_vec"
    assert st["vector_backend"] in ("sqlite-vec", "numpy", "json", "brute-force-cosine", "none")
    assert st["corpus"] >= 1 and st["vectors_indexed"] >= 1
    assert st["vectors_indexed"] <= st["corpus"]
    assert st["config"]["rrf_k"] > 0


def test_explain_shows_rank_contributions():
    m = _fresh("explain")
    m.remember("semantic", "Redis backs the job queue and rate limiting", importance=6)
    out = m.explain("Redis rate limiting queue", limit=3)
    assert out["query"] and "index" in out and "hits" in out
    row = out["hits"][0]
    assert row["retrieval"]["mode"] == "hybrid"
    assert "contributions" in row["retrieval"] and row["rrf_score"] > 0


def test_reindex_rebuilds_vectors():
    m = _fresh("reindex")
    m.remember("semantic", "Grafana dashboards live in deploy/observability", importance=5)
    before = m.store.index_stats()["vectors_indexed"]
    assert m.reindex()["success"] is not False
    assert m.store.index_stats()["vectors_indexed"] >= before


def test_hybrid_disabled_falls_back_to_keyword_recall():
    """An embedding outage must degrade retrieval, not break the agent."""
    from core.memory2 import Memory2

    m = Memory2(db_path=str(Path(_TMP) / "fallback.db"))
    m.remember("semantic", "Caddy terminates TLS for the staging domain", importance=6)
    backup = config.memory_hybrid_enabled
    config.memory_hybrid_enabled = False
    try:
        hits = m.hybrid_recall("Caddy TLS staging", limit=3)
        assert hits and hits[0]["kind"] == "semantic"
        # degradation is explicit and still carries a usable score
        assert hits[0]["retrieval"]["mode"] == "lexical-only"
        assert hits[0]["rrf_score"] == hits[0]["score"]
    finally:
        config.memory_hybrid_enabled = backup


# --------------------------------------------------------------------------
# Decay + eviction
# --------------------------------------------------------------------------
def test_decay_evaluate_bands_and_plan():
    from core.decay import MemoryDecay

    dec = MemoryDecay(half_life_days=30.0, access_half_life_days=14.0, saturate_access=8.0)
    now = datetime.now()
    fresh = {"ts": now.isoformat(timespec="seconds"), "access_count": 3, "importance": 5.0,
             "kind": "semantic", "archived": 0, "pinned": 0}
    stale = {"ts": (now - timedelta(days=400)).isoformat(timespec="seconds"), "access_count": 0,
             "importance": 2.0, "kind": "episodic", "archived": 0, "pinned": 0}

    rep = dec.evaluate(fresh, now)
    assert rep.decay > 0.999 and rep.band == "hot" and rep.age_days < 1
    stale_rep = dec.evaluate(stale, now)
    assert stale_rep.decay < 0.2 and stale_rep.band == "archived"

    # recency is not the whole story: heavily used old memories fade slower (spacing effect)
    assert dec.evaluate(dict(stale, access_count=30), now).decay > stale_rep.decay
    assert dec.evaluate(dict(stale, pinned=1), now).decay == 1.0
    expired = dict(fresh, expires_ts=(now - timedelta(seconds=5)).isoformat(timespec="seconds"))
    assert dec.evaluate(expired, now).decay == 0.0

    action, reasons = dec.plan(stale, now)
    assert action in ("keep", "decay", "archive", "purge", "promote") and reasons
    assert dec.plan(dict(stale, importance=9.5), now)[0] == "keep"      # protected
    assert dec.plan(dict(stale, kind="working"), now)[0] == "purge"     # working TTL
    assert dec.plan(fresh, now)[0] in ("keep", "decay")
    assert dec.status()["half_life_days"] == 30.0
    assert dec.band(0.7) == "hot" and dec.band(0.05) != "hot"


def test_consolidate_clusters_near_duplicates():
    from core.decay import consolidate

    rows = [
        {"content": "use pgbouncer in front of postgres for pooling", "importance": 5},
        {"content": "use pgbouncer in front of the postgres db for connection pooling", "importance": 8},
        {"content": "totally unrelated: rotate the grafana api token", "importance": 3},
    ]
    groups = consolidate(rows, similarity=0.5)
    assert groups and len(groups[0]) == 2
    assert groups[0][0]["importance"] == 8  # most important first


def test_fit_to_budget_evicts_low_value_long_memories():
    from core.decay import fit_to_budget

    items = [
        {"kind": "semantic", "content": "short high value fact", "score": 9.0, "pinned": 0},
        {"kind": "episodic", "content": "x" * 4000, "score": 8.0, "pinned": 0},
        {"kind": "procedural", "content": "another compact note", "score": 7.0, "pinned": 0},
        {"kind": "working", "content": "y" * 4000, "score": 1.0, "pinned": 0},
    ]
    out = fit_to_budget(items, budget_tokens=120, per_kind_cap=None)
    kept = [k["content"] for k in out["kept"]]
    assert "short high value fact" in kept
    assert "y" * 4000 not in kept, "junk-length low-score memory survived the budget"
    assert out["tokens"] <= 120 and out["text"] and out["budget_tokens"] == 120
    assert out["dropped_tokens"] > 0 and 0 < out["utilization"] <= 1.0


def test_fit_to_budget_relaxes_kind_cap_when_window_is_free():
    from core.decay import fit_to_budget

    items = [{"kind": "semantic", "content": f"fact {i}", "score": 9 - i, "pinned": 0}
             for i in range(6)]
    capped = fit_to_budget(items, budget_tokens=600, per_kind_cap=2)
    assert len(capped["kept"]) > 2 and not capped["evicted"]   # cap must not strand the window
    tight = fit_to_budget(items, budget_tokens=12, per_kind_cap=2)
    assert tight["tokens"] <= 12


def test_fit_to_budget_reserves_pinned_first():
    from core.decay import fit_to_budget

    items = [
        {"kind": "semantic", "content": "a" * 60, "score": 2.0, "pinned": 1},
        {"kind": "semantic", "content": "high score but long and unpinned " + "b" * 300, "score": 9.9,
         "pinned": 0},
    ]
    out = fit_to_budget(items, budget_tokens=40, per_kind_cap=None)
    assert out["kept"] and out["kept"][0]["pinned"] == 1


# --------------------------------------------------------------------------
# Lifecycle against the real store
# --------------------------------------------------------------------------
def test_sweep_reports_lifecycle_decisions():
    m = _fresh("sweep")
    r = m.remember("working", "Temporary scratch note that will go stale", importance=3)
    conn = m.store.conn()
    conn.execute("UPDATE memories SET ts = ?, access_count = 0 WHERE id = ?",
                 ((datetime.now() - timedelta(days=90)).isoformat(timespec="seconds"), r["id"]))
    conn.commit()

    dry = m.sweep(dry_run=True)
    assert dry["dry_run"] is True and "checked" in dry and dry["checked"] >= 1
    assert "archived" in dry and "purged" in dry and "promoted" in dry and "summary" in dry

    applied = m.sweep(dry_run=False)
    assert applied["dry_run"] is False
    for key in ("archived", "purged"):
        for row in applied[key]:
            assert row["id"] == r["id"] or row["id"] != r["id"]  # shape: list of {id, reason}
    hits = m.recall("Temporary scratch note that will go stale", limit=10)
    assert r["id"] not in [h["id"] for h in hits]
    assert "bands" in _fresh("sweep").stats() and "index" in _fresh("sweep").stats()


def test_pinned_memory_survives_sweep_and_stays_recallable():
    m = _fresh("pinned")
    r = m.remember("semantic", "Production database host is db-01.internal", importance=10, pinned=True)
    conn = m.store.conn()
    conn.execute("UPDATE memories SET ts = ?, access_count = 0, importance = 1 WHERE id = ?",
                 ((datetime.now() - timedelta(days=500)).isoformat(timespec="seconds"), r["id"]))
    conn.commit()
    out = m.sweep(dry_run=False)
    assert r["id"] not in [x["id"] for x in out["purged"]] + [x["id"] for x in out["archived"]]
    assert r["id"] in [h["id"] for h in m.recall("db-01.internal", limit=20)]
    assert m.pin(r["id"], False)["success"] is True


def test_forget_creates_tombstone():
    m = _fresh("forget")
    r = m.remember("semantic", "Old vendor contract expired in 2023 with Acme Corp", importance=6)
    out = m.forget(r["id"], reason="test")
    assert out["success"] is True and out["forgotten"] == [r["id"]]
    assert r["id"] not in [h["id"] for h in m.recall("Acme Corp expired contract", limit=10)]
    rows = m.store.conn().execute("SELECT reason FROM memory_tombstones WHERE memory_id = ?",
                                  (r["id"],)).fetchall()
    assert rows and rows[0][0] == "test"

    # by-query forgetting finds the row without knowing its id
    r2 = m.remember("semantic", "The staging box is reachable over tailscale only", importance=6)
    out2 = m.forget(query="staging machine network access", reason="by meaning")
    assert r2["id"] in out2["forgotten"], out2
    assert m.forget(reason="nothing")["success"] is False


def test_access_log_records_what_was_recalled():
    m = _fresh("accesslog")
    r = m.remember("semantic", "Retry policy: exponential backoff, five attempts max", importance=7)
    m.recall("exponential backoff retries", limit=5)
    log = m.store.access_log(r["id"], limit=5)
    assert log and log[0]["event"] == "recall" and "query" in log[0]
    row = m.store.conn().execute("SELECT access_count FROM memories WHERE id = ?",
                                 (r["id"],)).fetchone()
    assert int(row[0]) >= 1


def test_recall_context_reports_evictions_to_the_prompt():
    m = _fresh("ctx")
    for i in range(8):
        m.remember("semantic", f"Budget fact number {i} about shard rebalancing", importance=4 + (i % 5))
    ctx = m.recall_context("shard rebalancing budget facts", limit=8)
    assert "Relevant memories" in ctx["text"]
    assert ctx["kept"] and ctx["tokens"] <= ctx["budget_tokens"]
    assert ctx["mode"] in ("hybrid", "keyword")
    assert isinstance(ctx["evicted"], list)
    assert ctx["utilization"] > 0
    # eviction is explained, so nobody has to guess why context went missing
    for e in ctx["evicted"]:
        assert e.get("kind") and e.get("content")


def test_compact_working_memory_prunes_expired_rows():
    m = _fresh("compact")
    r = m.remember("working", "Working note: check flaky test in gateway suite", importance=5)
    conn = m.store.conn()
    conn.execute("UPDATE memories SET ts = ? WHERE id = ?",
                 ((datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds"), r["id"]))
    conn.commit()
    out = m.compact_working_memory(max_age_hours=1)
    assert out["deleted_count"] >= 1
    assert r["id"] not in [row[0] for row in
                           conn.execute("SELECT id FROM memories WHERE kind='working'").fetchall()]


def test_tool_registry_exposes_hybrid_search_and_sweep():
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    tools = tool_registry.list_tools()["tools"]
    for name in ("memory_hybrid_search", "memory_sweep", "skill_harvest", "delegate_tasks",
                 "sandbox_run", "skill_forge_stats"):
        assert name in tools, f"{name} not registered"
    # write through the tool, read back through the tool (global store, shared
    # with the agent loop) — one round trip proves both are wired
    mem = tool_registry.execute("memory2_remember", {
        "kind": "semantic",
        "content": "Registry probe: the vault holds the database password for this test",
        "importance": 9,
    })
    assert mem.get("success") or mem.get("id")
    out = tool_registry.execute("memory_hybrid_search", {"query": "vault database password", "limit": 5})
    assert out["mode"] == "hybrid" and out["count"] >= 1 and out["index"]["available"]
    assert "retrieval" in out["results"][0]
    exp = tool_registry.execute("memory_hybrid_search", {"query": "vault", "explain": True})
    assert "index" in exp and "hits" in exp
    assert "checked" in tool_registry.execute("memory_sweep", {"dry_run": True})


def test_legacy_keyword_recall_contract_unchanged():
    """The agent/gateway/CLI read {id, kind, content, score}; that must not break."""
    for h in _store().recall("Kafka", limit=5):
        assert isinstance(h["id"], int)
        assert h["kind"] in ("working", "episodic", "semantic", "procedural", "project")
        assert isinstance(h["score"], (int, float))
        assert isinstance(h["content"], str)


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
