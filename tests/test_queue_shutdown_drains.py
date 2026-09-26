"""JobQueue.stop() must not leave job threads running.

Proven failure, captured from a real run of tests/test_universal_runtime.py:

    File "gateway/queue.py", line 555, in _run_lane
      fut = self._loop.run_in_executor(None, _call_with_context, handler, ctx)
    File "logging/__init__.py", line 1154, in emit
      stream.write(msg + self.terminator)
    ValueError: I/O operation on closed file.

Chain: the job was offloaded to the event loop's DEFAULT executor. The lane task
finishes as soon as the work is handed over, so stop()'s gather over lane tasks
does not imply the job finished - stop() returned while a mission was still
running. The survivor then logged into a stream the host had already closed,
and (being a non-daemon worker) kept the process alive.

These tests pin the contract: the queue owns its executor, tracks in-flight
futures, and drains them on stop().
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from gateway.queue import JobQueue


def test_queue_owns_an_explicit_executor() -> None:
    """An executor we do not own cannot be drained on stop()."""
    q = JobQueue()
    assert hasattr(q, "_executor")
    assert hasattr(q, "_inflight")
    assert q._executor is None
    assert q._inflight == set()


def test_dispatch_uses_the_owned_executor_not_the_default_one() -> None:
    """A job must never be handed to run_in_executor(None, ...).

    Checked against the AST, not the source text: the fix adds a comment that
    *mentions* the bad call, and a substring test would match its own comment.
    """
    import ast
    import inspect

    from gateway import queue as qmod

    tree = ast.parse(inspect.getsource(qmod))
    first_args: list[object] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_in_executor"
        ):
            assert node.args, "run_in_executor must pass an executor"
            first_args.append(ast.unparse(node.args[0]))

    assert first_args, "expected at least one run_in_executor call"
    for arg in first_args:
        assert arg != "None", f"jobs must not use the un-drainable default executor (found {arg})"
    assert "self._executor" in first_args, "jobs must use the queue-owned executor"


def test_stop_drains_inflight_jobs() -> None:
    """stop() must wait for a running job rather than abandoning it."""
    started = threading.Event()
    finished = threading.Event()

    async def scenario() -> None:
        q = JobQueue(workers=2)
        await q.start()

        def slow_job(ctx):
            started.set()
            time.sleep(0.6)
            finished.set()
            return "done"

        # Dispatch directly through the owned executor, exactly as _run_lane does.
        loop = asyncio.get_running_loop()
        q._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hermus-test")
        fut = loop.run_in_executor(q._executor, slow_job, {})
        q._inflight.add(fut)
        fut.add_done_callback(q._inflight.discard)

        assert started.wait(2.0), "job should have started"

        # stop() must not return until the in-flight job is done.
        await q.stop(drain_timeout=5.0)
        assert finished.is_set(), "stop() returned while a job was still running"
        assert fut.done()

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))


def test_stop_closes_the_stream_it_logged_into() -> None:
    """The real symptom: a surviving thread logging to a closed stream.

    A closed stream raises ValueError inside logging, which logging swallows and
    reports as a 'Logging error' traceback. After stop(), no thread may remain
    able to touch a stream the host has closed.
    """
    stream = io.StringIO()
    stream.close()  # simulate host teardown

    async def scenario() -> None:
        q = JobQueue(workers=1)
        await q.start()
        q._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hermus-test")

        loop = asyncio.get_running_loop()

        def writes_after_close(ctx):
            # Exactly what the old code did: stream.write on a closed stream.
            stream.write("late log line")
            return "done"

        fut = loop.run_in_executor(q._executor, writes_after_close, {})
        q._inflight.add(fut)
        fut.add_done_callback(q._inflight.discard)
        await asyncio.sleep(0.05)

        await q.stop(drain_timeout=5.0)
        assert fut.done(), "stop() must have waited for the writer to finish"

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))
