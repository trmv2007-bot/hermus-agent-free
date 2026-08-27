"""Hybrid retrieval — BM25 (SQLite FTS5) + dense vectors fused with RRF.

Upgrades keyword-only recall to a real hybrid search, so a query phrased
differently from a stored memory still finds it.

Pipeline for every query:

    1. BM25 list   — FTS5 ``bm25()`` over an external-content index (joined back
                     to the base table so project/kind filters still apply).
    2. Dense list  — cosine kNN over float32 embeddings, accelerated by
                     ``sqlite-vec`` (``vec0`` virtual table) when the extension
                     is loadable, else a stdlib brute-force scan of packed
                     vectors (fine for tens of thousands of rows).
    3. Reciprocal Rank Fusion over the **union** of both candidate sets:

                        rrf(d) = Σ_lists  w_list / (k + rank_list(d))

       plus an additive **prior** (importance × decay) so durable, frequently
       accessed memories keep a small edge without dominating.
    4. Temporal re-scoring / eviction happens upstream (see ``core.decay``).

Two failure modes this removes compared with a naive "re-rank the lexical
top-K" approach:

* Candidates come from *both* lists, so a semantically perfect but lexically
  disjoint memory can be promoted instead of never being seen.
* The FTS5 query is sanitised, so a query containing ``AND``, quotes or parens
  cannot raise a syntax error and silently return nothing.

Pure stdlib; ``sqlite-vec`` is an optional accelerator discovered at runtime.
"""
from __future__ import annotations

import math
import re
import sqlite3
import struct
import threading
from dataclasses import dataclass, field
from typing import Any, Optional
from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "HybridConfig",
    "HybridHit",
    "HybridRetriever",
    "LockedConnection",
    "cosine",
    "jaccard",
    "lexical_prior",
    "pack_vector",
    "sanitize_fts_query",
    "tokenize",
    "unpack_vector",
]

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "when", "like",
    "it", "its", "with", "that", "this", "these", "those", "are", "was", "were",
    "be", "been", "being", "as", "by", "from", "has", "have", "had", "is", "do",
    "does", "did", "how", "what", "why", "which", "who", "can", "could", "i",
    "you", "me", "my", "we", "our", "not", "no", "but", "than", "then", "please",
}
_FTS_JUNK_RE = re.compile(r"[^\w\s]+")


# --------------------------------------------------------------------- helpers
def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS]


def jaccard(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def sanitize_fts_query(query: str, max_terms: int = 12) -> str:
    """Arbitrary user text → safe prefix-matched FTS5 MATCH expression.

    ``how do I rotate API keys??`` → ``"rotate" OR "rotate"* OR "api" OR "api"*``
    (stopwords dropped, punctuation stripped, each term phrase-quoted so no
    operator can be interpreted). Empty result means "nothing to match on".
    """
    terms: list[str] = []
    for tok in tokenize(_FTS_JUNK_RE.sub(" ", query or "")):
        if len(tok) < 2 or tok in terms:
            continue
        terms.append(tok)
        if len(terms) >= max_terms:
            break
    if not terms:
        return ""
    return " OR ".join(f'"{t}" OR "{t}"*' for t in terms)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def pack_vector(vec: Iterable[float]) -> bytes:
    v = list(vec)
    return struct.pack(f"{len(v)}f", *v)


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob)) if n else []


class LockedConnection:
    """Serialize access to one sqlite3 connection shared across threads.

    ``sqlite3.connect(check_same_thread=False)`` lets threads share a handle but
    does *not* serialize it; a gateway worker pool + background sweeper would
    otherwise interleave commits.
    """

    def __init__(self, conn: sqlite3.Connection, lock: Optional[threading.RLock] = None):
        self._conn = conn
        self._lock = lock or threading.RLock()

    @property
    def raw(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> Any:
        with self._lock:
            return self._conn.executemany(sql, list(seq))

    def executescript(self, sql: str) -> Any:
        with self._lock:
            return self._conn.executescript(sql)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __getattr__(self, name: str) -> Any:  # enable_load_extension etc.
        return getattr(self._conn, name)


def _vec0_available(conn: Any) -> bool:
    """Load the optional sqlite-vec extension into ``conn`` once."""
    if getattr(conn, "_hermus_vec0", None) is True:
        return True
    if getattr(conn, "_hermus_vec0", None) is False:
        return False
    try:
        import sqlite_vec  # type: ignore

        raw = conn.raw if isinstance(conn, LockedConnection) else conn
        if not hasattr(raw, "enable_load_extension"):
            conn._hermus_vec0 = False  # type: ignore[attr-defined]
            return False
        raw.enable_load_extension(True)
        sqlite_vec.load(raw)
        raw.enable_load_extension(False)
        conn._hermus_vec0 = True  # type: ignore[attr-defined]
        return True
    except Exception:
        try:
            conn._hermus_vec0 = False  # type: ignore[attr-defined]
        except Exception:
            pass
        return False


# ---------------------------------------------------------------- dataclasses
@dataclass
class HybridConfig:
    """Tunables for candidate generation + fusion weights."""

    rrf_k: int = 60
    weight_bm25: float = 1.0
    weight_vector: float = 1.0
    weight_prior: float = 0.35
    candidate_multiplier: int = 6
    min_candidates: int = 60
    max_candidates: int = 600
    #: when a lexical hit has no stored vector, embed it on the fly (bounded)
    embed_on_demand: int = 25
    #: keep candidates whose fused score is within this fraction of the best one
    min_score_ratio: float = 0.0

    def candidates_for(self, limit: int) -> int:
        return max(self.min_candidates, min(self.max_candidates, max(4, int(limit)) * self.candidate_multiplier))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rrf_k": self.rrf_k,
            "weight_bm25": self.weight_bm25,
            "weight_vector": self.weight_vector,
            "weight_prior": self.weight_prior,
            "candidate_multiplier": self.candidate_multiplier,
            "embed_on_demand": self.embed_on_demand,
        }


@dataclass
class HybridHit:
    """A fused retrieval result with its explanation attached."""

    id: Any
    content: str = ""
    score: float = 0.0
    bm25_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    vector_similarity: Optional[float] = None
    prior: float = 0.0
    contributions: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "hybrid_score": round(self.score, 6),
            "rrf_score": round(self.score, 6),
            "retrieval": {
                "bm25_rank": self.bm25_rank,
                "vector_rank": self.vector_rank,
                "vector_similarity": self.vector_similarity,
                "prior": round(self.prior, 4),
                "contributions": {k: round(v, 6) for k, v in self.contributions.items()},
            },
        }


# ------------------------------------------------------------------- retriever
class HybridRetriever:
    """Hybrid BM25 + dense retrieval over a single SQLite table.

    Parameters
    ----------
    conn:
        A ``sqlite3.Connection`` (or :class:`LockedConnection`).
    table / id_col / content_col:
        The document table. ``id_col`` is the stable integer document id.
    fts_table / vector_table:
        FTS5 external-content index and packed-vector store. Created if missing.
    embedder:
        ``callable(text) -> list[float]``. Failures are swallowed (lexical search
        still works) so memory recall can never be broken by an embedding outage.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        content_col: str = "content",
        id_col: str = "id",
        fts_table: Optional[str] = None,
        vector_table: str = "hybrid_vectors",
        embedder: Optional[Callable[[str], list[float]]] = None,
        config: Optional[HybridConfig] = None,
        vectorizer: str = "",
        manage_schema: bool = True,
    ):
        self.conn = conn
        self.table = table
        self.content_col = content_col
        self.id_col = id_col
        self.fts_table = fts_table or f"{table}_fts"
        self.vector_table = vector_table
        self.embedder = embedder
        self.vectorizer = vectorizer or ("embedder" if embedder else "none")
        self.config = config or HybridConfig()
        self._fts_ok: Optional[bool] = None
        self._vec_dim: Optional[int] = None
        self._knn_table: Optional[str] = None      # "" = probed, unavailable
        self._knn_probed = False
        if manage_schema:
            self.ensure_schema()

    # ------------------------------------------------------------------- schema
    def _ex(self, sql: str, params: Sequence[Any] = ()) -> Any:
        return self.conn.execute(sql, tuple(params))

    def ensure_schema(self) -> None:
        """Create the FTS5 index + trigger set + vector table (idempotent)."""
        if self._fts_supported():
            try:
                self._ex(
                    f"""CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table} USING fts5(
                            {self.content_col},
                            content='{self.table}',
                            content_rowid='{self.id_col}',
                            tokenize='porter unicode61 remove_diacritics 2')"""
                )
                self._ex(
                    f"""CREATE TRIGGER IF NOT EXISTS {self.fts_table}_ai AFTER INSERT ON {self.table}
                        BEGIN
                          INSERT INTO {self.fts_table}(rowid, {self.content_col})
                          VALUES (new.{self.id_col}, new.{self.content_col});
                        END"""
                )
                self._ex(
                    f"""CREATE TRIGGER IF NOT EXISTS {self.fts_table}_ad AFTER DELETE ON {self.table}
                        BEGIN
                          INSERT INTO {self.fts_table}({self.fts_table}, rowid, {self.content_col})
                          VALUES ('delete', old.{self.id_col}, old.{self.content_col});
                        END"""
                )
                self._ex(
                    f"""CREATE TRIGGER IF NOT EXISTS {self.fts_table}_au AFTER UPDATE ON {self.table}
                        BEGIN
                          INSERT INTO {self.fts_table}({self.fts_table}, rowid, {self.content_col})
                          VALUES ('delete', old.{self.id_col}, old.{self.content_col});
                          INSERT INTO {self.fts_table}(rowid, {self.content_col})
                          VALUES (new.{self.id_col}, new.{self.content_col});
                        END"""
                )
                self.conn.commit()
                self.backfill_fts()
            except Exception:
                self._fts_ok = False
        try:
            self._ex(
                f"""CREATE TABLE IF NOT EXISTS {self.vector_table} (
                        doc_id INTEGER PRIMARY KEY,
                        dim INTEGER NOT NULL,
                        vectorizer TEXT,
                        vec BLOB NOT NULL,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
            )
            self.conn.commit()
        except Exception:
            pass

    def backfill_fts(self) -> None:
        """Rebuild the FTS index from the base table (legacy rows / repair)."""
        if not self._fts_supported():
            return False
        try:
            self._ex(f"INSERT INTO {self.fts_table}({self.fts_table}) VALUES ('rebuild')")
            self.conn.commit()
            return True
        except Exception:
            return False

    def _fts_supported(self) -> bool:
        if self._fts_ok is None:
            try:
                self._ex("CREATE VIRTUAL TABLE IF NOT EXISTS _hermus_fts_probe USING fts5(x)")
                self._ex("DROP TABLE _hermus_fts_probe")
                self._fts_ok = True
            except Exception:
                self._fts_ok = False
        return bool(self._fts_ok)

    # -------------------------------------------------------------- vector side
    def dim(self) -> Optional[int]:
        if self._vec_dim is not None:
            return self._vec_dim or None
        try:
            row = self._ex(f"SELECT dim FROM {self.vector_table} LIMIT 1").fetchone()
            self._vec_dim = int(row[0]) if row else 0
        except Exception:
            self._vec_dim = 0
        return self._vec_dim or None

    def invalidate_index(self) -> None:
        self._vec_dim = None
        self._knn_table = None
        self._knn_probed = False

    def _ensure_knn(self) -> Optional[str]:
        """Materialise the sqlite-vec index over stored vectors (once)."""
        if self._knn_probed:
            return self._knn_table or None
        self._knn_probed = True
        dim = self.dim()
        if not dim or not _vec0_available(self.conn):
            self._knn_table = ""
            return None
        name = f"{self.vector_table}_vec0"
        try:
            self._ex(f"DROP TABLE IF EXISTS {name}")
            self._ex(
                f"CREATE VIRTUAL TABLE {name} USING vec0("
                f"doc_id INTEGER PRIMARY KEY, embedding float[{int(dim)}])"
            )
            self._ex(
                f"INSERT INTO {name}(doc_id, embedding) SELECT doc_id, vec FROM {self.vector_table} WHERE dim = ?",
                (dim,),
            )
            self.conn.commit()
            self._knn_table = name
            return name
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            self._knn_table = ""
            return None

    def _embed(self, text: str) -> list[float]:
        if not self.embedder or not (text or "").strip():
            return []
        try:
            vec = self.embedder(text)
        except Exception:
            return []
        return [float(x) for x in (vec or [])]

    def store_vector(self, doc_id: Any, vec: Sequence[float]) -> bool:
        if not vec:
            return False
        dim = self.dim()
        if dim and len(vec) != dim:
            # Embedding model switched: the old vectors are meaningless. Rebuild.
            try:
                self._ex(f"DELETE FROM {self.vector_table}")
                if self._knn_table:
                    self._ex(f"DROP TABLE IF EXISTS {self._knn_table}")
                self.conn.commit()
            except Exception:
                pass
            self.invalidate_index()
        try:
            self._ex(
                f"""INSERT OR REPLACE INTO {self.vector_table}(doc_id, dim, vectorizer, vec, updated_at)
                    VALUES (?,?,?,?,CURRENT_TIMESTAMP)""",
                (int(doc_id), len(vec), self.vectorizer, pack_vector(vec)),
            )
            self.conn.commit()
            self._vec_dim = len(vec)
            if self._knn_probed and self._knn_table:
                try:
                    self._ex(
                        f"INSERT OR REPLACE INTO {self._knn_table}(doc_id, embedding) VALUES (?,?)",
                        (int(doc_id), pack_vector(vec)),
                    )
                    self.conn.commit()
                except Exception:
                    self._knn_table, self._knn_probed = "", False
            return True
        except Exception:
            return False

    def upsert_vector(self, doc_id: Any, text: str) -> bool:
        vec = self._embed(text)
        if not vec:
            return False
        return self.store_vector(doc_id, vec)

    def has_vector(self, doc_id: Any) -> bool:
        try:
            row = self._ex(f"SELECT 1 FROM {self.vector_table} WHERE doc_id = ?", (int(doc_id),)).fetchone()
            return row is not None
        except Exception:
            return False

    def delete_vector(self, doc_id: Any) -> None:
        try:
            self._ex(f"DELETE FROM {self.vector_table} WHERE doc_id = ?", (int(doc_id),))
            if self._knn_table:
                self._ex(f"DELETE FROM {self._knn_table} WHERE doc_id = ?", (int(doc_id),))
            self.conn.commit()
        except Exception:
            pass

    def reindex(self, max_rows: int = 100_000) -> dict[str, Any]:
        """Wipe and re-embed every document (call after switching embedders)."""
        if not self.embedder:
            return {"success": False, "error": "no embedder configured"}
        try:
            self._ex(f"DELETE FROM {self.vector_table}")
            if self._knn_table:
                self._ex(f"DROP TABLE IF EXISTS {self._knn_table}")
            self.conn.commit()
        except Exception:
            pass
        self.invalidate_index()
        rows = self._ex(
            f"SELECT {self.id_col}, {self.content_col} FROM {self.table} "
            f"WHERE length({self.content_col}) > 0 LIMIT ?",
            (int(max_rows),),
        ).fetchall()
        done = 0
        for rid, content in rows:
            if self.upsert_vector(rid, content or ""):
                done += 1
        return {"success": True, "reindexed": done, "rows": len(rows), "dim": self.dim()}

    # ------------------------------------------------------------------ ranking
    def bm25(self, query: str, k: int, where: str = "", params: Sequence[Any] = ()) -> list[tuple[Any, float]]:
        """FTS5 BM25 ranking → ``[(id, score01)]`` (higher is better)."""
        if k <= 0 or not self._fts_supported():
            return []
        expr = sanitize_fts_query(query)
        if not expr:
            return []
        sql = (
            f"SELECT {self.fts_table}.rowid AS id, bm25({self.fts_table}) AS s "
            f"FROM {self.fts_table} JOIN {self.table} ON {self.table}.{self.id_col} = {self.fts_table}.rowid "
            f"WHERE {self.fts_table} MATCH ?"
        )
        if where:
            sql += f" AND ({where})"
        sql += " ORDER BY s ASC LIMIT ?"
        try:
            rows = self._ex(sql, (expr,) + tuple(params) + (int(k),)).fetchall()
        except Exception:
            # Malformed user query for FTS5 — degrade to LIKE so recall survives.
            try:
                like = f"%{(query or '').strip()[:80]}%"
                sql2 = f"SELECT {self.id_col} AS id, 0.0 AS s FROM {self.table} WHERE {self.content_col} LIKE ?"
                if where:
                    sql2 += f" AND ({where})"
                sql2 += f" ORDER BY {self.id_col} DESC LIMIT ?"
                rows = self._ex(sql2, (like,) + tuple(params) + (int(k),)).fetchall()
            except Exception:
                return []
        out: list[tuple[Any, float]] = []
        for rid, raw in rows:
            # SQLite bm25() is negative-is-better; map to a 0..1 relevance score.
            raw = float(raw or 0.0)
            out.append((rid, round(1.0 / (1.0 + max(0.0, -raw)), 6)))
        return out

    def vector_search(
        self,
        query: str,
        k: int,
        where: str = "",
        params: Sequence[Any] = (),
        restrict_ids: Optional[Sequence[Any]] = None,
    ) -> list[tuple[Any, float]]:
        """Dense retrieval → ``[(id, cosine_similarity)]`` sorted desc."""
        if k <= 0:
            return []
        qvec = self._embed(query)
        dim = self.dim()
        if not qvec or not dim:
            return []
        if len(qvec) != dim:
            return []
        results: list[tuple[Any, float]] = []
        knn = self._ensure_knn()
        if knn and restrict_ids is None:
            try:
                rows = self._ex(
                    f"SELECT doc_id, distance FROM {knn} WHERE embedding MATCH ? AND k = ? "
                    f"ORDER BY distance ASC",
                    (pack_vector(qvec), int(k * 2)),
                ).fetchall()
                # sqlite-vec returns cosine distance for float[] (1 - cos)
                results = [(rid, round(max(0.0, 1.0 - float(d)), 6)) for rid, d in rows]
            except Exception:
                self._knn_table, self._knn_probed = "", False
                results = []
        if not results:
            wanted = [int(i) for i in restrict_ids] if restrict_ids is not None else None
            try:
                if wanted:
                    ph = ",".join("?" for _ in wanted)
                    rows = self._ex(
                        f"SELECT doc_id, vec FROM {self.vector_table} WHERE dim = ? AND doc_id IN ({ph})",
                        (dim, *wanted),
                    ).fetchall()
                else:
                    rows = self._ex(f"SELECT doc_id, vec FROM {self.vector_table} WHERE dim = ?", (dim,)).fetchall()
            except Exception:
                return []
            results = []
            for doc_id, blob in rows:
                try:
                    results.append((doc_id, round(cosine(qvec, unpack_vector(blob)), 6)))
                except Exception:
                    continue
            results.sort(key=lambda x: x[1], reverse=True)
        if where or restrict_ids is not None:
            results = self._apply_filter(results, where, params)
        return results[: int(k)]

    def _apply_filter(
        self, pairs: list[tuple[Any, float]], where: str, params: Sequence[Any]
    ) -> list[tuple[Any, float]]:
        """Drop vector hits that fail the base-table filter (project/kind/…)."""
        if not pairs:
            return []
        ids = [p[0] for p in pairs]
        ph = ",".join("?" for _ in ids)
        sql = f"SELECT {self.id_col} FROM {self.table} WHERE {self.id_col} IN ({ph})"
        if where:
            sql += f" AND ({where})"
        try:
            allowed = {r[0] for r in self._ex(sql, tuple(ids) + tuple(params)).fetchall()}
        except Exception:
            return pairs
        return [p for p in pairs if p[0] in allowed]

    def fetch_docs(
        self, ids: Sequence[Any], where: str = "", params: Sequence[Any] = (), extra_select: str = ""
    ) -> dict[Any, dict[str, Any]]:
        ids = [i for i in ids if i is not None]
        if not ids:
            return {}
        ph = ",".join("?" for _ in ids)
        sql = f"SELECT {self.id_col} AS id, {self.content_col} AS content"
        if extra_select:
            sql += f", {extra_select}"
        sql += f" FROM {self.table} WHERE {self.id_col} IN ({ph})"
        if where:
            sql += f" AND ({where})"
        try:
            rows = self._ex(sql, tuple(ids) + tuple(params)).fetchall()
        except Exception:
            return {}
        out: dict[Any, dict[str, Any]] = {}
        for row in rows:
            d: dict[str, Any] = {"id": row[0], "content": row[1] or ""}
            if extra_select and len(row) > 2:
                d["prior"] = float(row[2] or 0.0)
            out[row[0]] = d
        return out

    def fuse(
        self,
        lists: dict[str, list[tuple[Any, float]]],
        docs: dict[Any, dict[str, Any]],
        *,
        limit: int = 10,
        weights: Optional[dict[str, float]] = None,
    ) -> list[HybridHit]:
        """Reciprocal Rank Fusion over arbitrary ranked lists.

        ``lists`` maps a list name → ``[(doc_id, score)]`` in best-first order.
        ``docs`` supplies content + an optional ``prior`` (0..1) per id. Ids that
        appear in a list but not in ``docs`` are skipped (filtered out upstream).
        """
        cfg = self.config
        weights = weights or {"bm25": cfg.weight_bm25, "vector": cfg.weight_vector}
        ranks: dict[str, dict[Any, int]] = {}
        sims: dict[Any, float] = {}
        union: list[Any] = []
        seen = set()
        for name, pairs in lists.items():
            ranks[name] = {}
            for idx, (did, score) in enumerate(pairs):
                ranks[name][did] = idx + 1
                if name == "vector":
                    sims[did] = float(score)
                if did not in seen:
                    seen.add(did)
                    union.append(did)
        hits: list[HybridHit] = []
        for did in union:
            doc = docs.get(did)
            if doc is None:
                continue
            total = 0.0
            contrib: dict[str, float] = {}
            ranks_out: dict[str, Optional[int]] = {}
            for name in lists:
                r = ranks[name].get(did)
                ranks_out[name] = r
                if r:
                    w = float(weights.get(name, 1.0))
                    c = w / (cfg.rrf_k + r)
                    contrib[name] = c
                    total += c
                else:
                    contrib[name] = 0.0
            prior = float(doc.get("prior") or 0.0)
            if prior and cfg.weight_prior:
                c = cfg.weight_prior * prior
                contrib["prior"] = c
                total += c
            hits.append(
                HybridHit(
                    id=did,
                    content=str(doc.get("content") or ""),
                    score=total,
                    bm25_rank=ranks_out.get("bm25"),
                    vector_rank=ranks_out.get("vector"),
                    vector_similarity=round(sims.get(did, 0.0), 4) or None,
                    prior=prior,
                    contributions=contrib,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        if cfg.min_score_ratio > 0 and hits:
            floor = hits[0].score * cfg.min_score_ratio
            hits = [h for h in hits if h.score >= floor]
        return hits[: int(limit)]

    # ------------------------------------------------------------------- search
    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        where: str = "",
        params: Sequence[Any] = (),
        prior_select: str = "",
        embed_missing: bool = True,
    ) -> list[HybridHit]:
        """Hybrid search: fused BM25 + dense ranking over ``self.table``.

        ``prior_select`` is a SQL expression evaluated on the base table that
        yields a 0..1 freshness/importance prior (e.g.
        ``"(importance/10.0) * COALESCE(decay,1.0)"``).
        """
        cfg = self.config
        k = cfg.candidates_for(limit)
        bm = self.bm25(query, k, where=where, params=params)
        # Lexical hits without a stored vector: embed them now so the dense list
        # can also judge them (bounded, best-effort, never fatal).
        if embed_missing and self.embedder:
            budget = cfg.embed_on_demand
            for did, _s in bm:
                if budget <= 0:
                    break
                if self.has_vector(did):
                    continue
                doc = self.fetch_docs([did], where=where, params=params)
                if did in doc and self.upsert_vector(did, doc[did]["content"]):
                    budget -= 1
        vec = self.vector_search(query, k, where=where, params=params)

        ids: list[Any] = [d for d, _ in bm] + [d for d, _ in vec]
        docs = self.fetch_docs(ids, where=where, params=params, extra_select=prior_select)
        # Corpus is small enough to scan? Then let the dense list consider every
        # filtered row, so semantic-only matches are not capped by candidate size.
        if len(docs) < self._corpus_size(where, params):
            extra = self.vector_search(query, k * 3, where=where, params=params)
            for did, _s in extra:
                if did not in docs:
                    docs.update(self.fetch_docs([did], where=where, params=params, extra_select=prior_select))
        return self.fuse({"bm25": bm, "vector": vec}, docs, limit=limit)

    def _corpus_size(self, where: str = "", params: Sequence[Any] = ()) -> int:
        sql = f"SELECT COUNT(*) FROM {self.table}"
        if where:
            sql += f" WHERE ({where})"
        try:
            return int(self._ex(sql, tuple(params)).fetchone()[0])
        except Exception:
            return 1 << 30

    def lexical_only(self, query: str, *, limit: int = 10, where: str = "", params: Sequence[Any] = ()):
        """BM25-only ranking (used as a fallback and by tests)."""
        bm = self.bm25(query, max(limit * 3, 20), where=where, params=params)
        docs = self.fetch_docs([d for d, _ in bm], where=where, params=params)
        return self.fuse({"bm25": bm}, docs, limit=limit, weights={"bm25": 1.0})

    def stats(self) -> dict[str, Any]:
        knn = self._ensure_knn()
        out: dict[str, Any] = {
            "available": True,
            "fts5": self._fts_supported(),
            "fts_table": self.fts_table,
            "vector_table": self.vector_table,
            "vector_backend": "sqlite-vec" if knn else "brute-force-cosine",
            "vectorizer": self.vectorizer,
            "embedder": bool(self.embedder),
            "dim": self.dim(),
            "config": self.config.to_dict(),
        }
        try:
            out["vectors_indexed"] = int(self._ex(f"SELECT COUNT(*) FROM {self.vector_table}").fetchone()[0])
        except Exception:
            out["vectors_indexed"] = 0
        try:
            out["corpus"] = int(self._ex(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0])
        except Exception:
            out["corpus"] = 0
        return out


def lexical_prior(importance: float, decay: float, max_importance: float = 10.0) -> float:
    """Blend importance (0..max) with a 0..1 decay factor into a 0..1 prior."""
    imp = max(0.0, min(1.0, float(importance or 0.0) / float(max_importance or 10.0)))
    return round(0.6 * imp + 0.4 * max(0.0, min(1.0, float(decay or 0.0))), 4)
