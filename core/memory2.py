"""Memory 2.0 — typed long-term memory with a scoring/recall engine.

Upgrades the flat ``curated_memory`` key/value store into five typed stores:

- working     — current task/context (short-lived, per session)
- episodic    — what happened during previous tasks
- semantic    — durable facts the agent learned
- procedural  — successful ways of doing things (recipes / playbooks)
- project     — project-specific knowledge (scoped to a project)

Every memory carries a score computed from six signals:

- importance (explicit 0..10)
- recency (exponential decay by age)
- frequency (how often a near-duplicate has been recorded)
- task relevance (lexical overlap with the current query)
- user preference (boost if the user model flags the topic)
- success/failure (procedural/episodic memories get boosted/penalized)

Backed by plain SQLite — free, no vector DB. Compatible with (but independent
of) the existing ``core.memory.Memory``.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime
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


class MemoryStore:
    """Typed memory backed by a single SQLite database."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or config.resolve_path(config.memory2_db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        conn = self._conn()
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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mem_ts ON memories(ts);")
        conn.commit()
        conn.close()

    def _dedupe_key(self, kind: str, content: str) -> str:
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
    ) -> Dict[str, Any]:
        if kind not in KINDS:
            return {"success": False, "error": f"unknown kind '{kind}' (choose {KINDS})"}
        project = project or getattr(config, "project", "default")
        key = self._dedupe_key(kind, content)
        conn = self._conn()
        cur = conn.cursor()
        # frequency signal: bump an existing near-identical memory instead of duping
        cur.execute("SELECT id, metadata FROM memories WHERE key = ?", (key,))
        row = cur.fetchone()
        if row:
            meta = json.loads(row["metadata"] or "{}")
            meta["frequency"] = meta.get("frequency", 1) + 1
            cur.execute(
                "UPDATE memories SET ts = ?, importance = MAX(importance, ?), metadata = ?, success = COALESCE(?, success) WHERE id = ?",
                (datetime.now().isoformat(), importance, json.dumps(meta), success, row["id"]),
            )
            conn.commit()
            conn.close()
            return {"success": True, "id": row["id"], "merged": True}
        cur.execute(
            "INSERT INTO memories (kind, content, project, importance, success, key, metadata, ts) VALUES (?,?,?,?,?,?,?,?)",
            (
                kind,
                content,
                project,
                importance,
                success,
                key,
                json.dumps(metadata or {"frequency": 1}),
                datetime.now().isoformat(),
            ),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "id": new_id, "merged": False}

    def forget(self, memory_id: int) -> bool:
        conn = self._conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        conn.close()
        return True

    def all(self, kind: Optional[str] = None, project: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        q = "SELECT * FROM memories"
        conds, args = [], []
        if kind:
            conds.append("kind = ?")
            args.append(kind)
        if project:
            conds.append("project = ?")
            args.append(project)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        conn = self._conn()
        cur = conn.cursor()
        cur.execute(q, args)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows


class MemoryScorer:
    """Scores memories against a query using the six signals."""

    def __init__(self, half_life_days: float = 30.0, preference_bonus: float = 2.0):
        self.half_life_days = half_life_days
        self.preference_bonus = preference_bonus

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
        frequency = min(float(meta.get("frequency", 1)), 10.0) / 10.0
        relevance = _overlap(query, memory.get("content") or "")
        success = memory.get("success")
        success_signal = 0.5  # neutral (None)
        if success is not None:
            # SQLite stores booleans as 1.0 / 0.0 floats
            success_signal = 1.0 if bool(success) else 0.2
        # user preference boost for matching topics
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

        score = (
            2.0 * importance
            + 2.0 * recency
            + 1.5 * frequency
            + 3.0 * relevance
            + 1.0 * success_signal
            + pref
        )
        return round(score, 4)


class Memory2:
    """High-level API: typed memory + ranked recall."""

    def __init__(self, db_path: Optional[str] = None):
        self.store = MemoryStore(db_path)
        self.scorer = MemoryScorer()

    def remember(self, kind: str, content: str, **kwargs) -> Dict[str, Any]:
        return self.store.remember(kind, content, **kwargs)

    def recall(
        self,
        query: str,
        project: Optional[str] = None,
        kinds: Optional[List[str]] = None,
        limit: int = 10,
        user_preferences: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        results = []
        for kind in kinds or KINDS:
            for mem in self.store.all(kind=kind, project=project):
                mem = dict(mem)
                mem["score"] = self.scorer.score(mem, query, user_preferences)
                mem["signals"] = {
                    "importance": mem.get("importance"),
                    "recency": self.scorer.recency(mem.get("ts") or ""),
                    "relevance": _overlap(query, mem.get("content") or ""),
                    "success": mem.get("success"),
                }
                results.append(mem)
        results.sort(key=lambda m: m["score"], reverse=True)
        return results[:limit]

    def hybrid_recall(
        self,
        query: str,
        project: Optional[str] = None,
        kinds: Optional[List[str]] = None,
        limit: int = 10,
        k_rrf: int = 60,
        user_preferences: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion (RRF) combining scored lexical recall with vector embeddings."""
        lexical = self.recall(query, project=project, kinds=kinds, limit=limit * 2, user_preferences=user_preferences)
        lexical_ranks = {m.get("id"): idx + 1 for idx, m in enumerate(lexical) if m.get("id")}

        vector_ranks = {}
        try:
            from .embeddings import semantic_embeddings
            v_hits = semantic_embeddings.search(query, limit=limit * 2)
            for v_idx, hit in enumerate(v_hits):
                hit_text = hit.get("text", "")
                for m in lexical:
                    if m.get("content") and (m.get("content") in hit_text or hit_text in m.get("content")):
                        if m.get("id") not in vector_ranks:
                            vector_ranks[m.get("id")] = v_idx + 1
        except Exception:
            pass

        rrf_scored = []
        for mem in lexical:
            m_id = mem.get("id")
            r_lex = lexical_ranks.get(m_id, limit * 2)
            r_vec = vector_ranks.get(m_id, limit * 2)
            rrf = (1.0 / (k_rrf + r_lex)) + (1.0 / (k_rrf + r_vec))
            mem_copy = dict(mem)
            mem_copy["rrf_score"] = round(rrf, 6)
            mem_copy["hybrid_rank"] = {
                "lexical_rank": r_lex,
                "vector_rank": r_vec if m_id in vector_ranks else None,
            }
            rrf_scored.append(mem_copy)

        rrf_scored.sort(key=lambda m: m["rrf_score"], reverse=True)
        return rrf_scored[:limit]

    def compact_working_memory(self, max_age_hours: int = 48) -> Dict[str, Any]:
        """Prune expired short-lived working memory records older than max_age_hours."""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        conn = self.store._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM memories WHERE kind='working' AND ts <= ?", (cutoff,))
            count = cur.fetchone()[0]
            cur.execute("DELETE FROM memories WHERE kind='working' AND ts <= ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()
        return {"deleted_count": count, "cutoff": cutoff, "max_age_hours": max_age_hours}

    def recall_prompt_block(self, query: str, limit: int = 5, **kwargs) -> str:
        mems = self.recall(query, limit=limit, **kwargs)
        if not mems:
            return ""
        lines = [f"- [{m['kind']}] ({m['score']}) {m['content'][:300]}" for m in mems]
        return "Relevant memories:\n" + "\n".join(lines)


memory2 = Memory2()
