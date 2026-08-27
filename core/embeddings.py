"""
Semantic Memory / Embeddings - Free local stack
- Ollama embeddings (nomic-embed-text) when available
- Hashing fallback embedding (no deps) so hybrid search always works offline
- SQLite storage of vectors + chunk text (no Pinecone)
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import config

# Default free embedding model on Ollama
DEFAULT_EMBED_MODEL = "nomic-embed-text"
FALLBACK_DIM = 256


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9_]{2,}", (text or "").lower())


def _hash_embed(text: str, dim: int = FALLBACK_DIM) -> List[float]:
    """Deterministic bag-of-tokens hashing trick — offline free fallback."""
    vec = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _pack_vector(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vector(blob: bytes) -> List[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + chunk_size])
        i += max(1, chunk_size - overlap)
    return chunks


class EmbeddingStore:
    """Local embedding store backed by SQLite."""

    def __init__(self, db_path: str = None, model: str = None):
        self.db_path = Path(db_path or config.resolve_path(getattr(config, "embeddings_db_path", "data/embeddings.db")))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.model = model or getattr(config, "embedding_model", DEFAULT_EMBED_MODEL)
        self.ollama_url = getattr(config, "ollama_base_url", "http://localhost:11434")
        self._backend = None  # "ollama" | "hash"
        self._dim = FALLBACK_DIM
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                chunk_id TEXT,
                content TEXT,
                metadata TEXT,
                vector BLOB,
                dim INTEGER,
                backend TEXT,
                created TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_emb_source ON embeddings(source);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_emb_chunk ON embeddings(chunk_id);")
        conn.commit()
        conn.close()

    def available(self) -> bool:
        """Always available — hash fallback if Ollama embed model missing."""
        return True

    def backend_info(self) -> Dict:
        self._ensure_backend()
        return {
            "backend": self._backend,
            "model": self.model if self._backend == "ollama" else "hash-fallback",
            "dim": self._dim,
            "db": str(self.db_path),
            "count": self.count(),
        }

    def _ensure_backend(self):
        if self._backend:
            return
        pref = str(getattr(config, "embedding_backend", "auto") or "auto").lower()
        if pref in ("hash", "none", "local", "off"):
            self._backend = "hash"
            self._dim = FALLBACK_DIM
            return
        # Probe Ollama embeddings
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.model, "prompt": "ping"},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                emb = data.get("embedding") or []
                if emb:
                    self._backend = "ollama"
                    self._dim = len(emb)
                    return
        except Exception:
            pass
        self._backend = "hash"
        self._dim = FALLBACK_DIM

    def embed(self, text: str) -> List[float]:
        self._ensure_backend()
        text = (text or "")[:8000]
        if self._backend == "ollama":
            try:
                resp = requests.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                    timeout=60,
                )
                resp.raise_for_status()
                emb = resp.json().get("embedding") or []
                if emb:
                    # normalize
                    norm = math.sqrt(sum(x * x for x in emb)) or 1.0
                    return [x / norm for x in emb]
            except Exception:
                # fall through to hash
                pass
        return _hash_embed(text, self._dim)

    def count(self) -> int:
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM embeddings")
        n = cur.fetchone()[0]
        conn.close()
        return n

    def add_text(
        self,
        text: str,
        metadata: Optional[Dict] = None,
        source: str = "manual",
        chunk_id: str = None,
    ) -> Dict:
        if not text or not text.strip():
            return {"success": False, "error": "empty text"}
        from datetime import datetime

        vec = self.embed(text)
        cid = chunk_id or hashlib.sha1(f"{source}:{text[:200]}".encode()).hexdigest()[:16]
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        # Upsert-ish: delete same chunk_id then insert
        cur.execute("DELETE FROM embeddings WHERE chunk_id=?", (cid,))
        cur.execute(
            """
            INSERT INTO embeddings (source, chunk_id, content, metadata, vector, dim, backend, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                cid,
                text,
                json.dumps(metadata or {}),
                _pack_vector(vec),
                len(vec),
                self._backend or "hash",
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        return {"success": True, "chunk_id": cid, "source": source, "dim": len(vec)}

    def add_chunks(self, texts: List[str], source: str = "manual", metadata: Optional[Dict] = None) -> Dict:
        added = 0
        for i, t in enumerate(texts):
            r = self.add_text(t, metadata={**(metadata or {}), "chunk_index": i}, source=source)
            if r.get("success"):
                added += 1
        return {"success": True, "added": added, "source": source}

    def search(self, query: str, limit: int = 5, source: str = None) -> Dict:
        qvec = self.embed(query)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if source:
            cur.execute("SELECT * FROM embeddings WHERE source=?", (source,))
        else:
            cur.execute("SELECT * FROM embeddings")
        rows = cur.fetchall()
        conn.close()

        scored: List[Tuple[float, Dict]] = []
        for r in rows:
            try:
                vec = _unpack_vector(r["vector"])
                # If dims mismatch (backend switch), re-embed content with current backend once
                if len(vec) != len(qvec):
                    vec = self.embed(r["content"])
                score = _cosine(qvec, vec)
                scored.append(
                    (
                        score,
                        {
                            "id": r["id"],
                            "source": r["source"],
                            "chunk_id": r["chunk_id"],
                            "content": r["content"][:1500],
                            "metadata": json.loads(r["metadata"] or "{}"),
                            "score": round(score, 4),
                            "backend": r["backend"],
                        },
                    )
                )
            except Exception:
                continue
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:limit]]
        return {
            "query": query,
            "results": results,
            "count": len(results),
            "mode": "semantic",
            "backend": self.backend_info(),
        }

    def hybrid_search(self, query: str, limit: int = 5) -> Dict:
        """Merge FTS5 keyword hits with semantic hits."""
        from .memory import memory

        fts = []
        try:
            fts = memory.search_sessions(query, limit=limit)
        except Exception:
            fts = []
        sem = self.search(query, limit=limit)
        # Normalize FTS rows
        fts_norm = []
        for r in fts:
            fts_norm.append(
                {
                    "source": "fts5_session",
                    "content": (r.get("content") or "")[:1500],
                    "session_id": r.get("session_id"),
                    "role": r.get("role"),
                    "timestamp": r.get("timestamp"),
                    "score": 0.5,  # baseline keyword score
                    "mode": "fts5",
                }
            )
        # Merge by content prefix de-dupe
        seen = set()
        merged = []
        for item in sem.get("results", []) + fts_norm:
            key = (item.get("content") or "")[:120]
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        merged.sort(key=lambda x: x.get("score", 0), reverse=True)
        summary = ""
        try:
            summary = memory.summarize_search_results(query, fts[:3] if fts else [])
        except Exception:
            summary = f"Hybrid search: {len(merged)} hits"
        return {
            "query": query,
            "results": merged[:limit],
            "summary": summary,
            "mode": "hybrid",
            "semantic_count": len(sem.get("results", [])),
            "fts_count": len(fts_norm),
            "backend": self.backend_info(),
        }

    def ingest_path(self, path: str, source: str = None) -> Dict:
        """Ingest file or directory into semantic memory."""
        p = Path(path).expanduser()
        if not p.exists():
            return {"success": False, "error": f"Path not found: {path}"}

        files: List[Path] = []
        if p.is_file():
            files = [p]
        else:
            for ext in ("*.md", "*.txt", "*.py", "*.json", "*.csv", "*.rst", "*.html"):
                files.extend(p.rglob(ext))
            files = files[:200]  # safety cap

        total_chunks = 0
        ingested_files = 0
        errors = []
        src_base = source or str(p)

        for f in files:
            try:
                text = self._read_file_text(f)
                if not text.strip():
                    continue
                chunks = chunk_text(text)
                r = self.add_chunks(
                    chunks,
                    source=f"{src_base}:{f.name}",
                    metadata={"path": str(f), "file": f.name},
                )
                total_chunks += r.get("added", 0)
                ingested_files += 1
            except Exception as e:
                errors.append(f"{f}: {e}")

        return {
            "success": True,
            "path": str(p),
            "files": ingested_files,
            "chunks": total_chunks,
            "errors": errors[:10],
            "backend": self.backend_info(),
        }

    def _read_file_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            # Best-effort PDF text without hard dependency
            try:
                # Try pypdf if installed
                from pypdf import PdfReader

                reader = PdfReader(str(path))
                return "\n".join([(page.extract_text() or "") for page in reader.pages])[:100000]
            except Exception:
                try:
                    raw = path.read_bytes()
                    # crude extract of readable strings
                    return " ".join(re.findall(rb"[\x20-\x7e]{6,}", raw[:500000]))[:50000]
                except Exception:
                    return ""
        try:
            return path.read_text(encoding="utf-8", errors="ignore")[:200000]
        except Exception:
            return ""

    def clear(self, source: str = None) -> Dict:
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        if source:
            cur.execute("DELETE FROM embeddings WHERE source LIKE ?", (f"{source}%",))
        else:
            cur.execute("DELETE FROM embeddings")
        conn.commit()
        n = cur.rowcount
        conn.close()
        return {"success": True, "deleted": n}


# Global store
embedding_store = EmbeddingStore()
