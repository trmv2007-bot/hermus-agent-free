"""Harness task suites — the "instances" a harness runs an agent against.

A suite is a JSON file, either a bare list of task dicts (compatible with
``tests/eval/benchmark_tasks.json``) or an object ``{"name": ..., "tasks": [...]}``.

Task fields::

    id         unique within the suite (required)
    category   grouping label for reports (default "general")
    prompt     what the agent is asked to do (required)
    files      {relpath: content} injected into the task workspace before the run
    setup      [shell command] run inside the workspace before the agent starts
    checks     [check dict] verified after the run — see core/harness/verify.py
    max_steps  per-task agent step budget (overrides the runner default)
    timeout_s  per-task wall-clock budget for the agent turn
    metadata   free-form dict, copied into the report

Suites are resolved by name from ``harness/suites/<name>.json`` (committed,
ships with the repo) or by explicit path to any JSON file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import config
from ..log import get_logger

logger = get_logger(__name__)

#: Committed suites that ship with the repository.
BUILTIN_SUITES_DIR = "harness/suites"


class SuiteError(ValueError):
    """Raised when a suite file is missing or malformed."""


@dataclass
class Task:
    """One harness instance: a prompt, an optional workspace seed, and checks.

    ``driver`` selects what runs the task: ``agent`` (default — the LLM agent
    gets ``prompt``) or ``sentinel`` (the always-alert core gets ``event`` /
    ``events`` and its decisions become the verifiable output). ``use_llm_triage``
    lets a sentinel task consult a model for act/ignore decisions.
    """

    id: str
    prompt: str
    category: str = "general"
    files: dict[str, str] = field(default_factory=dict)
    setup: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    max_steps: int | None = None
    timeout_s: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    driver: str = "agent"
    event: dict[str, Any] | None = None
    events: list[dict[str, Any]] | None = None
    use_llm_triage: bool = False
    ticks: list[list[dict[str, Any]]] | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        if not isinstance(raw, dict):
            raise SuiteError(f"task must be an object, got {type(raw).__name__}")
        tid = str(raw.get("id") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not tid:
            raise SuiteError("task is missing required field 'id'")
        if not prompt:
            raise SuiteError(f"task '{tid}' is missing required field 'prompt'")
        files = raw.get("files") or {}
        if not isinstance(files, dict):
            raise SuiteError(f"task '{tid}': 'files' must be an object of path -> content")
        setup = raw.get("setup") or []
        if isinstance(setup, str):
            setup = [setup]
        if not isinstance(setup, list) or not all(isinstance(s, str) for s in setup):
            raise SuiteError(f"task '{tid}': 'setup' must be a list of shell commands")
        checks = raw.get("checks") or []
        if not isinstance(checks, list):
            raise SuiteError(f"task '{tid}': 'checks' must be a list")
        max_steps = raw.get("max_steps")
        if max_steps is not None:
            max_steps = int(max_steps)
        timeout_s = raw.get("timeout_s")
        if timeout_s is not None:
            timeout_s = float(timeout_s)
        driver = str(raw.get("driver") or "agent").strip().lower()
        if driver not in ("agent", "sentinel", "ambient"):
            raise SuiteError(f"task '{tid}': unknown driver '{driver}' (choose agent|sentinel|ambient)")
        event = raw.get("event")
        if event is not None and not isinstance(event, dict):
            raise SuiteError(f"task '{tid}': 'event' must be an object")
        events = raw.get("events") or []
        if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
            raise SuiteError(f"task '{tid}': 'events' must be a list of objects")
        if driver == "sentinel" and not event and not events:
            raise SuiteError(f"task '{tid}': sentinel tasks need 'event' or 'events'")
        ticks = raw.get("ticks")
        if ticks is not None:
            if not isinstance(ticks, list) or not all(isinstance(t, list) and all(isinstance(i, dict) for i in t) for t in ticks):
                raise SuiteError(f"task '{tid}': 'ticks' must be a list of attention-item lists")
        if driver == "ambient" and not ticks:
            raise SuiteError(f"task '{tid}': ambient tasks need 'ticks'")
        return cls(
            id=tid,
            prompt=prompt,
            category=str(raw.get("category") or "general"),
            files={str(k): str(v) for k, v in files.items()},
            setup=list(setup),
            checks=list(checks),
            max_steps=max_steps,
            timeout_s=timeout_s,
            metadata=dict(raw.get("metadata") or {}),
            driver=driver,
            event=event,
            events=list(events),
            use_llm_triage=bool(raw.get("use_llm_triage", False)),
            ticks=ticks if isinstance(ticks, list) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "prompt": self.prompt,
            "files": dict(self.files),
            "setup": list(self.setup),
            "checks": list(self.checks),
            "max_steps": self.max_steps,
            "timeout_s": self.timeout_s,
            "metadata": dict(self.metadata),
            "driver": self.driver,
            "event": self.event,
            "events": self.events,
            "use_llm_triage": self.use_llm_triage,
            "ticks": self.ticks,
        }


@dataclass
class Suite:
    """A named collection of tasks."""

    name: str
    tasks: list[Task]
    description: str = ""
    source: str = ""  # where it was loaded from (path or builtin name)

    def select(self, limit: int | None = None, only: list[str] | None = None) -> list[Task]:
        tasks = self.tasks
        if only:
            wanted = {o.strip() for o in only if o.strip()}
            missing = wanted - {t.id for t in tasks}
            if missing:
                raise SuiteError(f"suite '{self.name}' has no task(s): {', '.join(sorted(missing))}")
            tasks = [t for t in tasks if t.id in wanted]
        if limit is not None:
            tasks = tasks[:limit]
        return tasks


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SuiteError(f"{path} is not valid JSON: {e}") from e
    except OSError as e:
        raise SuiteError(f"cannot read {path}: {e}") from e


def _parse_suite(name: str, raw: Any, source: str) -> Suite:
    if isinstance(raw, list):
        tasks_raw, description = raw, ""
    elif isinstance(raw, dict):
        tasks_raw = raw.get("tasks")
        description = str(raw.get("description") or "")
        if tasks_raw is None and "prompt" in raw:
            tasks_raw = [raw]  # single-task object
    else:
        raise SuiteError(f"suite '{name}' must be a list or an object with 'tasks'")
    if not isinstance(tasks_raw, list):
        raise SuiteError(f"suite '{name}': 'tasks' must be a list")
    tasks = [Task.from_dict(t) for t in tasks_raw]
    seen: set[str] = set()
    for t in tasks:
        if t.id in seen:
            raise SuiteError(f"suite '{name}' has duplicate task id '{t.id}'")
        seen.add(t.id)
    return Suite(name=name, tasks=tasks, description=description, source=source)


def resolve_suite(source: str) -> Suite:
    """Load a suite by builtin name (``smoke``) or by path to a JSON file."""
    if not source:
        raise SuiteError("empty suite name")
    p = Path(source)
    candidates: list[Path] = []
    if p.is_absolute() or p.exists():
        candidates.append(p)
    else:
        candidates.append(config.resolve_path(f"{BUILTIN_SUITES_DIR}/{source}.json"))
        candidates.append(config.resolve_path(f"{BUILTIN_SUITES_DIR}/{source}"))
        candidates.append(config.resolve_path(source))
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        available = list_builtin_suites()
        hint = f" Available builtin suites: {', '.join(available)}" if available else ""
        raise SuiteError(f"suite '{source}' not found (looked at: {', '.join(str(c) for c in candidates)}).{hint}")
    raw = _read_json(path)
    name = path.stem if path.suffix == ".json" else source
    if isinstance(raw, dict) and raw.get("name"):
        name = str(raw["name"])
    return _parse_suite(name, raw, source=str(path))


def list_builtin_suites() -> list[str]:
    """Names of the suites committed under ``harness/suites/``."""
    d = config.resolve_path(BUILTIN_SUITES_DIR)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def suite_info(source: str) -> dict[str, Any]:
    """Suite name, description and per-task summary without running anything."""
    suite = resolve_suite(source)
    return {
        "name": suite.name,
        "description": suite.description,
        "source": suite.source,
        "task_count": len(suite.tasks),
        "tasks": [
            {
                "id": t.id,
                "category": t.category,
                "prompt": t.prompt[:160],
                "checks": len(t.checks),
                "files": sorted(t.files),
                "setup": len(t.setup),
                "max_steps": t.max_steps,
                "timeout_s": t.timeout_s,
            }
            for t in suite.tasks
        ],
    }
