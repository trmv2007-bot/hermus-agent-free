"""Computer Control Benchmark — measure Hermus's real ability on desktop tasks.

Provides:
- 30 curated computer tasks across 7 categories
- A benchmark runner that executes tasks and records metrics
- Per-task and aggregate scoring
- Integration with the episode store for historical comparison
"""
from .tasks import COMPUTER_TASKS, get_task, list_tasks, TaskSpec, get_categories
from .runner import BenchmarkRunner, BenchmarkResult, run_benchmark

__all__ = [
    "COMPUTER_TASKS",
    "TaskSpec",
    "get_task",
    "list_tasks",
    "BenchmarkRunner",
    "BenchmarkResult",
    "run_benchmark",
]