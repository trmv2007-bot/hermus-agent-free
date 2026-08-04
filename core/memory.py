"""Memory - SQLite FTS5 free, no vector DB cost, plus curated memory, nudges, user modeling (free Honcho alternative)"""
import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from .config import config

class Memory:
    """Free memory system: SQLite FTS5 for session search + curated memory + nudges + user model"""

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or config.resolve_path(config.memory_db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
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
        conn.close()

    def add_session_message(self, session_id: str, role: str, content: str, tool_calls: List[Dict] = None, metadata: Dict = None):
        """Add message to session + FTS index"""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sessions (session_id, timestamp, role, content, tool_calls, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, datetime.now().isoformat(), role, content, json.dumps(tool_calls or []), json.dumps(metadata or {})))
        # Add to FTS
        cur.execute("""
            INSERT INTO sessions_fts (content, session_id, role) VALUES (?, ?, ?)
        """, (content, session_id, role))
        conn.commit()
        conn.close()

        # Also log to trajectory file for batch generation
        try:
            traj_path = config.resolve_path(config.trajectory_path)
            traj_path.parent.mkdir(parents=True, exist_ok=True)
            with open(traj_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                    "role": role,
                    "content": content,
                    "tool_calls": tool_calls
                }) + "\n")
        except:
            pass

    def search_sessions(self, query: str, limit: int = 5) -> List[Dict]:
        """Free FTS5 search - no vector DB needed"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            # FTS5 search with ranking
            cur.execute("""
                SELECT s.*, rank FROM sessions_fts
                JOIN sessions s ON s.rowid = sessions_fts.rowid
                WHERE sessions_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit))
            rows = cur.fetchall()
            result = [dict(r) for r in rows]
        except Exception as e:
            # Fallback LIKE search if FTS fails
            cur.execute("""
                SELECT * FROM sessions WHERE content LIKE ? ORDER BY id DESC LIMIT ?
            """, (f"%{query}%", limit))
            rows = cur.fetchall()
            result = [dict(r) for r in rows]
        conn.close()
        return result

    def summarize_search_results(self, query: str, results: List[Dict]) -> str:
        """LLM summarization for cross-session recall (free via Ollama)"""
        if not results:
            return "No prior sessions found for query."
        # Build context for LLM
        context = "\n".join([f"[{r['timestamp']}] {r['role']}: {r['content'][:500]}" for r in results])
        # Use free LLM to summarize
        try:
            from .llm import free_llm
            messages = [
                {"role": "system", "content": "You are a memory summarizer. Summarize prior sessions relevant to query."},
                {"role": "user", "content": f"Query: {query}\n\nPrior sessions:\n{context}\n\nSummarize what is relevant for cross-session recall."}
            ]
            resp = free_llm.chat(messages)
            return resp.content
        except:
            # Fallback without LLM
            return f"Found {len(results)} prior sessions for '{query}':\n" + context[:1000]

    def curate_memory(self, key: str, value: str, source_session: str = "", importance: int = 5):
        """Agent-curated memory - agent decides what to remember"""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO curated_memory (timestamp, key, value, source_session, importance)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), key, value, source_session, importance))
        conn.commit()
        conn.close()

    def get_curated_memory(self, limit: int = 20) -> List[Dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM curated_memory ORDER BY importance DESC, timestamp DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def periodic_nudges(self) -> List[str]:
        """Periodic nudges - agent asks itself what to persist"""
        # Find sessions from yesterday that were important but not yet curated
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute("SELECT * FROM sessions WHERE timestamp > ? AND role='user' ORDER BY id DESC LIMIT 5", (yesterday,))
        recent = cur.fetchall()
        conn.close()

        nudges = []
        for row in recent:
            # Simple heuristic: if user said "remember" or session had many tool calls, nudge
            content = row[3] if len(row) > 3 else ""
            if "remember" in content.lower() or len(content) > 200:
                nudges.append(f"Should I persist knowledge from session {row[1]}? Content: {content[:100]}")
        return nudges

    # Free Honcho alternative - User Modeling

    def get_user_model_path(self) -> Path:
        return config.resolve_path(config.user_model_path)

    def load_user_model(self) -> Dict:
        path = self.get_user_model_path()
        if not path.exists():
            return {"preferences": {}, "projects": [], "workflows": [], "created": datetime.now().isoformat()}
        try:
            return json.loads(path.read_text())
        except:
            return {}

    def save_user_model(self, model: Dict):
        path = self.get_user_model_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model, indent=2))

    def update_user_model(self, new_info: Dict):
        """Dialectic user modeling - LLM asks what matters, builds model free"""
        model = self.load_user_model()
        # Merge new info
        for k, v in new_info.items():
            if k in model and isinstance(model[k], dict) and isinstance(v, dict):
                model[k].update(v)
            elif k in model and isinstance(model[k], list) and isinstance(v, list):
                model[k].extend(v)
                # Deduplicate
                model[k] = list(dict.fromkeys([json.dumps(x) if isinstance(x, dict) else str(x) for x in model[k]]))
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

    def add_token_usage(self, session_id: str, usage: Dict):
        """Add token usage - free tracking"""
        try:
            conn = sqlite3.connect(str(self.db_path))
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
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Token usage tracking failed: {e}")

    def get_token_usage(self, session_id: str = None, limit: int = 100) -> Dict:
        """Get token usage stats - free"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
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
            conn.close()
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
            conn.close()
            return {"error": str(e), "recent": [], "totals": {"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"total_cost":0.0}}

# Global memory instance
memory = Memory()
