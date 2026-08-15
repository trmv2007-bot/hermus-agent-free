"""Benchmark runner — executes tasks and measures reliability metrics.

The runner:
1. Iterates over benchmark tasks
2. For each task, runs the ComputerAgent
3. Records detailed metrics per task
4. Aggregates scores across dimensions:
   - Task success rate
   - First-attempt success
   - Repair success
   - Replan success
   - False click rate
   - Average retries
   - Average duration
   - Vision calls (approximate)
   - LLM calls (approximate)
   - Token usage (approximate)
   - Resume success
   - Skill reuse success
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..computer_agent import ComputerAgent
from ..episodes import Episode, EpisodeStore, record_episode
from ..skills import ComputerSkillStore
from ..task_store import TaskStore
from .tasks import COMPUTER_TASKS, TaskSpec, get_task
from ...config import config


@dataclass
class TaskResult:
    """Results for one benchmark task execution."""

    task_id: str
    spec_id: str
    prompt: str
    category: str
    difficulty: int
    success: bool
    duration: float
    actions: int = 0
    retries: int = 0
    repairs: int = 0
    verifications: int = 0
    replan_attempts: int = 0
    error: Optional[str] = None
    outcome: str = "UNKNOWN"
    false_clicks: int = 0
    vision_calls: int = 0
    llm_calls: int = 0
    episode_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "spec_id": self.spec_id,
            "prompt": self.prompt,
            "category": self.category,
            "difficulty": self.difficulty,
            "success": self.success,
            "duration": round(self.duration, 2),
            "actions": self.actions,
            "retries": self.retries,
            "repairs": self.repairs,
            "verifications": self.verifications,
            "replan_attempts": self.replan_attempts,
            "error": self.error,
            "outcome": self.outcome,
            "false_clicks": self.false_clicks,
            "vision_calls": self.vision_calls,
            "llm_calls": self.llm_calls,
        }


@dataclass
class BenchmarkResult:
    """Aggregate benchmark results across all tasks."""

    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    total_tasks: int = 0
    total_success: int = 0
    total_failure: int = 0
    success_rate: float = 0.0
    first_attempt_success: int = 0
    # Per-category breakdown
    by_category: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Detailed results
    results: List[TaskResult] = field(default_factory=list)
    # Aggregate metrics
    avg_duration: float = 0.0
    avg_actions: float = 0.0
    avg_retries: float = 0.0
    avg_repairs: float = 0.0
    avg_verifications: float = 0.0
    avg_replan_attempts: float = 0.0
    total_retries: int = 0
    total_repairs: int = 0
    total_replans: int = 0
    total_false_clicks: int = 0
    repair_success_rate: float = 0.0
    false_click_rate: float = 0.0
    resume_success_count: int = 0
    skill_reuse_count: int = 0
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "summary": {
                "total_tasks": self.total_tasks,
                "total_success": self.total_success,
                "total_failure": self.total_failure,
                "success_rate": round(self.success_rate * 100, 1),
                "first_attempt_success": self.first_attempt_success,
                "avg_duration_seconds": round(self.avg_duration, 2),
                "avg_actions": round(self.avg_actions, 1),
                "avg_retries": round(self.avg_retries, 2),
                "avg_repairs": round(self.avg_repairs, 2),
                "avg_verifications": round(self.avg_verifications, 1),
                "avg_replan_attempts": round(self.avg_replan_attempts, 2),
                "repair_success_rate": round(self.repair_success_rate * 100, 1),
                "false_click_rate": round(self.false_click_rate * 100, 1),
                "resume_success_count": self.resume_success_count,
                "skill_reuse_count": self.skill_reuse_count,
            },
            "by_category": self.by_category,
            "results": [r.to_dict() for r in self.results],
            "config": self.config,
        }


class BenchmarkRunner:
    """Runs computer tasks and measures reliability."""

    def __init__(
        self,
        agent: Optional[ComputerAgent] = None,
        dry_run: bool = True,
        episode_store: Optional[EpisodeStore] = None,
        max_tasks: int = 0,  # 0 = all
        categories: Optional[List[str]] = None,
        max_difficulty: int = 3,
        tags: Optional[List[str]] = None,
    ):
        self.agent = agent
        self.dry_run = dry_run
        self.episode_store = episode_store or EpisodeStore()
        self.max_tasks = max_tasks
        self.categories = categories
        self.max_difficulty = max_difficulty
        self.tags = tags

    def run(self) -> BenchmarkResult:
        """Run the benchmark and return results."""
        result = BenchmarkResult()
        result.config = {
            "dry_run": self.dry_run,
            "max_difficulty": self.max_difficulty,
            "categories": self.categories,
            "tags": self.tags,
        }

        # Select tasks
        tasks = [
            t for t in COMPUTER_TASKS
            if t.difficulty <= self.max_difficulty
            and (self.categories is None or t.category in self.categories)
            and (self.tags is None or any(tag in t.tags for tag in self.tags))
        ]
        if self.max_tasks > 0:
            tasks = tasks[:self.max_tasks]

        result.total_tasks = len(tasks)

        for task_spec in tasks:
            task_result = self._run_single(task_spec)
            result.results.append(task_result)

            if task_result.success:
                result.total_success += 1
                if task_result.retries == 0:
                    result.first_attempt_success += 1
            else:
                result.total_failure += 1

            # Per-category tracking
            cat = task_spec.category
            if cat not in result.by_category:
                result.by_category[cat] = {"total": 0, "success": 0, "failure": 0}
            result.by_category[cat]["total"] += 1
            if task_result.success:
                result.by_category[cat]["success"] += 1
            else:
                result.by_category[cat]["failure"] += 1

            # Accumulate metrics
            result.total_retries += task_result.retries
            result.total_repairs += task_result.repairs
            result.total_replans += task_result.replan_attempts
            result.total_false_clicks += task_result.false_clicks

        # Compute aggregates
        n = max(len(result.results), 1)
        result.success_rate = result.total_success / n
        result.avg_duration = sum(r.duration for r in result.results) / n
        result.avg_actions = sum(r.actions for r in result.results) / n
        result.avg_retries = result.total_retries / n
        result.avg_repairs = result.total_repairs / n
        result.avg_verifications = sum(r.verifications for r in result.results) / n
        result.avg_replan_attempts = result.total_replans / n

        total_repaired = sum(1 for r in result.results if r.repairs > 0)
        repairs_fixed = sum(1 for r in result.results if r.repairs > 0 and r.success)
        result.repair_success_rate = repairs_fixed / max(total_repaired, 1)

        total_clicks = sum(r.actions for r in result.results)
        result.false_click_rate = result.total_false_clicks / max(total_clicks, 1)

        return result

    def _run_single(self, task_spec: TaskSpec) -> TaskResult:
        """Run a single benchmark task and record metrics."""
        started_at = time.monotonic()
        task_id = f"benchmark-{task_spec.id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        tr = TaskResult(
            task_id=task_id,
            spec_id=task_spec.id,
            prompt=task_spec.prompt,
            category=task_spec.category,
            difficulty=task_spec.difficulty,
            success=False,
            duration=0.0,
        )

        try:
            if self.agent is not None:
                result = self.agent.run(
                    task_spec.prompt,
                    task_id=task_id,
                    dry_run=self.dry_run,
                )

                tr.success = bool(result.get("success"))
                tr.duration = time.monotonic() - started_at
                tr.actions = len(result.get("actions", []))
                tr.retries = result.get("retries", 0)
                tr.repairs = result.get("repair_steps", len(result.get("repairs", [])))
                tr.verifications = len(result.get("verifications", []))
                tr.outcome = str(result.get("result", "UNKNOWN"))
                tr.error = result.get("error")
                tr.replan_attempts = int(result.get("replan_attempts", 0))
                tr.false_clicks = sum(
                    1 for a in result.get("actions", [])
                    if isinstance(a, dict) and a.get("grounding_failure")
                )

                # Save the episode
                try:
                    path = record_episode(task_id, task_spec.prompt, result)
                    tr.episode_path = path
                except Exception:
                    pass

            tr.duration = time.monotonic() - started_at

        except Exception as exc:
            tr.error = str(exc)
            tr.success = False
            tr.duration = time.monotonic() - started_at
            tr.outcome = "ERROR"

        return tr


def run_benchmark(
    agent: Optional[ComputerAgent] = None,
    dry_run: bool = True,
    max_tasks: int = 0,
    categories: Optional[List[str]] = None,
    **kwargs,
) -> BenchmarkResult:
    """Convenience: create runner and execute benchmark."""
    runner = BenchmarkRunner(
        agent=agent,
        dry_run=dry_run,
        max_tasks=max_tasks,
        categories=categories,
        **kwargs,
    )
    return runner.run()