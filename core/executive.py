"""Executive Brain for Hermus.

The Executive Brain is a small, deterministic control-plane layer that sits
above the existing mission runtime. It keeps durable executive state (active
goals, priorities, recent outcomes and observations), derives a bounded goal
plan, and exposes a stable hand-off contract for the universal runtime.

It intentionally does *not* execute tools itself. Execution remains owned by
``core.runtime`` / ``core.mission`` so safety, sandboxing, verification and
approval gates cannot be bypassed by a higher-level planner.

This is the foundation for a Jarvis/FAIRY-style executive loop:

    perceive -> maintain world state -> prioritize -> plan -> delegate ->
    execute -> verify -> learn

The first implementation is provider-independent and SQLite-backed. Later
model-driven planning can replace ``plan_goal`` without changing the state
contract or execution boundary.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS executive_goals (
    goal_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_executive_goals_status_priority
    ON executive_goals(status, priority DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS executive_events (
    event_id TEXT PRIMARY KEY,
    goal_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_executive_events_goal_time
    ON executive_events(goal_id, created_at DESC);

CREATE TABLE IF NOT EXISTS executive_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class ExecutiveStep:
    """A bounded planning step handed to the mission runtime."""

    id: str
    objective: str
    role: str = "executor"
    expected_output: str = "analysis"
    depends_on: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective": self.objective,
            "role": self.role,
            "expected_output": self.expected_output,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class ExecutivePlan:
    """Provider-independent plan contract."""

    goal_id: str
    goal: str
    priority: int
    steps: tuple[ExecutiveStep, ...]
    success_criteria: tuple[str, ...]
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal": self.goal,
            "priority": self.priority,
            "steps": [step.to_dict() for step in self.steps],
            "success_criteria": list(self.success_criteria),
            "context": dict(self.context),
        }


class ExecutiveBrain:
    """Persistent executive state and deterministic planning facade.

    The brain is deliberately safe to instantiate from any entry point. The
    database path may be supplied explicitly for tests or set with
    ``HERMUS_EXECUTIVE_DB``. No tool execution occurs in this class.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        configured = db_path or os.getenv("HERMUS_EXECUTIVE_DB")
        self.db_path = Path(configured or (Path.home() / ".hermus" / "executive.sqlite3"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------ state
    def set_state(self, key: str, value: Any) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO executive_state(key, value_json, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False, default=str), now),
            )
            conn.commit()

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value_json FROM executive_state WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except (TypeError, json.JSONDecodeError):
            return default

    def observe(self, event_type: str, payload: dict[str, Any] | None = None, *, goal_id: str | None = None) -> str:
        """Record an observation without executing or mutating external systems."""
        event_id = f"evt_{uuid.uuid4().hex[:16]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO executive_events(event_id, goal_id, event_type, payload_json, created_at) VALUES(?, ?, ?, ?, ?)",
                (event_id, goal_id, event_type, json.dumps(payload or {}, ensure_ascii=False, default=str), time.time()),
            )
            conn.commit()
        return event_id

    def recent_events(self, *, goal_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        sql = "SELECT * FROM executive_events"
        args: list[Any] = []
        if goal_id:
            sql += " WHERE goal_id=?"
            args.append(goal_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "goal_id": row["goal_id"],
                    "event_type": row["event_type"],
                    "payload": payload,
                    "created_at": row["created_at"],
                }
            )
        return result

    # ------------------------------------------------------------------- goals
    def create_goal(self, goal: str, *, priority: int = 50, metadata: dict[str, Any] | None = None) -> str:
        goal = str(goal or "").strip()
        if not goal:
            raise ValueError("goal must not be empty")
        goal_id = f"goal_{uuid.uuid4().hex[:16]}"
        now = time.time()
        priority = max(0, min(int(priority), 100))
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO executive_goals(goal_id, goal, status, priority, created_at, updated_at, metadata_json) "
                "VALUES(?, ?, 'active', ?, ?, ?, ?)",
                (goal_id, goal, priority, now, now, json.dumps(metadata or {}, ensure_ascii=False, default=str)),
            )
            conn.commit()
        self.observe("goal_created", {"goal": goal, "priority": priority}, goal_id=goal_id)
        return goal_id

    def update_goal(self, goal_id: str, *, status: str | None = None, priority: int | None = None) -> bool:
        allowed = {"active", "paused", "completed", "failed", "cancelled"}
        if status is not None and status not in allowed:
            raise ValueError(f"invalid executive goal status: {status}")
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status=?")
            values.append(status)
        if priority is not None:
            fields.append("priority=?")
            values.append(max(0, min(int(priority), 100)))
        if not fields:
            return False
        fields.append("updated_at=?")
        values.append(time.time())
        values.append(goal_id)
        with self._lock, self._connect() as conn:
            cur = conn.execute(f"UPDATE executive_goals SET {', '.join(fields)} WHERE goal_id=?", values)
            conn.commit()
        if cur.rowcount:
            self.observe("goal_updated", {"status": status, "priority": priority}, goal_id=goal_id)
        return bool(cur.rowcount)

    def active_goals(self, *, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM executive_goals WHERE status='active' ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._goal_row(row) for row in rows]

    @staticmethod
    def _goal_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return {
            "goal_id": row["goal_id"],
            "goal": row["goal"],
            "status": row["status"],
            "priority": row["priority"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": metadata,
        }

    # ------------------------------------------------------------------- plan
    def plan_goal(
        self,
        goal: str,
        *,
        goal_id: str | None = None,
        priority: int = 50,
        success_criteria: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ExecutivePlan:
        """Create a conservative plan that can be handed to MissionEngine.

        This decomposition is intentionally deterministic. It prevents the
        executive layer from becoming an unbounded second agent. A future LLM
        planner can produce richer steps, but it must still return this bounded
        contract before execution.
        """
        goal = str(goal or "").strip()
        if not goal:
            raise ValueError("goal must not be empty")
        gid = goal_id or self.create_goal(goal, priority=priority)
        low = goal.lower()

        steps: list[ExecutiveStep] = [
            ExecutiveStep("understand", "Inspect the current state and constraints relevant to the goal.", "planner", "analysis"),
            ExecutiveStep(
                "plan", "Create an executable plan with explicit expected outputs.", "architect", "analysis", ("understand",)
            ),
            ExecutiveStep("execute", goal, "executor", "change", ("plan",)),
            ExecutiveStep(
                "verify", "Verify the requested result using objective evidence.", "verifier", "analysis", ("execute",)
            ),
        ]
        if any(word in low for word in ("fix", "repair", "debug", "until", "test")):
            steps.append(
                ExecutiveStep(
                    "repair",
                    "If verification fails, diagnose the failure and repair only what is necessary, then re-verify.",
                    "repairer",
                    "change",
                    ("verify",),
                )
            )

        criteria = tuple(success_criteria or ["The requested objective is completed and independently verified."])
        plan = ExecutivePlan(
            goal_id=gid,
            goal=goal,
            priority=max(0, min(int(priority), 100)),
            steps=tuple(steps),
            success_criteria=criteria,
            context=dict(context or {}),
        )
        self.observe("plan_created", {"steps": [s.to_dict() for s in plan.steps]}, goal_id=gid)
        return plan

    def handoff(self, plan: ExecutivePlan) -> dict[str, Any]:
        """Return a runtime-safe hand-off payload.

        The payload contains intent and requirements, not executable commands.
        The existing runtime remains responsible for approvals, tools,
        sandboxing and verification.
        """
        return {
            "goal_id": plan.goal_id,
            "goal": plan.goal,
            "priority": plan.priority,
            "requirements": list(plan.success_criteria),
            "subgoals": [step.objective for step in plan.steps if step.id not in {"understand", "plan"}],
            "executive_plan": plan.to_dict(),
        }


# Process-local default; callers can replace it in tests or instantiate with a
# project-specific database path. Importing this module performs only local DB
# initialization and never executes external actions.
executive_brain = ExecutiveBrain()

__all__ = ["ExecutiveBrain", "ExecutivePlan", "ExecutiveStep", "executive_brain"]
