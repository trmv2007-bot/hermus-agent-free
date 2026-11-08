"""Hermus agent harness.

Two layers live here:

* **Runtime primitives** (``bus``, ``files``, ``sessions``, compaction,
  cascade memory, swarm) — jcode-inspired machinery the ReAct loop uses each
  turn so the loop itself can stay small. Session ≠ window.
* **Eval harness** (``tasks``, ``verify``, ``runner``, ``report``, ``models``) —
  the outer layer that makes HERMUS usable *as* a harness: define task suites,
  run the agent against each task in an isolated workspace, verify the result,
  and produce JSON/markdown reports with history and A/B comparison. Any model
  the runtime can see — API-key/auth hosted providers or local runtimes — can
  be the agent under test via a flexible ``--model`` spec.
  CLI: ``hermus harness run <suite>`` (see ``hermus_cli/g_runtime.py``).
"""

from . import bus, files, sessions
from .compaction import compact_messages
from .memory_graph import cascade_recall
from .models import MOCK_REF, list_models, ping_model, resolve_model_spec
from .runner import HarnessRunner, RunOptions, default_agent_factory
from .runtime import HarnessRuntime, governed_call, governed_turn, harness
from .swarm import run_workers, spawn
from .swarm import status as swarm_status
from .tasks import BUILTIN_SUITES_DIR, Suite, SuiteError, Task, list_builtin_suites, resolve_suite, suite_info
from .verify import verify_task

__all__ = [
    "bus",
    "files",
    "sessions",
    "compact_messages",
    "cascade_recall",
    "HarnessRuntime",
    "harness",
    "governed_turn",
    "governed_call",
    "spawn",
    "run_workers",
    "swarm_status",
    # eval harness
    "BUILTIN_SUITES_DIR",
    "Suite",
    "SuiteError",
    "Task",
    "list_builtin_suites",
    "resolve_suite",
    "suite_info",
    "verify_task",
    "MOCK_REF",
    "list_models",
    "ping_model",
    "resolve_model_spec",
    "HarnessRunner",
    "RunOptions",
    "default_agent_factory",
]
