"""Lessons Store — the experience feedback loop (Phase 3).

Closes the half-open self-improvement loop: reflections, user corrections, tool
failures and skill failures are distilled into small reusable LESSONS, and the
top relevant lessons are injected into every system prompt so the agent stops
repeating its own past mistakes at the moment it is reasoning.

Storage: `lessons` table in the existing data/memory.db (SQLite, free, WAL).
Relevance: keyword-overlap scoring (zero tokens), recency tie-break.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..config import config

_CORRECTION_WORDS = (
    "wrong", "incorrect", "that's not", "thats not", "that is not", "not what i",
    "no,", "not that", "fix it", "fix this", "stop", "don't do that", "dont do that",
    "actually", "you missed", "you forgot", "i meant", "never mind that",
)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "was", "were", "be", "been", "it", "this", "that", "i", "you", "we",
    "they", "he", "she", "not", "no", "but", "do", "does", "did", "have", "has",
    "will", "would", "should", "can", "could", "from", "at", "by", "as", "your",
    "my", "our", "their", "please", "about", "what", "when", "where", "how",
}


class LessonsStore:
    """SQLite-backed store of distilled lessons + relevance retrieval."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or config.resolve_path(config.memory_db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    keywords TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    times_applied INTEGER DEFAULT 0,
                    outcome_improved INTEGER DEFAULT 0,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lessons_category ON lessons(category);"
            )

    # ---------------------------------------------------------------- write

    def add(
        self,
        lesson: str,
        category: str = "general",
        keywords: str = "",
        source: str = "",
        dedupe: bool = True,
    ) -> dict[str, Any]:
        """Add a lesson. dedupe=True skips near-identical lessons added recently."""
        lesson = (lesson or "").strip()
        if len(lesson) < 10:
            return {"success": False, "error": "lesson too short"}
        if not keywords:
            keywords = " ".join(self._tokens(lesson)[:12])
        if dedupe and self._exists_recent(lesson, category):
            return {"success": False, "error": "duplicate recent lesson", "duplicate": True}
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO lessons (lesson, category, keywords, source, created_at) VALUES (?,?,?,?,?)",
                (lesson, category, keywords, source, datetime.now().isoformat()),
            )
            lesson_id = cur.lastrowid
        return {"success": True, "id": lesson_id, "lesson": lesson, "category": category}

    def _exists_recent(self, lesson: str, category: str, window: int = 50) -> bool:
        norm = self._normalize(lesson)
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT lesson FROM lessons WHERE category=? ORDER BY id DESC LIMIT ?",
                    (category, window),
                ).fetchall()
            for (existing,) in rows:
                if norm and self._normalize(existing) == norm:
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", (text or "").lower())

    @staticmethod
    def _tokens(text: str) -> list[str]:
        words = re.findall(r"[a-z0-9']{3,}", (text or "").lower())
        return [w for w in words if w not in _STOPWORDS]

    # ---------------------------------------------------------------- read

    def relevant(self, text: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Top lessons for the current task: keyword-overlap score + recency."""
        limit = limit or getattr(config, "lessons_in_prompt", 8)
        tokens = set(self._tokens(text))
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT id, lesson, category, keywords, times_applied, outcome_improved, created_at FROM lessons ORDER BY id DESC LIMIT 200"
                ).fetchall()
        except Exception:
            return []
        scored = []
        for rid, lesson, category, keywords, applied, improved, created in rows:
            kw = set((keywords or "").split())
            score = len(tokens & kw)
            # decay: recent lessons rank slightly higher on ties
            scored.append(
                {
                    "id": rid,
                    "lesson": lesson,
                    "category": category,
                    "keywords": keywords,
                    "times_applied": applied,
                    "outcome_improved": improved,
                    "created_at": created,
                    "score": score,
                }
            )
        scored.sort(key=lambda r: (r["score"], r["created_at"]), reverse=True)
        top = [r for r in scored if r["score"] > 0][:limit]
        return top

    def mark_applied(self, lesson_id: int):
        try:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE lessons SET times_applied = times_applied + 1 WHERE id=?",
                    (lesson_id,),
                )
        except Exception:
            pass

    def mark_improved(self, lesson_id: int):
        try:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE lessons SET outcome_improved = outcome_improved + 1 WHERE id=?",
                    (lesson_id,),
                )
        except Exception:
            pass

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT id, lesson, category, times_applied, outcome_improved, created_at FROM lessons ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [
                {
                    "id": rid,
                    "lesson": lesson,
                    "category": category,
                    "times_applied": applied,
                    "outcome_improved": improved,
                    "created_at": created,
                }
                for rid, lesson, category, applied, improved, created in rows
            ]
        except Exception:
            return []

    def stats(self) -> dict[str, Any]:
        try:
            with self._conn() as conn:
                total = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
                by_cat = conn.execute(
                    "SELECT category, COUNT(*) FROM lessons GROUP BY category"
                ).fetchall()
                applied = conn.execute(
                    "SELECT COALESCE(SUM(times_applied),0) FROM lessons"
                ).fetchone()[0]
            return {
                "total": total,
                "by_category": {c: n for c, n in by_cat},
                "times_applied_total": applied,
            }
        except Exception as e:
            return {"error": str(e)}

    # ---------------------------------------------------------------- distillers

    def distill_user_correction(self, user_message: str) -> Optional[dict[str, Any]]:
        """User pushed back on the previous answer -> lesson."""
        low = (user_message or "").lower()
        if not any(w in low for w in _CORRECTION_WORDS):
            return None
        keywords = " ".join(self._tokens(user_message)[:10])
        return self.add(
            lesson=(
                f"User correction: '{user_message[:160]}' — verify this area carefully "
                "before answering again; do not repeat the previous mistake."
            ),
            category="user_correction",
            keywords=keywords,
            source="agent_chat",
        )

    def distill_tool_failure(self, tool_name: str, error_text: str) -> Optional[dict[str, Any]]:
        if not error_text:
            return None
        err = error_text.strip()[:140]
        return self.add(
            lesson=(
                f"Tool {tool_name} failed: {err}. Prefer a fallback path "
                "(web_read / browser_navigate / retry with simpler args)."
            ),
            category="tool_failure",
            keywords=f"{tool_name} failed error fallback retry",
            source="agent_chat",
        )

    def distill_reflection(self, reflection: dict[str, Any]) -> list[dict[str, Any]]:
        """Self-improvement reflection mistakes -> lessons."""
        out = []
        for m in reflection.get("mistakes", [])[:5]:
            res = self.add(
                lesson=f"Reflection: {m[:180]}",
                category="reflection",
                source="self_improvement",
            )
            if res.get("success"):
                out.append(res)
        return out

    def distill_skill_failure(self, skill_name: str, feedback: str) -> Optional[dict[str, Any]]:
        if not feedback:
            return None
        return self.add(
            lesson=f"Skill {skill_name} failed: {feedback[:140]}. Improve the skill or avoid this path.",
            category="skill_failure",
            keywords=f"{skill_name} skill failed",
            source="skill_use",
        )

    def to_prompt_block(self, text: str) -> str:
        """Render relevant lessons for system-prompt injection."""
        lessons = self.relevant(text)
        if not lessons:
            return ""
        for l in lessons:
            self.mark_applied(l["id"])
        return "Lessons learned (from past sessions):\n" + "\n".join(
            f"- [{l['category']}] {l['lesson'][:220]}" for l in lessons
        )


lessons_store = LessonsStore()
