"""Memory - SQLite FTS5 free, no vector DB cost, plus curated memory, nudges, user modeling (free Honcho alternative)"""
import sqlite3
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any
from ..config import config

class Memory:
    """Free memory system: SQLite FTS5 for session search + curated memory + nudges + user model"""

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or config.resolve_path(config.memory_db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    # ------------------------------------------------------------- connections
    @property
    def _conn(self) -> sqlite3.Connection:
        """Long-lived thread-local connection.

        sqlite3 connections must not cross threads, and the gateway serves
        requests from several worker threads. Instead of paying a
        connect/PRAGMA/close cycle per operation, every thread keeps one
        connection. WAL (set once in ``_init_db``) lets readers proceed while
        a writer commits; ``busy_timeout`` makes concurrent writers wait
        briefly instead of raising 'database is locked'.
        """
        from ..db_registry import db_registry, open_db

        conn = getattr(self._local, "conn", None)
        # A gateway shutdown closes every registered handle; the generation
        # bump is how this thread learns its cached handle is dead and must
        # reopen instead of raising "Cannot operate on a closed database".
        if conn is not None and getattr(self._local, "gen", -1) != db_registry.generation:
            conn = None
        if conn is None:
            # Registered with the process-wide registry so the gateway shutdown
            # can release it. ``check_same_thread=False`` is safe *and*
            # necessary here: the handle is still created per thread and used
            # by that thread only (that is what ``self._local`` guarantees), so
            # nothing gains a second concurrent user — but sqlite's default
            # same-thread check would make the shutdown path unable to close
            # another worker's handle, leaving exactly the unclosed-database
            # ResourceWarning this registry exists to prevent.
            conn = open_db(
                self.db_path,
                owner="memory",
                timeout=10.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA busy_timeout=10000;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA temp_store=MEMORY;")
            except sqlite3.Error:
                pass
            self._local.conn = conn
            self._local.gen = db_registry.generation
        return conn

    def close(self) -> None:
        """Close this thread's cached connection (safe to call repeatedly)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            from ..db_registry import db_registry

            db_registry.unregister(conn)
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    @contextmanager
    def _write_txn(self):
        """Commit on success, roll back on failure.

        With persistent connections a failed write must not leave an open
        transaction pinned to the thread (it would hold locks and leak
        phantom state into later reads on the same connection).
        """
        conn = self._conn
        try:
            yield conn
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise

    def _init_db(self):
        conn = self._conn
        cur = conn.cursor()
        # Optimized: WAL mode for better concurrency + faster writes
        # (journal_mode persists in the DB file; the per-connection pragmas
        # live in Memory._conn).
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA cache_size=-64000;")  # 64MB cache
        except sqlite3.Error:
            pass

        # Sessions table with FTS5 for free full-text search (no Pinecone)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp TEXT,
                role TEXT,
                content TEXT,
                tool_calls TEXT,
                metadata TEXT
            )
        """)
        # Indexes for faster queries - optimized
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_timestamp ON sessions(timestamp);")
        # Project-scoped memory (Phase 4, P4): migration for older DBs
        try:
            cur.execute("ALTER TABLE sessions ADD COLUMN project TEXT DEFAULT 'default';")
        except sqlite3.Error:
            pass  # column already exists
        # FTS5 virtual table for free search
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                content, session_id, role, tokenize='porter'
            )
        """)
        # Curated memory - agent decides what to remember
        cur.execute("""
            CREATE TABLE IF NOT EXISTS curated_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                key TEXT UNIQUE,
                value TEXT,
                source_session TEXT,
                importance INTEGER DEFAULT 5
            )
        """)
        # Skills usage log for self-improvement
        cur.execute("""
            CREATE TABLE IF NOT EXISTS skill_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT,
                timestamp TEXT,
                success BOOLEAN,
                feedback TEXT
            )
        """)
        # Trajectories for research-ready batch generation
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trajectories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                trajectory JSON,
                timestamp TEXT
            )
        """)
        # Token usage tracking - free
        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp TEXT,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cost REAL,
                is_free BOOLEAN
            )
        """)
        conn.commit()

    def add_session_message(self, session_id: str, role: str, content: str, tool_calls: list[dict] = None, metadata: dict = None, project: str = None, tag: dict = None):
        """Add message to session + FTS index.

        Phase 4: `project` scopes messages to a project (P4); `tag` attaches
        reasoning metadata (strategy/difficulty/plan) to the trajectory line (P6).
        """
        project = project or getattr(config, "project", "default")
        with self._write_txn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sessions (session_id, timestamp, role, content, tool_calls, metadata, project)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (session_id, datetime.now().isoformat(), role, content, json.dumps(tool_calls or []), json.dumps(metadata or {}), project))
            # Add to FTS
            cur.execute("""
                INSERT INTO sessions_fts (content, session_id, role) VALUES (?, ?, ?)
            """, (content, session_id, role))

        # Also log to trajectory file for batch generation (+ Phase 4 tag)
        try:
            traj_path = config.resolve_path(config.trajectory_path)
            traj_path.parent.mkdir(parents=True, exist_ok=True)
            line = {
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "role": role,
                "content": content,
                "tool_calls": tool_calls,
            }
            if tag:
                line["tag"] = tag
            with open(traj_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
        except Exception:
            pass

    def search_sessions(self, query: str, limit: int = 5, project: str = None) -> list[dict]:
        """Free FTS5 search - no vector DB needed. Phase 4: project filter."""
        conn = self._conn
        cur = conn.cursor()
        try:
            # FTS5 search with ranking
            if project:
                cur.execute("""
                    SELECT s.*, rank FROM sessions_fts
                    JOIN sessions s ON s.rowid = sessions_fts.rowid
                    WHERE sessions_fts MATCH ? AND s.project = ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, project, limit))
            else:
                cur.execute("""
                    SELECT s.*, rank FROM sessions_fts
                    JOIN sessions s ON s.rowid = sessions_fts.rowid
                    WHERE sessions_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, limit))
            rows = cur.fetchall()
            result = [dict(r) for r in rows]
        except sqlite3.Error:
            # Fallback LIKE search if FTS fails (bad MATCH syntax etc.)
            if project:
                cur.execute("""
                    SELECT * FROM sessions WHERE content LIKE ? AND project = ? ORDER BY id DESC LIMIT ?
                """, (f"%{query}%", project, limit))
            else:
                cur.execute("""
                    SELECT * FROM sessions WHERE content LIKE ? ORDER BY id DESC LIMIT ?
                """, (f"%{query}%", limit))
            rows = cur.fetchall()
            result = [dict(r) for r in rows]
        return result

    def summarize_search_results(self, query: str, results: list[dict]) -> str:
        """LLM summarization for cross-session recall (free via Ollama)"""
        if not results:
            return "No prior sessions found for query."
        # Build context for LLM
        context = "\n".join([f"[{r['timestamp']}] {r['role']}: {r['content'][:500]}" for r in results])
        # Use free LLM to summarize
        try:
            from ..llm import free_llm
            messages = [
                {"role": "system", "content": "You are a memory summarizer. Summarize prior sessions relevant to query."},
                {"role": "user", "content": f"Query: {query}\n\nPrior sessions:\n{context}\n\nSummarize what is relevant for cross-session recall."}
            ]
            resp = free_llm.chat(messages)
            return resp.content
        except Exception:
            # Fallback without LLM
            return f"Found {len(results)} prior sessions for '{query}':\n" + context[:1000]

    def curate_memory(self, key: str, value: str, source_session: str = "", importance: int = 5):
        """Agent-curated memory - agent decides what to remember"""
        with self._write_txn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO curated_memory (timestamp, key, value, source_session, importance)
                VALUES (?, ?, ?, ?, ?)
            """, (datetime.now().isoformat(), key, value, source_session, importance))

    def get_curated_memory(self, limit: int = 20) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM curated_memory ORDER BY importance DESC, timestamp DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def periodic_nudges(self) -> list[str]:
        """Periodic nudges - agent asks itself what to persist"""
        # Find sessions from yesterday that were important but not yet curated
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM sessions WHERE timestamp > ? AND role='user' ORDER BY id DESC LIMIT 5", (yesterday,))
        recent = cur.fetchall()

        nudges = []
        for row in recent:
            # Simple heuristic: if user said "remember" or session had many tool calls, nudge
            content = row["content"] if "content" in row.keys() else ""
            if "remember" in content.lower() or len(content) > 200:
                nudges.append(f"Should I persist knowledge from session {row['session_id']}? Content: {content[:100]}")
        return nudges

    # Free Honcho alternative - User Modeling

    def get_user_model_path(self) -> Path:
        return config.resolve_path(config.user_model_path)

    def load_user_model(self) -> dict:
        path = self.get_user_model_path()
        if not path.exists():
            return {"preferences": {}, "projects": [], "workflows": [], "created": datetime.now().isoformat()}
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def save_user_model(self, model: dict):
        path = self.get_user_model_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model, indent=2))

    @staticmethod
    def _dedupe_preserving_types(items: list[Any]) -> list[Any]:
        """Deduplicate a list without coercing items to strings.

        The previous implementation ran every item through ``str()``/``json.dumps()``
        and kept the *stringified* values, silently turning ``[{"name": "x"}]``
        into ``['{"name": "x"}']`` and corrupting the persisted user model.
        """
        seen: set = set()
        out: list[Any] = []
        for item in items:
            try:
                key = json.dumps(item, sort_keys=True, default=str)
            except (TypeError, ValueError):
                key = str(item)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    def update_user_model(self, new_info: dict):
        """Dialectic user modeling - LLM asks what matters, builds model free"""
        model = self.load_user_model()
        # Merge new info
        for k, v in new_info.items():
            if k in model and isinstance(model[k], dict) and isinstance(v, dict):
                model[k].update(v)
            elif k in model and isinstance(model[k], list) and isinstance(v, list):
                model[k] = self._dedupe_preserving_types(model[k] + v)
            else:
                model[k] = v
        model["last_updated"] = datetime.now().isoformat()
        self.save_user_model(model)

    def dialectic_question(self) -> str:
        """Free Honcho alternative - asks dialectic questions to build user model"""
        questions = [
            "What kind of projects do you work on most?",
            "What’s your preferred coding style or stack?",
            "What matters to you in how I help?",
            "Any workflows you repeat often that I should learn?",
        ]
        model = self.load_user_model()
        if not model.get("projects"):
            return questions[0]
        if not model.get("preferences"):
            return questions[1]
        if len(model.get("workflows", [])) < 2:
            return questions[3]
        return questions[2]

    # Token usage tracking free

    def add_token_usage(self, session_id: str, usage: dict):
        """Add token usage - free tracking"""
        try:
            with self._write_txn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, completion_tokens, total_tokens, cost, is_free)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    datetime.now().isoformat(),
                    usage.get("model",""),
                    usage.get("prompt_tokens",0),
                    usage.get("completion_tokens",0),
                    usage.get("total_tokens",0),
                    usage.get("total_cost",0.0),
                    usage.get("is_free",True)
                ))
        except Exception as e:
            print(f"Token usage tracking failed: {e}")

    def get_token_usage(self, session_id: str = None, limit: int = 100) -> dict:
        """Get token usage stats - free"""
        conn = self._conn
        cur = conn.cursor()
        try:
            if session_id:
                cur.execute("SELECT * FROM token_usage WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit))
            else:
                cur.execute("SELECT * FROM token_usage ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            # Sum totals
            cur.execute("SELECT SUM(prompt_tokens) as p, SUM(completion_tokens) as c, SUM(total_tokens) as t, SUM(cost) as cost FROM token_usage" + (" WHERE session_id=?" if session_id else ""), (session_id,) if session_id else ())
            totals = cur.fetchone()
            return {
                "recent": [dict(r) for r in rows],
                "totals": {
                    "prompt_tokens": totals["p"] or 0,
                    "completion_tokens": totals["c"] or 0,
                    "total_tokens": totals["t"] or 0,
                    "total_cost": totals["cost"] or 0.0
                },
                "count": len(rows)
            }
        except Exception as e:
            return {"error": str(e), "recent": [], "totals": {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"total_cost":0.0}}

# Global memory instance
memory = Memory()
