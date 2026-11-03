"""Harness runner — execute a task suite against an agent and record everything.

This is the outer layer that turns the HERMUS agent loop into a *harness*:

1. resolve a suite (builtin name or JSON path),
2. for every task, build an isolated workspace under
   ``data/harness/runs/<run_id>/tasks/<task_id>/workspace``, inject its files,
   run its ``setup`` commands,
3. run the agent on the prompt inside that workspace (step budget + wall-clock
   timeout), capturing response, steps, tool calls and the trajectory,
4. verify the result with the task's checks,
5. write per-task artifacts plus a ``report.json`` / ``report.md`` and append
   the run to ``index.json`` so runs can be listed, shown and compared later.

The agent under test is pluggable: pass ``agent_factory`` (the test suite uses
it to inject scripted agents). The default factory builds a real
:class:`core.agent.HermusAgent`, so ``hermus harness run smoke`` measures the
actual agent — with ``--model mock/mock`` it runs fully offline.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import config
from ..log import get_logger
from .models import MOCK_REF, ping_model, resolve_model_spec
from .report import render_compare, render_markdown
from .tasks import Suite, Task, resolve_suite
from .verify import verify_task

logger = get_logger(__name__)


def _safe_id(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s)) or "task"


def default_agent_factory(task: Task, workspace: Path, *, model: str | None, max_steps: int) -> Any:
    """Build the real HERMUS agent for one task run. Imported lazily — heavy."""
    from ..agent import HermusAgent

    return HermusAgent(model=model, mode="agent", max_steps=max_steps)


@dataclass
class RunOptions:
    """Knobs for one suite run.

    ``model`` is a flexible spec (see :mod:`core.harness.models`): ``auto``,
    a role name, ``provider/model``, a bare provider, or ``mock/mock``. It is
    resolved against the canonical runtime catalog — which covers API-key/auth
    hosted providers and local runtimes (ollama/lmstudio/nollama) — before the
    run starts; the resolved ref plus a preflight ping land in the report.
    """

    model: str | None = None
    limit: int | None = None
    only: list[str] | None = None
    max_steps: int | None = None
    timeout_s: float = 300.0
    tag: str = ""
    out_dir: str | Path | None = None
    keep_workspace: bool = True
    agent_factory: Callable[..., Any] | None = None  # (task, workspace, model=, max_steps=) -> agent
    on_task_done: Callable[[int, int, dict], None] | None = None  # live progress callback
    preflight: bool = True  # ping the resolved model once before the suite
    workers: int = 1  # parallel task execution (requires isolated=True for agent tasks)
    max_tokens_est: int | None = None  # budget: run is flagged over-budget if total_tokens_est exceeds this
    bus: Any = None  # optional EventBus for run events (defaults to the global bus)
    isolated: bool = False  # run each agent task in its own subprocess (own cwd)


@dataclass
class HarnessRunner:
    """Runs suites and keeps the run history under ``runs_dir``."""

    runs_dir: Path = field(default_factory=lambda: config.resolve_path("data/harness/runs"))

    def __post_init__(self) -> None:
        if not self.runs_dir:
            self.runs_dir = config.resolve_path("data/harness/runs")
        self.runs_dir = Path(self.runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- public

    def run_suite(self, source: str | Suite, options: RunOptions | None = None) -> dict[str, Any]:
        """Run a suite (name, path, or loaded :class:`Suite`) and return the report."""
        options = options or RunOptions()
        suite = resolve_suite(source) if isinstance(source, str) else source
        tasks = suite.select(limit=options.limit, only=options.only)
        if not tasks:
            raise ValueError(f"suite '{suite.name}' selected 0 tasks")

        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:6]}"
        if options.tag:
            run_id = f"{run_id}-{_safe_id(options.tag)}"
        run_dir = Path(options.out_dir) / run_id if options.out_dir else self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Resolve the model spec (any API-key/auth hosted provider or local
        # runtime from the canonical catalog, or mock/mock offline). With a
        # custom agent_factory the spec is passed through untouched.
        if options.agent_factory is not None:
            model = options.model or MOCK_REF
            resolution = {
                "ok": True,
                "spec": options.model or MOCK_REF,
                "ref": model,
                "source": "custom_factory",
                "note": "custom agent_factory",
            }
        else:
            resolution = resolve_model_spec(options.model or "auto")
            if not resolution.get("ok"):
                raise ValueError(f"cannot resolve model '{options.model}': {resolution.get('note')}")
            model = resolution["ref"]
        report: dict[str, Any] = {
            "run_id": run_id,
            "suite": suite.name,
            "suite_source": suite.source,
            "model": model,
            "model_spec": options.model or "auto",
            "model_resolution": resolution,
            "tag": options.tag,
            "timestamp": datetime.now().isoformat(),
            "options": {
                "limit": options.limit,
                "only": options.only,
                "max_steps": options.max_steps,
                "timeout_s": options.timeout_s,
                "keep_workspace": options.keep_workspace,
            },
            "tasks": [],
        }
        if options.preflight and options.agent_factory is None:
            report["preflight"] = ping_model(model, timeout_s=min(30.0, options.timeout_s))
            if not report["preflight"].get("ok"):
                logger.warning(f"[Harness] preflight ping failed for {model}: {report['preflight'].get('error')}")
        wall0 = time.time()
        if options.workers > 1 or options.isolated:
            results: list[dict[str, Any] | None] = [None] * len(tasks)
            with ThreadPoolExecutor(max_workers=max(1, int(options.workers))) as pool:
                futs = {pool.submit(self._run_task, task, run_dir, model, options): i for i, task in enumerate(tasks)}
                for fut in as_completed(futs):
                    i = futs[fut]
                    try:
                        results[i] = fut.result()
                    except Exception as e:
                        results[i] = {"id": tasks[i].id, "status": "error", "success": False, "error": f"{type(e).__name__}: {e}"}
                    if options.on_task_done:
                        try:
                            options.on_task_done(i + 1, len(tasks), results[i])
                        except Exception:
                            pass
            report["tasks"] = [r for r in results if r is not None]
        else:
            for i, task in enumerate(tasks, 1):
                result = self._run_task(task, run_dir, model, options)
                report["tasks"].append(result)
                if options.on_task_done:
                    try:
                        options.on_task_done(i, len(tasks), result)
                    except Exception:
                        pass
        report["summary"] = self._summarize(report["tasks"], time.time() - wall0)
        mt = getattr(options, "max_tokens_est", None)
        report["summary"]["budget_tokens_est"] = mt
        report["summary"]["budget_exceeded"] = bool(mt and report["summary"].get("total_tokens_est", 0) > mt)
        report["report_json"] = str(run_dir / "report.json")
        report["report_md"] = str(run_dir / "report.md")

        (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
        (run_dir / "report.md").write_text(render_markdown(report))
        # ---- product hooks: cost estimate, regression vs previous run, bus events
        s = report["summary"]
        try:
            from ..token_counter import token_counter

            cost = token_counter.estimate_cost(int(s.get("total_tokens_est", 0) or 0), 0, model)
            if isinstance(cost, dict):
                s["cost_est_usd"] = cost.get("usd", cost.get("cost_usd", cost.get("total_usd")))
            else:
                s["cost_est_usd"] = cost
        except Exception:
            s["cost_est_usd"] = None
        prev = self.latest_run(suite.name, model=model, before=run_id)
        reg = None
        if prev is not None:
            drop = float(prev.get("success_rate", 1.0)) - float(s.get("success_rate", 0.0))
            if drop > 0:
                failed_ids = [t.get("id") for t in report["tasks"] if not t.get("success")]
                reg = {
                    "previous_run_id": prev.get("run_id"),
                    "previous_success_rate": prev.get("success_rate"),
                    "success_rate": s.get("success_rate"),
                    "drop": round(drop, 4),
                    "failed_tasks": failed_ids,
                }
        s["regression"] = reg
        bus = options.bus
        if bus is None:
            try:
                from ..events import get_bus

                bus = get_bus()
            except Exception:
                bus = None
        if bus is not None:
            try:
                from ..sentinel import envelope_from_dict

                base_args = {
                    "suite": suite.name,
                    "model": model,
                    "run_id": run_id,
                    "passed": s.get("passed", 0),
                    "runs": s.get("runs", 0),
                    "success_rate": s.get("success_rate", 0.0),
                    "total_tokens_est": s.get("total_tokens_est", 0),
                    "budget_exceeded": bool(s.get("budget_exceeded")),
                }
                failed = int(s.get("failed", 0) or 0)
                bus.publish(
                    envelope_from_dict(
                        {
                            "type": "harness.run.failed" if failed or s.get("budget_exceeded") else "harness.run.completed",
                            "command": f"harness run {suite.name}",
                            "status": "failed" if failed or s.get("budget_exceeded") else "succeeded",
                            "args": base_args,
                        }
                    )
                )
                if reg:
                    bus.publish(
                        envelope_from_dict(
                            {
                                "type": "harness.regression",
                                "command": f"{suite.name} regressed vs {reg['previous_run_id']}",
                                "status": "failed",
                                "args": {**base_args, **reg, "severity": "high"},
                            }
                        )
                    )
            except Exception as e:
                logger.warning(f"[Harness] run event publish failed: {e}")
        self._index(run_dir, run_id, report)
        logger.info(f"[Harness] run {run_id}: {report['summary']['passed']}/{report['summary']['runs']} passed")
        return report

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._read_index()[-limit:]

    def latest_run(self, suite: str, model: str | None = None, *, before: str | None = None) -> dict[str, Any] | None:
        """Most recent index entry for a suite (optionally a model), optionally before a run_id."""
        for row in reversed(self._read_index()):
            if row.get("suite") != suite:
                continue
            if model is not None and row.get("model") != model:
                continue
            if before is not None and row.get("run_id") == before:
                continue
            return row
        return None

    def suite_stats(self, per_suite_runs: int = 20) -> list[dict[str, Any]]:
        """Product rollup per suite: run count, last run, avg success rate, flaky tasks."""
        index = self._read_index()
        by_suite: dict[str, list[dict[str, Any]]] = {}
        for row in index:
            by_suite.setdefault(row.get("suite") or "?", []).append(row)
        out = []
        for suite, rows in sorted(by_suite.items()):
            rows = rows[-per_suite_runs:]
            rates = [float(r.get("success_rate", 0.0) or 0.0) for r in rows]
            # flaky tasks: pass rate strictly between 0 and 1 across recent reports
            task_stats: dict[str, list[bool]] = {}
            for row in rows:
                rep = self.load_run(row.get("run_id"))
                for t in (rep or {}).get("tasks", []):
                    task_stats.setdefault(t.get("id"), []).append(bool(t.get("success")))
            flaky = sorted(tid for tid, vals in task_stats.items() if vals and 0 < sum(vals) < len(vals))
            out.append(
                {
                    "suite": suite,
                    "runs": len(rows),
                    "avg_success_rate": round(sum(rates) / len(rates), 4) if rates else 0.0,
                    "last": rows[-1],
                    "models": sorted({r.get("model") for r in rows if r.get("model")}),
                    "flaky_tasks": flaky,
                }
            )
        return out

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        path = self.runs_dir / _safe_id(run_id) / "report.json"
        if not path.is_file():
            # tolerate a partial id prefix
            matches = [p for p in self.runs_dir.glob(f"{_safe_id(run_id)}*") if (p / "report.json").is_file()]
            if len(matches) == 1:
                path = matches[0] / "report.json"
            else:
                return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[Harness] cannot read run {run_id}: {e}")
            return None

    def compare(self, run_a: str, run_b: str) -> dict[str, Any]:
        a, b = self.load_run(run_a), self.load_run(run_b)
        if a is None:
            raise ValueError(f"run '{run_a}' not found")
        if b is None:
            raise ValueError(f"run '{run_b}' not found")
        tasks_a = {t["id"]: t for t in a.get("tasks", [])}
        tasks_b = {t["id"]: t for t in b.get("tasks", [])}
        common = sorted(set(tasks_a) & set(tasks_b))
        both_pass = sum(1 for t in common if tasks_a[t].get("success") and tasks_b[t].get("success"))
        regressions = [t for t in common if tasks_a[t].get("success") and not tasks_b[t].get("success")]
        fixed = [t for t in common if not tasks_a[t].get("success") and tasks_b[t].get("success")]
        sa = a.get("summary", {}).get("success_rate", 0.0)
        sb = b.get("summary", {}).get("success_rate", 0.0)
        comp = {
            "run_a": a.get("run_id"),
            "run_b": b.get("run_id"),
            "suite_a": a.get("suite"),
            "suite_b": b.get("suite"),
            "model_a": a.get("model"),
            "model_b": b.get("model"),
            "success_rate_a": sa,
            "success_rate_b": sb,
            "delta": round(sb - sa, 3),
            "common_tasks": len(common),
            "both_pass": both_pass,
            "regressions": regressions,
            "fixed": fixed,
            "only_in_a": sorted(set(tasks_a) - set(tasks_b)),
            "only_in_b": sorted(set(tasks_b) - set(tasks_a)),
            "winner": "a" if sa > sb else ("b" if sb > sa else "tie"),
            "timestamp": datetime.now().isoformat(),
        }
        return comp

    def compare_markdown(self, run_a: str, run_b: str) -> str:
        return render_compare(self.compare(run_a, run_b))

    # ------------------------------------------------------------- one task

    @staticmethod
    def _empty_verification() -> dict[str, Any]:
        return {
            "success": False,
            "score": 0.0,
            "checks": [],
            "checks_total": 0,
            "checks_passed": 0,
            "response": "",
            "steps": 0,
            "tool_calls": [],
            "tool_results_count": 0,
        }

    def _run_task(self, task: Task, run_dir: Path, model: str, options: RunOptions) -> dict[str, Any]:
        task_dir = run_dir / "tasks" / _safe_id(task.id)
        ws = task_dir / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        result: dict[str, Any] = {
            "id": task.id,
            "category": task.category,
            "prompt": task.prompt,
            "status": "ok",
            "success": False,
            "workspace": str(ws),
            "metadata": dict(task.metadata),
            **self._empty_verification(),
        }

        # 1. seed files
        for rel, content in task.files.items():
            target = ws / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        # 2. setup commands
        setup_log: list[dict[str, Any]] = []
        for cmd in task.setup:
            t0 = time.time()
            try:
                proc = subprocess.run(cmd, shell=True, cwd=str(ws), capture_output=True, text=True, timeout=120)
                entry = {
                    "cmd": cmd,
                    "returncode": proc.returncode,
                    "stdout": (proc.stdout or "")[:2000],
                    "stderr": (proc.stderr or "")[:2000],
                    "duration_ms": int((time.time() - t0) * 1000),
                }
            except Exception as e:
                entry = {"cmd": cmd, "returncode": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}", "duration_ms": 0}
            setup_log.append(entry)
            if entry["returncode"] != 0:
                result.update(status="setup_failed", error=f"setup command failed: {cmd!r}", setup=setup_log)
                self._save_task(task_dir, task, result, None)
                return result
        if setup_log:
            result["setup"] = setup_log

        # 3. run the task driver inside the workspace (agent LLM loop, or the
        #    always-alert sentinel core for driver="sentinel" tasks)
        max_steps = task.max_steps or options.max_steps or getattr(config, "max_tool_steps", 8)
        timeout_s = float(task.timeout_s or options.timeout_s)
        factory = options.agent_factory or default_agent_factory
        t0 = time.time()
        response = ""
        steps = 0
        tool_calls: list[str] = []
        tool_results: list[dict] = []
        trajectory: list[dict] = []
        error = ""
        driver = getattr(task, "driver", "agent") or "agent"
        if driver == "ambient":
            try:
                from ..ambient_loop import run_ambient_task

                out = run_ambient_task(task, ws)
                response = out["response"]
                steps = out.get("steps", 0)
                tool_calls = out.get("tool_calls", [])
                tool_results = out.get("tool_results", [])
            except Exception as e:
                logger.warning(f"[Harness] task {task.id} ambient error: {e}")
                result.update(status="error", error=f"{type(e).__name__}: {e}")
            duration_ms = int((time.time() - t0) * 1000)
            verification = verify_task(task, workspace=ws, response=response, tool_calls=tool_calls)
            result.update(
                success=bool(verification["success"]) and result["status"] == "ok",
                score=verification["score"],
                checks=verification["checks"],
                checks_total=verification["checks_total"],
                checks_passed=verification["checks_passed"],
                response=response[:4000],
                steps=steps,
                tool_calls=tool_calls,
                tool_results_count=len(tool_results),
                duration_ms=duration_ms,
                error=error or result.get("error", ""),
                driver=driver,
            )
            self._save_task(task_dir, task, result, None)
            if not options.keep_workspace:
                result["workspace"] = "(removed)"
                shutil.rmtree(ws, ignore_errors=True)
            return result
        if driver == "sentinel":
            try:
                from ..sentinel import run_sentinel_task

                out = run_sentinel_task(task, ws, model=model)
                response = out["response"]
                steps = out.get("steps", 0)
                tool_calls = out.get("tool_calls", [])
                tool_results = out.get("tool_results", [])
            except Exception as e:
                logger.warning(f"[Harness] task {task.id} sentinel error: {e}")
                result.update(status="error", error=f"{type(e).__name__}: {e}")
            duration_ms = int((time.time() - t0) * 1000)
            verification = verify_task(task, workspace=ws, response=response, tool_calls=tool_calls)
            result.update(
                success=bool(verification["success"]) and result["status"] == "ok",
                score=verification["score"],
                checks=verification["checks"],
                checks_total=verification["checks_total"],
                checks_passed=verification["checks_passed"],
                response=response[:4000],
                steps=steps,
                tool_calls=tool_calls,
                tool_results_count=len(tool_results),
                duration_ms=duration_ms,
                error=error or result.get("error", ""),
                driver=driver,
            )
            self._save_task(task_dir, task, result, None)
            if not options.keep_workspace:
                result["workspace"] = "(removed)"
                shutil.rmtree(ws, ignore_errors=True)
            return result
        tokens_est = 0
        trajectory: list[dict] = []
        try:
            if options.isolated and driver == "agent" and options.agent_factory is None:
                chat = self._run_worker_subprocess(task, task_dir, ws, model, max_steps, timeout_s)
                if chat is None:
                    result.update(
                        status="timeout",
                        error=f"agent exceeded {timeout_s}s",
                        duration_ms=int((time.time() - t0) * 1000),
                    )
                    self._save_task(task_dir, task, result, None)
                    return result
                tokens_est = int(chat.pop("tokens_est", 0) or 0)
            else:
                agent = factory(task, ws, model=model, max_steps=max_steps)
                chat = self._chat_with_timeout(agent, task.prompt, ws, timeout_s)
                trajectory = list(getattr(agent, "trajectory", []) or [])
            if chat is None:
                result.update(
                    status="timeout",
                    error=f"agent exceeded {timeout_s}s",
                    duration_ms=int((time.time() - t0) * 1000),
                )
                self._save_task(task_dir, task, result, None)
                return result
            response = str(chat.get("response") or chat.get("final_answer") or "")
            if not tokens_est:
                tokens_est = self._estimate_tokens(trajectory, response)
            steps = int(chat.get("steps") or 0)
            tool_results = list(chat.get("tool_results") or [])
            tool_calls = [str(tr.get("tool") or "") for tr in tool_results] or [str(t) for t in chat.get("tool_calls") or []]
            if chat.get("error"):
                error = str(chat["error"])
        except Exception as e:
            logger.warning(f"[Harness] task {task.id} agent error: {e}")
            result.update(status="error", error=f"{type(e).__name__}: {e}")
        duration_ms = int((time.time() - t0) * 1000)

        # 4. verify
        verification = verify_task(task, workspace=ws, response=response, tool_calls=tool_calls)
        result.update(
            success=bool(verification["success"]) and result["status"] == "ok",
            score=verification["score"],
            checks=verification["checks"],
            checks_total=verification["checks_total"],
            checks_passed=verification["checks_passed"],
            response=response[:4000],
            steps=steps,
            tool_calls=tool_calls,
            tool_results_count=len(tool_results),
            duration_ms=duration_ms,
            error=error or result.get("error", ""),
            tokens_est=tokens_est,
            driver=driver,
        )
        self._save_task(task_dir, task, result, trajectory if result["status"] == "ok" else None)
        if not options.keep_workspace:
            result["workspace"] = "(removed)"
            shutil.rmtree(ws, ignore_errors=True)
        return result

    def _run_worker_subprocess(
        self, task: Task, task_dir: Path, ws: Path, model: str, max_steps: int, timeout_s: float
    ) -> dict | None:
        """Run one agent task in an isolated subprocess (own cwd). None on timeout."""
        import subprocess as _sp
        import sys as _sys

        task_dir.mkdir(parents=True, exist_ok=True)
        task_file = task_dir / "task.json"
        task_file.write_text(json.dumps(task.to_dict(), default=str))
        cmd = [
            _sys.executable,
            "-m",
            "core.harness.worker",
            "--task",
            str(task_file),
            "--workspace",
            str(ws),
            "--model",
            model,
            "--max-steps",
            str(max_steps),
        ]
        root = config.resolve_path(".")
        try:
            proc = _sp.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout_s + 60,  # grace beyond the harness timeout
            )
        except _sp.TimeoutExpired:
            return None
        for line in reversed((proc.stdout or "").splitlines()):
            if line.startswith("HARNESS_RESULT "):
                try:
                    return json.loads(line[len("HARNESS_RESULT ") :])
                except Exception:
                    return None
            if line.startswith("HARNESS_FATAL "):
                try:
                    return json.loads(line[len("HARNESS_FATAL ") :])
                except Exception:
                    return None
        return {
            "response": "",
            "steps": 0,
            "tool_calls": [],
            "error": f"worker exited {proc.returncode}: {(proc.stderr or '')[:300]}",
        }

    @staticmethod
    def _estimate_tokens(trajectory: list, response: str) -> int:
        try:
            from ..token_counter import token_counter

            msgs = [m for m in trajectory if isinstance(m, dict)]
            msgs.append({"role": "assistant", "content": response})
            return int(token_counter.count_messages(msgs))
        except Exception:
            return max(1, len(response or "") // 4)

    @staticmethod
    def _chat_with_timeout(agent: Any, prompt: str, workspace: Path, timeout_s: float) -> dict | None:
        """Run ``agent.chat`` in the workspace cwd with a wall-clock timeout.

        Returns the chat dict, or None on timeout (the stuck worker thread is
        abandoned — the harness moves on and records the timeout honestly).
        """
        prev_cwd = os.getcwd()
        pool = ThreadPoolExecutor(max_workers=1)
        timed_out = False
        chat = None
        try:
            os.chdir(workspace)
            fut = pool.submit(agent.chat, prompt)
            try:
                chat = fut.result(timeout=timeout_s)
            except TimeoutError:
                timed_out = True
        finally:
            os.chdir(prev_cwd)
            pool.shutdown(wait=not timed_out, cancel_futures=True)
        return None if timed_out else chat

    # ------------------------------------------------------------- storage

    def _save_task(self, task_dir: Path, task: Task, result: dict[str, Any], trajectory: list | None) -> None:
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task.json").write_text(json.dumps(task.to_dict(), indent=2, default=str))
        (task_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
        if trajectory is not None:
            (task_dir / "trajectory.json").write_text(json.dumps(trajectory, indent=2, default=str))

    def _index_path(self) -> Path:
        return self.runs_dir / "index.json"

    def _read_index(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._index_path().read_text(encoding="utf-8"))
        except Exception:
            return []

    def _index(self, run_dir: Path, run_id: str, report: dict[str, Any]) -> None:
        index = self._read_index()
        s = report.get("summary", {})
        index.append(
            {
                "run_id": run_id,
                "timestamp": report.get("timestamp"),
                "suite": report.get("suite"),
                "model": report.get("model"),
                "tag": report.get("tag", ""),
                "runs": s.get("runs", 0),
                "passed": s.get("passed", 0),
                "success_rate": s.get("success_rate", 0.0),
                "total_tokens_est": s.get("total_tokens_est", 0),
                "cost_est_usd": s.get("cost_est_usd"),
                "budget_exceeded": bool(s.get("budget_exceeded")),
                "regression": bool(s.get("regression")),
            }
        )
        self._index_path().write_text(json.dumps(index[-200:], indent=2))

    def _summarize(self, results: list[dict[str, Any]], wall_s: float) -> dict[str, Any]:
        total = len(results)
        passed = sum(1 for r in results if r.get("success"))
        by_cat: dict[str, dict[str, int]] = {}
        for r in results:
            c = by_cat.setdefault(r.get("category", "general"), {"runs": 0, "passed": 0})
            c["runs"] += 1
            c["passed"] += 1 if r.get("success") else 0
        steps = [r.get("steps", 0) for r in results if r.get("status") == "ok"]
        durations = [r.get("duration_ms", 0) for r in results]
        tokens = [int(r.get("tokens_est", 0) or 0) for r in results]
        statuses: dict[str, int] = {}
        for r in results:
            key = r.get("status", "unknown")
            statuses[key] = statuses.get(key, 0) + 1
        return {
            "runs": total,
            "passed": passed,
            "failed": total - passed,
            "success_rate": round(passed / total, 3) if total else 0.0,
            "by_category": {k: {**v, "rate": round(v["passed"] / v["runs"], 3)} for k, v in by_cat.items()},
            "avg_steps": round(sum(steps) / len(steps), 2) if steps else 0,
            "avg_duration_ms": round(sum(durations) / len(durations)) if durations else 0,
            "total_tokens_est": sum(tokens),
            "avg_tokens_est": round(sum(tokens) / len(tokens)) if tokens else 0,
            "wall_ms": int(wall_s * 1000),
            "statuses": statuses,
        }
