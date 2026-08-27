"""Memory 2.0 — typed long-term memory with hybrid retrieval, decay, eviction.

Five typed stores (`working`, `episodic`, `semantic`, `procedural`, `project`)
in one SQLite DB. Recall scores each memory across six signals — importance,
recency (exp. decay), access frequency, task relevance, user preference and
success/failure — and returns a ranked list. Near-duplicates merge instead of
duplicating.

Upgrades on top of the original scoring engine:

* **Hybrid retrieval** (`core.hybrid_search`): FTS5 BM25 + dense embeddings
  fused with Reciprocal Rank Fusion. Fuses over the *union* of both candidate
  lists, so a memory phrased completely differently from the query can still
  win. Accelerated by `sqlite-vec` when installed; otherwise a stdlib cosine
  scan. Keyword-only recall is retained as a fallback whenever the vector or
  FTS side is unavailable.
* **Temporal decay + lifecycle** (`core.decay`): access-adaptive exponential
  decay, TTL for `working` memories, archive → purge for stale ones, promotion
  of repeatedly-useful episodic memories into `semantic`.
* **Context eviction**: `recall_prompt_block(..., max_tokens=…)` packs memories
  into a token budget by value density instead of dumping the top-K, so stale
  history cannot crowd out fresh, relevant context.
* **Access tracking**: every recall records which memories were used, which is
  what feeds the frequency signal *and* the decay half-life.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import config

KINDS = ("working", "episodic", "semantic", "procedural", "project")

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "when", "like",
    "it", "its", "with", "that", "this", "these", "those", "are", "was", "were",
    "be", "been", "being", "as", "by", "from", "has", "have", "had", "is", "do",
    "does", "did", "how", "what", "why", "which", "who", "can", "could", "i",
    "you", "me", "my", "we", "our", "not", "no", "but", "than", "then", "please",
}


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall((text or "").lower())) - _STOPWORDS


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MemoryStore:
    """Typed memory backed by a single SQLite database (+ FTS5 + vector index)."""

    def __init__(self, db_path: Optional[str] = None, *, index: bool = True):
        self.db_path = Path(db_path or config.resolve_path(config.memory2_db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._index_conn = None          # LockedConnection used by the retriever
        self._retriever = None
        self._index_enabled = bool(index)
        self._init()

    def _conn_new(self, shared: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=not shared)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=4000;")
            conn.execute("PRAGMA temp_store=MEMORY;")
        except Exception:
            pass
        return conn

    def conn(self) -> sqlite3.Connection:
        """Shared connection (thread-safe because every caller holds ``_lock``)."""
        with self._lock:
            if self._conn is None:
                self._conn = self._conn_new(shared=True)
            return self._conn

    def index_conn(self):
        """Connection handed to the hybrid retriever, serialized on the store lock."""
        from .hybrid_search import LockedConnection

        with self._lock:
            if self._index_conn is None:
                if self._conn is None:
                    self._conn = self._conn_new(shared=True)
                self._index_conn = LockedConnection(self._conn, self._lock)
            return self._index_conn

    def close(self) -> None:
        with self._lock:
            self._retriever = None
            self._index_conn = None
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def _init(self) -> None:
        conn = self._conn_new()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                project TEXT DEFAULT 'default',
                importance REAL DEFAULT 5,
                success REAL,             -- NULL = n/a
                key TEXT,                 -- normalized dedupe key
                metadata TEXT,
                ts TEXT NOT NULL
            )
            """
        )
        # temporal / lifecycle columns (migration-safe)
        for col, decl in (
            ("access_count", "REAL DEFAULT 0"),
            ("last_access_ts", "TEXT"),
            ("decay", "REAL DEFAULT 1.0"),
            ("archived", "INTEGER DEFAULT 0"),
            ("pinned", "INTEGER DEFAULT 0"),
            ("expires_ts", "TEXT"),
            ("session", "TEXT"),
            ("kind_promoted", "TEXT"),
        ):
            try:
                cur.execute(f"ALTER TABLE memories ADD COLUMN {col} {decl};")
            except Exception:
                pass  # column already exists
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mem_ts ON memories(ts);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mem_archived ON memories(archived);")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                event TEXT DEFAULT 'recall',
                query TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_acc_mem ON memory_access(memory_id, ts);")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_tombstones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER,
                kind TEXT,
                content TEXT,
                reason TEXT,
                ts TEXT
            )
            """
        )
        conn.commit()
        conn.close()
        if self._index_enabled:
            self._ensure_retriever()

    # ----------------------------------------------------------------- indexing
    def retriever(self):
        if self._retriever is None:
            self._ensure_retriever()
        return self._retriever

    def _ensure_retriever(self):
        """Lazily build the hybrid (BM25 + vector) index over ``memories``."""
        try:
            from .hybrid_search import HybridConfig, HybridRetriever
        except Exception:
            self._retriever = None
            return None
        embedder = None
        vectorizer = "none"
        if getattr(config, "memory_vectors_enabled", True):
            try:
                from .embeddings import embedding_store

                def embedder(text: str, _store=embedding_store) -> List[float]:
                    return _store.embed(text)

                vectorizer = str(getattr(embedding_store, "model", "embed"))
            except Exception:
                embedder = None
        cfg = HybridConfig()
        try:
            cfg.weight_prior = float(getattr(config, "memory_prior_weight", 0.35))
            cfg.rrf_k = int(getattr(config, "memory_rrf_k", 60))
        except Exception:
            pass
        try:
            self._retriever = HybridRetriever(
                self.index_conn(),
                table="memories",
                content_col="content",
                id_col="id",
                fts_table="memories_fts",
                vector_table="memories_vec",
                embedder=embedder,
                config=cfg,
                vectorizer=vectorizer,
            )
        except Exception:
            self._retriever = None
        return self._retriever

    def _index_add(self, memory_id: int, content: str) -> None:
        """Store the vector for a new memory (FTS is handled by triggers)."""
        r = self.retriever()
        if r is None or not content.strip():
            return
        try:
            r.upsert_vector(int(memory_id), content[:2000])
        except Exception:
            pass

    # ------------------------------------------------------------------ mutations
    def _dedupe_key(self, kind: str, content: str, project: str = "default") -> str:
        """Dedupe key — kind + project + canonical token signature.

        Project is part of the key so the same fact learned under one project
        cannot silently retarget another project's scoped memory.
        """
        toks = sorted(_tokens(content))[:24]
        return f"{kind}:{project}:{' '.join(toks)}"

    @staticmethod
    def _legacy_key(kind: str, content: str) -> str:
        toks = sorted(_tokens(content))[:24]
        return f"{kind}:{' '.join(toks)}"

    def remember(
        self,
        kind: str,
        content: str,
        project: Optional[str] = None,
        importance: float = 5.0,
        success: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None,
        session: Optional[str] = None,
        ttl_hours: Optional[float] = None,
        pinned: bool = False,
    ) -> Dict[str, Any]:
        if kind not in KINDS:
            return {"success": False, "error": f"unknown kind '{kind}' (choose {KINDS})"}
        content = (content or "").strip()
        if not content:
            return {"success": False, "error": "empty content"}
        project = project or getattr(config, "project", "default")
        key = self._dedupe_key(kind, content, project)
        expires = (
            datetime.now() + timedelta(hours=float(ttl_hours))
        ).isoformat(timespec="seconds") if ttl_hours else None
        with self._lock:
            conn = self.conn()
            cur = conn.cursor()
            # frequency signal: bump an existing near-identical memory instead of duping
            cur.execute(
                "SELECT id, metadata, archived FROM memories WHERE key = ? OR key = ?",
                (key, self._legacy_key(kind, content)),
            )
            row = cur.fetchone()
            if row:
                meta = json.loads(row["metadata"] or "{}") if row["metadata"] else {}
                meta["frequency"] = meta.get("frequency", 1) + 1
                if session:
                    meta.setdefault("sessions", [])
                    meta["sessions"] = (meta["sessions"] + [session])[-8:]
                if row["archived"]:
                    meta["restored_from_archive"] = True
                cur.execute(
                    """
                    UPDATE memories
                       SET ts = ?,
                           importance = MAX(importance, ?),
                           metadata = ?,
                           success = COALESCE(?, success),
                           archived = 0,
                           pinned = MAX(pinned, ?),
                           access_count = access_count + 1,
                           last_access_ts = ?,
                           expires_ts = COALESCE(?, expires_ts)
                     WHERE id = ?
                    """,
                    (
                        _now(), importance, json.dumps(meta), success,
                        1 if pinned else 0, _now(), expires, row["id"],
                    ),
                )
                conn.commit()
                self._index_add(int(row["id"]), content)
                return {"success": True, "id": row["id"], "merged": True}
            cur.execute(
                """
                INSERT INTO memories (kind, content, project, importance, success, key,
                                      metadata, ts, session, expires_ts, pinned, decay)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    kind, content, project, importance, success, key,
                    json.dumps({**(metadata or {}), "frequency": 1, "sessions": [session] if session else []}),
                    _now(), session, expires, 1 if pinned else 0, 1.0,
                ),
            )
            new_id = int(cur.lastrowid)
            conn.commit()
        self._index_add(new_id, content)
        return {"success": True, "id": new_id, "merged": False}

    def forget(self, memory_id: int, *, reason: str = "manual", tombstone: bool = True) -> bool:
        with self._lock:
            conn = self.conn()
            cur = conn.cursor()
            if tombstone:
                try:
                    cur.execute(
                        "SELECT kind, content FROM memories WHERE id = ?", (int(memory_id),)
                    )
                    row = cur.fetchone()
                    if row:
                        cur.execute(
                            "INSERT INTO memory_tombstones (memory_id, kind, content, reason, ts)"
                            " VALUES (?,?,?,?,?)",
                            (int(memory_id), row["kind"], row["content"], reason, _now()),
                        )
                except Exception:
                    pass
            cur.execute("DELETE FROM memories WHERE id = ?", (int(memory_id),))
            try:
                cur.execute("DELETE FROM memories_vec WHERE doc_id = ?", (int(memory_id),))
            except Exception:
                pass
            conn.commit()
        return True

    def archive(self, memory_id: int, *, reason: str = "decay") -> bool:
        with self._lock:
            conn = self.conn()
            conn.execute(
                "UPDATE memories SET archived = 1, decay = MIN(decay, 0.05) WHERE id = ?",
                (int(memory_id),),
            )
            conn.execute(
                "INSERT INTO memory_tombstones (memory_id, kind, content, reason, ts) "
                "SELECT id, kind, content, ?, ? FROM memories WHERE id = ?",
                (f"archived:{reason}", _now(), int(memory_id)),
            )
            conn.commit()
        return True

    def pin(self, memory_id: int, pinned: bool = True) -> bool:
        with self._lock:
            conn = self.conn()
            conn.execute("UPDATE memories SET pinned = ? WHERE id = ?", (1 if pinned else 0, int(memory_id)))
            conn.commit()
        return True

    def set_expiry(self, memory_id: int, ttl_hours: Optional[float]) -> bool:
        expires = (datetime.now() + timedelta(hours=float(ttl_hours))).isoformat(timespec="seconds") if ttl_hours else None
        with self._lock:
            conn = self.conn()
            conn.execute("UPDATE memories SET expires_ts = ? WHERE id = ?", (expires, int(memory_id)))
            conn.commit()
        return True

    def record_access(self, memory_ids: List[int], query: str = "", event: str = "recall") -> None:
        """Close the loop: what gets used stays fresh, what doesn't decays."""
        ids = [int(i) for i in (memory_ids or []) if i is not None]
        if not ids:
            return
        now = _now()
        with self._lock:
            conn = self.conn()
            cur = conn.cursor()
            for mid in ids:
                try:
                    cur.execute(
                        "UPDATE memories SET access_count = access_count + 1, last_access_ts = ?,"
                        " decay = MIN(1.0, decay + 0.05) WHERE id = ?",
                        (now, mid),
                    )
                    cur.execute(
                        "INSERT INTO memory_access (memory_id, ts, event, query) VALUES (?,?,?,?)",
                        (mid, now, event, (query or "")[:200]),
                    )
                except Exception:
                    continue
            conn.commit()

    # ------------------------------------------------------------------- queries
    def all(
        self,
        kind: Optional[str] = None,
        project: Optional[str] = None,
        limit: int = 1000,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        q = "SELECT * FROM memories"
        conds, args = [], []
        if kind:
            conds.append("kind = ?")
            args.append(kind)
        if project:
            conds.append("project = ?")
            args.append(project)
        if not include_archived:
            conds.append("archived = 0")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            conn = self.conn()
            cur = conn.cursor()
            cur.execute(q, args)
            rows = [dict(r) for r in cur.fetchall()]
        return rows

    def get(self, memory_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self.conn().execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),))
            row = cur.fetchone()
        return dict(row) if row else None

    def access_log(self, memory_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self.conn().execute(
                "SELECT * FROM memory_access WHERE memory_id = ? ORDER BY id DESC LIMIT ?",
                (int(memory_id), int(limit)),
            )
            return [dict(r) for r in cur.fetchall()]

    def tombstones(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self.conn().execute(
                "SELECT * FROM memory_tombstones ORDER BY id DESC LIMIT ?", (int(limit),)
            )
            return [dict(r) for r in cur.fetchall()]

    def count(self, include_archived: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM memories" + ("" if include_archived else " WHERE archived = 0")
        with self._lock:
            return int(self.conn().execute(sql).fetchone()[0])

    def index_stats(self) -> Dict[str, Any]:
        r = self.retriever()
        return r.stats() if r is not None else {"available": False}


class MemoryScorer:
    """Scores memories against a query using the six signals + temporal decay."""

    def __init__(
        self,
        half_life_days: float = None,
        preference_bonus: float = 2.0,
        decay=None,
        apply_decay: bool = True,
    ):
        self.half_life_days = float(half_life_days if half_life_days is not None
                                    else getattr(config, "memory_half_life_days", 30.0))
        self.preference_bonus = preference_bonus
        self.apply_decay = apply_decay
        if decay is None:
            try:
                from .decay import MemoryDecay

                decay = MemoryDecay(half_life_days=self.half_life_days)
            except Exception:
                decay = None
        self.decay = decay

    def recency(self, ts: str, now: Optional[datetime] = None) -> float:
        now = now or datetime.now()
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return 0.0
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        return math.exp(-math.log(2) * age_days / self.half_life_days)

    def score(
        self,
        memory: Dict[str, Any],
        query: str,
        user_preferences: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> float:
        meta = memory.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        importance = float(memory.get("importance") or 5.0) / 10.0  # 0..1
        recency = self.recency(memory.get("ts") or "", now)
        # frequency signal: prefer real access_count (what recall updates) and
        # fall back to the dedupe counter for legacy rows.
        try:
            from .decay import access_count_of

            freq_count = access_count_of({**memory, "metadata": meta})
        except Exception:
            freq_count = float(memory.get("access_count") or meta.get("frequency", 1) or 1)
        frequency = min(freq_count, 10.0) / 10.0
        relevance = _overlap(query, memory.get("content") or "")
        success = memory.get("success")
        success_signal = 0.5  # neutral (None)
        if success is not None:
            success_signal = 1.0 if bool(success) else 0.2
        pref = 0.0
        if user_preferences:
            for k, v in (user_preferences or {}).items():
                if isinstance(v, str) and _overlap(query, v) > 0.3:
                    pref += self.preference_bonus / 10.0
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and _overlap(query, item) > 0.3:
                            pref += self.preference_bonus / 10.0
                elif isinstance(v, dict):
                    for item in v.values():
                        if isinstance(item, str) and _overlap(query, item) > 0.3:
                            pref += self.preference_bonus / 10.0

        raw = (
            2.0 * importance
            + 2.0 * recency
            + 1.5 * frequency
            + 3.0 * relevance
            + 1.0 * success_signal
            + pref
        )
        # Temporal gate: a memory that has decayed away must not dominate a
        # prompt. Applied as a multiplier with a floor so nothing is fully
        # silenced when it is the only lexical hit.
        if self.apply_decay and self.decay is not None:
            try:
                d = self.decay.evaluate({**memory, "metadata": meta}, now)
                floor = float(getattr(config, "memory_decay_floor", 0.35))
                mult = floor + (1.0 - floor) * max(0.0, min(1.0, d.decay))
                raw *= mult
            except Exception:
                pass
        return round(raw, 4)

    def decay_report(self, memory: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
        if self.decay is None:
            return {"decay": 1.0, "band": "hot", "reasons": ["decay engine unavailable"]}
        return self.decay.evaluate(memory, now).to_dict()


class Memory2:
    """High-level API: typed memory + hybrid retrieval + decay + eviction."""

    def __init__(self, db_path: Optional[str] = None, *, index: bool = True):
        self.store = MemoryStore(db_path, index=index)
        self.scorer = MemoryScorer()
        self._hybrid_warned = False

    # ------------------------------------------------------------------ config
    @property
    def hybrid_enabled(self) -> bool:
        return bool(getattr(config, "memory_hybrid_enabled", True)) and self.store.retriever() is not None

    def remember(self, kind: str, content: str, **kwargs) -> Dict[str, Any]:
        return self.store.remember(kind, content, **kwargs)

    # ----------------------------------------------------------------- lexical
    def recall(
        self,
        query: str,
        project: Optional[str] = None,
        kinds: Optional[List[str]] = None,
        limit: int = 10,
        user_preferences: Optional[Dict[str, Any]] = None,
        record_access: bool = True,
    ) -> List[Dict[str, Any]]:
        results = []
        now = datetime.now()
        for kind in kinds or KINDS:
            for mem in self.store.all(kind=kind, project=project):
                mem = dict(mem)
                mem["score"] = self.scorer.score(mem, query, user_preferences, now)
                d = self.scorer.decay_report(mem, now)
                mem["decay"] = d.get("decay", 1.0)
                mem["signals"] = {
                    "importance": mem.get("importance"),
                    "recency": self.scorer.recency(mem.get("ts") or "", now),
                    "relevance": _overlap(query, mem.get("content") or ""),
                    "success": mem.get("success"),
                    "access_count": mem.get("access_count") or 0,
                    "band": d.get("band"),
                }
                results.append(mem)
        results.sort(key=lambda m: m["score"], reverse=True)
        top = results[:limit]
        if record_access and top:
            try:
                self.store.record_access([m["id"] for m in top], query=query)
            except Exception:
                pass
        return top

    # ------------------------------------------------------------------ hybrid
    def hybrid_recall(
        self,
        query: str,
        project: Optional[str] = None,
        kinds: Optional[List[str]] = None,
        limit: int = 10,
        k_rrf: int = None,
        user_preferences: Optional[Dict[str, Any]] = None,
        record_access: bool = True,
    ) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion over BM25 (FTS5) + dense vector candidate lists.

        Falls back to lexical scored recall when FTS5, embeddings, or
        ``sqlite-vec`` are unavailable — the agent never loses recall entirely.
        """
        if not self.hybrid_enabled:
            out = self.recall(query, project=project, kinds=kinds, limit=limit,
                              user_preferences=user_preferences, record_access=record_access)
            for m in out:
                m["rrf_score"] = m["score"]
                m["retrieval"] = {"mode": "lexical-only", "reason": "hybrid disabled or unavailable"}
            return out

        retriever = self.store.retriever()
        if retriever is None:
            return self.recall(query, project=project, kinds=kinds, limit=limit,
                               user_preferences=user_preferences, record_access=record_access)
        if k_rrf:
            try:
                retriever.config.rrf_k = int(k_rrf)
            except Exception:
                pass

        where_parts, params = ["archived = 0"], []
        if project:
            where_parts.append("project = ?")
            params.append(project)
        kinds = kinds or list(KINDS)
        if kinds:
            where_parts.append(f"kind IN ({','.join('?' for _ in kinds)})")
            params.extend(kinds)
        # prior = importance × decay, so durable + fresh memories get a small
        # edge on top of the fused rank.
        prior_select = "MAX(0.0, (importance / 10.0) * COALESCE(decay, 1.0))"
        try:
            hits = retriever.search(
                query,
                limit=limit * 2,
                where=" AND ".join(where_parts),
                params=params,
                prior_select=prior_select,
            )
        except Exception as e:
            out = self.recall(query, project=project, kinds=kinds, limit=limit,
                              user_preferences=user_preferences, record_access=record_access)
            for m in out:
                m["rrf_score"] = m["score"]
                m["retrieval"] = {"mode": "lexical-only", "reason": f"hybrid failed: {e}"}
            return out

        now = datetime.now()
        out: List[Dict[str, Any]] = []
        for h in hits:
            mem = self.store.get(h.id) or {}
            if not mem:
                continue
            mem = dict(mem)
            mem["content"] = mem.get("content") or h.content or ""
            lexical = _overlap(query, mem.get("content") or "")
            mem["score"] = self.scorer.score(mem, query, user_preferences, now)
            mem["rrf_score"] = round(float(h.score), 6)
            mem["signals"] = {
                "importance": mem.get("importance"),
                "recency": self.scorer.recency(mem.get("ts") or "", now),
                "relevance": round(lexical, 4),
                "success": mem.get("success"),
                "access_count": mem.get("access_count") or 0,
                "band": (self.scorer.decay_report(mem, now) or {}).get("band"),
            }
            mem["retrieval"] = {
                "mode": "hybrid",
                "bm25_rank": h.bm25_rank,
                "vector_rank": h.vector_rank,
                "vector_similarity": h.vector_similarity,
                "prior": round(float(h.prior or 0.0), 4),
                "contributions": h.contributions,
                "backend": retriever.stats().get("vector_backend"),
            }
            out.append(mem)
        # fused score is primary; lexical score breaks ties
        out.sort(key=lambda m: (m["rrf_score"], m["score"]), reverse=True)
        out = out[:limit]
        if record_access and out:
            try:
                self.store.record_access([m["id"] for m in out], query=query)
            except Exception:
                pass
        return out

    def explain(self, query: str, limit: int = 5, **kw) -> Dict[str, Any]:
        """Diagnostic view of why each memory ranked where it did."""
        hits = self.hybrid_recall(query, limit=limit, record_access=False, **kw)
        return {
            "query": query,
            "index": self.store.index_stats(),
            "hits": [
                {
                    "id": m.get("id"),
                    "kind": m.get("kind"),
                    "content": (m.get("content") or "")[:160],
                    "rrf_score": m.get("rrf_score"),
                    "score": m.get("score"),
                    "decay": m.get("decay"),
                    "retrieval": m.get("retrieval"),
                    "signals": m.get("signals"),
                }
                for m in hits
            ],
        }

    # ----------------------------------------------------------------- eviction
    def sweep(
        self,
        *,
        project: Optional[str] = None,
        archive_below: float = None,
        purge_below: float = None,
        working_ttl_hours: float = None,
        consolidate: bool = True,
        dry_run: bool = False,
        limit: int = 5000,
    ) -> Dict[str, Any]:
        """Apply the decay lifecycle: decay → archive → purge → consolidate.

        Run this on a schedule (the gateway does it hourly) so stale context
        stops polluting prompts.
        """
        from .decay import MemoryDecay, consolidate as _consolidate

        decay = MemoryDecay(
            half_life_days=float(getattr(config, "memory_half_life_days", 30.0))
        )
        archive_below = (archive_below if archive_below is not None
                         else float(getattr(config, "memory_archive_below", 0.08)))
        purge_below = (purge_below if purge_below is not None
                       else float(getattr(config, "memory_purge_below", 0.02)))
        working_ttl_hours = (working_ttl_hours if working_ttl_hours is not None
                             else float(getattr(config, "memory_working_ttl_hours", 48.0)))

        now = datetime.now()
        report = {
            "checked": 0, "archived": [], "purged": [], "promoted": [], "consolidated": [],
            "dry_run": dry_run, "project": project,
        }
        rows = self.store.all(project=project, limit=limit, include_archived=True)
        for row in rows:
            report["checked"] += 1
            rep = decay.evaluate(row, now)
            action, reasons = decay.plan(
                row, now,
                archive_below=archive_below, purge_below=purge_below,
                working_ttl_hours=working_ttl_hours,
            )
            row["decay"] = rep.decay
            if action == "keep":
                if not dry_run:
                    try:
                        with self.store._lock:
                            self.store.conn().execute(
                                "UPDATE memories SET decay = ? WHERE id = ?", (rep.decay, row["id"])
                            )
                    except Exception:
                        pass
                continue
            entry = {"id": row["id"], "kind": row["kind"], "decay": round(rep.decay, 4),
                     "reasons": reasons, "content": (row.get("content") or "")[:120]}
            if action == "purge":
                report["purged"].append(entry)
                if not dry_run:
                    self.store.forget(int(row["id"]), reason="purge:" + ",".join(reasons)[:120])
            elif action == "archive":
                report["archived"].append(entry)
                if not dry_run and not row.get("archived"):
                    self.store.archive(int(row["id"]), reason=",".join(reasons)[:120])
            elif action == "promote" or (action == "decay" and "promote" in reasons):
                report["promoted"].append(entry)
                if not dry_run:
                    self.store.remember(
                        "semantic",
                        f"{(row.get('content') or '').strip()}",
                        project=row.get("project"),
                        importance=max(6.0, float(row.get("importance") or 6.0)),
                        success=row.get("success"),
                        metadata={"promoted_from": row["id"], "reason": "repeatedly accessed episodic"},
                    )
            elif not dry_run:
                try:
                    with self.store._lock:
                        self.store.conn().execute(
                            "UPDATE memories SET decay = ? WHERE id = ?", (rep.decay, row["id"])
                        )
                except Exception:
                    pass

        if consolidate:
            # merge near-duplicate hot memories so recall is not spammed
            for kind in ("semantic", "procedural"):
                rows_k = [r for r in self.store.all(kind=kind, project=project, limit=500)
                          if not r.get("archived")]
                for group in _consolidate(rows_k, similarity=0.7):
                    keep, *rest = group
                    merged_freq = sum(float(r.get("access_count") or 0) for r in rest) + float(
                        keep.get("access_count") or 0)
                    report["consolidated"].append({
                        "keep": keep["id"],
                        "merged": [r["id"] for r in rest],
                        "access_count": merged_freq,
                    })
                    if not dry_run:
                        for r in rest:
                            self.store.forget(int(r["id"]), reason="consolidated into %s" % keep["id"])
                        try:
                            with self.store._lock:
                                self.store.conn().execute(
                                    "UPDATE memories SET access_count = ?, ts = ? WHERE id = ?",
                                    (merged_freq, _now(), keep["id"]),
                                )
                                self.store.conn().commit()
                        except Exception:
                            pass
        try:
            with self.store._lock:
                self.store.conn().commit()
        except Exception:
            pass
        report["summary"] = {
            k: (len(v) if isinstance(v, list) else v)
            for k, v in report.items()
            if k not in ("dry_run", "project")
        }
        return report

    def forget(
        self,
        memory_id: Optional[int] = None,
        *,
        kind: Optional[str] = None,
        query: str = "",
        reason: str = "manual",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Tombstone memories so recall stops surfacing them.

        By id, or by a `query` (hybrid-matched, so "that postgres note" finds the
        row) optionally narrowed with `kind`. Tombstoned rows stay in the audit
        trail and are excluded from every read path.
        """
        ids: List[int] = []
        if memory_id is not None:
            ids = [int(memory_id)]
        else:
            if not query.strip():
                return {"success": False, "error": "memory_id or query required", "forgotten": []}
            rows = self.hybrid_recall(query, limit=max(1, int(limit)), kinds=[kind] if kind else None)
            ids = [int(r["id"]) for r in rows if r.get("id") is not None]
        forgotten = []
        for mid in ids:
            try:
                if self.store.forget(mid, reason=reason):
                    forgotten.append(mid)
            except Exception:
                continue
        return {
            "success": bool(forgotten),
            "forgotten": forgotten,
            "count": len(forgotten),
            "reason": reason,
        }

    def pin(self, memory_id: int, pinned: bool = True) -> Dict[str, Any]:
        """Pin (or unpin) a memory: exempt from decay, eviction and sweep."""
        ok = False
        try:
            ok = bool(self.store.pin(int(memory_id), bool(pinned)))
        except Exception:
            ok = False
        return {"success": ok, "id": int(memory_id), "pinned": bool(pinned)}

    def compact_working_memory(self, max_age_hours: int = 48) -> Dict[str, Any]:
        """Prune expired short-lived working memory records older than max_age_hours."""
        cutoff_dt = datetime.now() - timedelta(hours=max_age_hours)
        cutoff = cutoff_dt.isoformat(timespec="seconds")
        if max_age_hours <= 0:
            cutoff = _now()  # prune everything currently stored
        with self.store._lock:
            conn = self.store.conn()
            cur = conn.cursor()
            cur.execute("SELECT id FROM memories WHERE kind='working' AND ts <= ?", (cutoff,))
            ids = [int(r[0]) for r in cur.fetchall()]
        for mid in ids:
            self.store.forget(mid, reason="working-memory TTL")
        return {
            "deleted_count": len(ids),
            "cutoff": cutoff,
            "max_age_hours": max_age_hours,
            "ids": ids[:50],
            "tombstoned": True,
        }

    # ------------------------------------------------------------------- prompt
    def recall_context(
        self,
        query: str,
        limit: int = 5,
        max_tokens: int = None,
        hybrid: bool = True,
        per_kind_cap: int = 2,
        project: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Ranked memories + the eviction report (what got dropped and why).

        This is the object the agent loop should use: the text goes into the
        prompt, the rest goes into run events / the dashboard so recall is
        inspectable instead of magic.
        """
        empty = {"text": "", "kept": [], "evicted": [], "tokens": 0,
                 "budget_tokens": 0, "mode": "empty", "ids": [], "index": {}}
        mems = None
        mode = "lexical"
        if hybrid and self.hybrid_enabled:
            try:
                mems = self.hybrid_recall(query, limit=limit * 2, project=project, **kwargs)
                mode = "hybrid"
            except Exception:
                mems = None
        if mems is None:
            mems = self.recall(query, limit=limit * 2, project=project, **kwargs)
        if not mems:
            return empty
        budget = int(max_tokens if max_tokens is not None
                     else getattr(config, "memory_budget_tokens", 600))
        try:
            from .decay import fit_to_budget

            packed = fit_to_budget(
                mems,
                budget_tokens=budget,
                per_kind_cap=per_kind_cap,
                prefix=lambda m: (f"[{m.get('kind')}] ({round(float(m.get('score') or 0), 2)}, "
                                  f"decay {round(float(m.get('decay') or 1), 2)})"),
            )
        except Exception:
            lines = [f"- [{m['kind']}] ({m['score']}) {(m.get('content') or '')[:300]}" for m in mems[:limit]]
            return {**empty, "text": "Relevant memories:\n" + "\n".join(lines),
                    "kept": mems[:limit], "ids": [m.get("id") for m in mems[:limit]]}
        body = packed.get("text") or ""
        evicted = packed.get("evicted") or []
        note = (
            f"\n({len(evicted)} lower-value memor{'y' if len(evicted) == 1 else 'ies'} evicted "
            f"to fit the {budget}-token memory budget)"
            if evicted else ""
        )
        return {
            "text": ("Relevant memories:\n" + body + note) if body else "",
            "kept": packed.get("kept") or [],
            "evicted": evicted,
            "tokens": packed.get("tokens", 0),
            "budget_tokens": budget,
            "utilization": packed.get("utilization", 0.0),
            "mode": mode,
            "ids": [m.get("id") for m in (packed.get("kept") or [])],
            "index": self.store.index_stats(),
        }

    def recall_prompt_block(
        self,
        query: str,
        limit: int = 5,
        max_tokens: int = None,
        hybrid: bool = True,
        **kwargs,
    ) -> str:
        """Memories fitted into a token budget (context eviction) as prompt text."""
        return self.recall_context(
            query, limit=limit, max_tokens=max_tokens, hybrid=hybrid, **kwargs
        ).get("text", "")

    # ------------------------------------------------------------------- ops
    def reindex(self) -> Dict[str, Any]:
        """Rebuild FTS + vector indexes (after a backend/model switch)."""
        r = self.store.retriever()
        if r is None:
            return {"success": False, "error": "hybrid index unavailable"}
        r.backfill_fts()
        res = r.reindex()
        res["fts_rows"] = self.store.count(include_archived=True)
        return res

    def stats(self) -> Dict[str, Any]:
        by_kind: Dict[str, int] = {}
        bands: Dict[str, int] = {}
        with self.store._lock:
            conn = self.store.conn()
            for row in conn.execute("SELECT kind, COUNT(*) c FROM memories WHERE archived=0 GROUP BY kind"):
                by_kind[row["kind"]] = row["c"]
            for row in conn.execute(
                "SELECT CASE WHEN decay >= 0.66 THEN 'hot' WHEN decay >= 0.33 THEN 'warm' "
                "WHEN decay >= 0.15 THEN 'cold' ELSE 'archived' END band, COUNT(*) c "
                "FROM memories WHERE archived=0 GROUP BY band"
            ):
                bands[row["band"] or "unknown"] = row["c"]
            try:
                accesses = conn.execute("SELECT COUNT(*) FROM memory_access").fetchone()[0]
            except Exception:
                accesses = 0
        return {
            "total": self.store.count(),
            "archived": self.store.count(include_archived=True) - self.store.count(),
            "by_kind": by_kind,
            "bands": bands,
            "access_events": accesses,
            "index": self.store.index_stats(),
            "decay": self.scorer.decay.status() if self.scorer.decay else {},
            "budget_tokens": int(getattr(config, "memory_budget_tokens", 600)),
        }


memory2 = Memory2()
