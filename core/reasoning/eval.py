"""Eval Harness (Phase 4) — measure Hermus instead of guessing.

- benchmark tasks from tests/eval/benchmark_tasks.json (5 categories, checks)
- run tasks under a given thinking strategy, score success / steps / failures
- compare two strategies A/B -> winner; history in data/eval_history.json
- CLI: hermus eval run | list | compare | history

The harness forces single-agent mode (council disabled) so strategy effects are
measured in isolation; it is fully offline-safe when run with mock/mock.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from collections.abc import Callable

from ..config import config


class EvalHarness:
    def __init__(
        self,
        tasks_path: Optional[str] = None,
        history_path: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.tasks_path = Path(tasks_path or config.resolve_path("tests/eval/benchmark_tasks.json"))
        self.history_path = Path(history_path or config.resolve_path("data/eval_history.json"))
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.model = model or config.model
        self._ensure_history()

    # ------------------------------------------------------------ tasks

    def load_tasks(self) -> list[dict]:
        if not self.tasks_path.exists():
            return []
        try:
            return json.loads(self.tasks_path.read_text())
        except Exception as e:
            print(f"[Eval] could not load tasks: {e}")
            return []

    def list_categories(self) -> list[str]:
        cats = []
        for t in self.load_tasks():
            if t.get("category") not in cats:
                cats.append(t["category"])
        return cats

    # ------------------------------------------------------------ checks

    def check_task(self, task: dict, response: str) -> dict:
        checks = task.get("checks") or []
        passed, failed = [], []
        for c in checks:
            ctype = c.get("type", "substring")
            value = c.get("value", "")
            ok = False
            try:
                if ctype == "substring":
                    ok = value.lower() in (response or "").lower()
                elif ctype == "not_substring":
                    ok = value.lower() not in (response or "").lower()
                elif ctype == "regex":
                    ok = bool(re.search(value, response or "", re.I))
                elif ctype == "length_gt":
                    ok = len(response or "") > int(value)
                elif ctype == "callable":
                    fn: Callable = c.get("fn")
                    if fn:
                        ok = bool(fn(response))
                else:
                    ok = False
            except Exception:
                ok = False
            entry = {"type": ctype, "value": str(value)[:80], "passed": ok}
            (passed if ok else failed).append(entry)
        return {"passed": passed, "failed": failed, "success": not failed}

    # ------------------------------------------------------------ run

    def run(
        self,
        strategy: str = "auto",
        tasks: Optional[list[dict]] = None,
        limit: Optional[int] = None,
        model: Optional[str] = None,
        tag: str = "",
    ) -> dict:
        """Run benchmark tasks under a strategy. Offline-safe with mock/mock."""
        all_tasks = tasks if tasks is not None else self.load_tasks()
        if limit:
            all_tasks = all_tasks[:limit]
        if not all_tasks:
            return {"error": "no tasks to run", "results": []}

        from ..agent import HermusAgent

        # Isolate the strategy being measured: single-agent, no council
        prev_strategy = getattr(config, "think_strategy", "auto")
        prev_counsel = config.counsel_enabled
        config.think_strategy = strategy if strategy != "auto" else prev_strategy
        config.counsel_enabled = False

        results = []
        try:
            agent = None
            for task in all_tasks:
                prompt = task.get("prompt", "")
                t0 = time.time()
                failures = 0
                try:
                    if agent is None:
                        agent = HermusAgent(model=model or self.model, mode="agent")
                    result = agent.chat(prompt)
                    response = result.get("response", "")
                    failures = sum(
                        1
                        for tr in result.get("tool_results", [])
                        if "error" in json.dumps(tr.get("result", {}), default=str)[:200].lower()
                    )
                    steps = result.get("steps", 0)
                    used_strategy = result.get("strategy", strategy)
                except Exception as e:
                    response = f"(run error: {e})"
                    failures = 1
                    steps = 0
                    used_strategy = strategy
                elapsed = int((time.time() - t0) * 1000)
                check = self.check_task(task, response)
                results.append(
                    {
                        "id": task.get("id"),
                        "category": task.get("category", "general"),
                        "prompt": prompt[:120],
                        "strategy": used_strategy,
                        "success": check["success"],
                        "response": (response or "")[:300],
                        "checks_passed": len(check["passed"]),
                        "checks_failed": len(check["failed"]),
                        "steps": steps,
                        "tool_failures": failures,
                        "latency_ms": elapsed,
                    }
                )
        finally:
            config.think_strategy = prev_strategy
            config.counsel_enabled = prev_counsel

        summary = self._summarize(results)
        summary["strategy"] = strategy if strategy != "auto" else "auto"
        summary["tag"] = tag
        summary["timestamp"] = datetime.now().isoformat()
        summary["results"] = results
        self._save_run(summary)
        return summary

    def _summarize(self, results: list[dict]) -> dict:
        if not results:
            return {"runs": 0, "success_rate": 0.0}
        total = len(results)
        ok = sum(1 for r in results if r["success"])
        steps = [r["steps"] for r in results]
        fails = [r["tool_failures"] for r in results]
        by_cat: dict[str, dict] = {}
        for r in results:
            c = by_cat.setdefault(r["category"], {"runs": 0, "success": 0})
            c["runs"] += 1
            c["success"] += 1 if r["success"] else 0
        return {
            "runs": total,
            "success": ok,
            "success_rate": round(ok / total, 3),
            "avg_steps": round(sum(steps) / total, 2) if total else 0,
            "avg_tool_failures": round(sum(fails) / total, 2) if total else 0,
            "by_category": {k: v for k, v in by_cat.items()},
        }

    # ------------------------------------------------------------ history

    def _ensure_history(self):
        if not self.history_path.exists():
            self.history_path.write_text("[]")

    def _load_history(self) -> list[dict]:
        try:
            return json.loads(self.history_path.read_text())
        except Exception:
            return []

    def _save_run(self, run: dict):
        history = self._load_history()
        history.append(run)
        self.history_path.write_text(json.dumps(history[-100:], indent=2))

    def history(self, limit: int = 20) -> list[dict]:
        h = self._load_history()
        return h[-limit:]

    def summary(self) -> dict:
        h = self._load_history()
        if not h:
            return {"runs": 0}
        last = h[-1]
        return {
            "runs": len(h),
            "last_run": last.get("timestamp"),
            "last_strategy": last.get("strategy"),
            "last_success_rate": last.get("success_rate"),
            "last_runs": last.get("runs"),
            "recent": [
                {"timestamp": r.get("timestamp"), "strategy": r.get("strategy"),
                 "success_rate": r.get("success_rate"), "runs": r.get("runs")}
                for r in h[-5:]
            ],
        }

    # ------------------------------------------------------------ compare

    def compare(
        self,
        strategy_a: str,
        strategy_b: str,
        tasks: Optional[list[dict]] = None,
        limit: Optional[int] = None,
        model: Optional[str] = None,
    ) -> dict:
        """A/B: run both strategies on the same tasks, pick the winner."""
        all_tasks = (tasks if tasks is not None else self.load_tasks())[:limit]
        run_a = self.run(strategy_a, tasks=all_tasks, limit=None, model=model, tag=f"compare:{strategy_a}")
        run_b = self.run(strategy_b, tasks=all_tasks, limit=None, model=model, tag=f"compare:{strategy_b}")

        sa, sb = run_a.get("success_rate", 0), run_b.get("success_rate", 0)
        fa, fb = run_a.get("avg_tool_failures", 0), run_b.get("avg_tool_failures", 0)
        if sa > sb:
            winner = strategy_a
        elif sb > sa:
            winner = strategy_b
        elif fa < fb:
            winner = strategy_a
        elif fb < fa:
            winner = strategy_b
        else:
            winner = "tie"
        return {
            "strategy_a": strategy_a,
            "strategy_b": strategy_b,
            "a": {k: run_a.get(k) for k in ("success_rate", "runs", "avg_steps", "avg_tool_failures")},
            "b": {k: run_b.get(k) for k in ("success_rate", "runs", "avg_steps", "avg_tool_failures")},
            "winner": winner,
            "timestamp": datetime.now().isoformat(),
        }


eval_harness = EvalHarness()
