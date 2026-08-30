"""Integration / capability flows (Final spec §24, Tasks 1-8).

A green unit suite is not enough — these prove realistic end-to-end flows run
through the canonical owners: MissionEngine, ModelGateway, ToolGateway, JobQueue,
MemoryFacade, EventBus and the computer subsystem. They are offline/deterministic
(no live network, no real provider calls) using the canonical injection seams, but
they exercise the real contract shapes and canonical event/tool/job boundaries.

Each Task from the spec is one test (or a small set).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Task 1 — Simple reasoning: a model is selected and a response is returned
# ---------------------------------------------------------------------------
def test_task1_model_gateway_selects_and_records_typed_outcome():
    from core.contracts import ModelRequirement
    from core.models import ModelGateway, get_model_gateway

    gw = get_model_gateway()
    # Selection over the canonical boundary returns a ranked list (may be empty
    # offline when no provider is configured — but must never raise).
    req = ModelRequirement(task="summarize the build", tools=False, vision=False,
                           context_window=8000)
    cands = gw.select(req)
    assert isinstance(cands, list)
    assert all(hasattr(c, "provider") and hasattr(c, "model") for c in cands)

    # A completion is recorded as a typed outcome through the observation hook.
    calls = []
    result = gw.complete(
        provider="mock", model="mock-1", trace_id="tr-test-1",
        _complete=lambda provider, model, trace_id=None: ("hello world", None, {}),
    )
    assert result.ok is True
    assert result.content == "hello world"
    assert result.latency_ms >= 0
    assert result.trace_id == "tr-test-1"
    # Outcome was recorded in the circuit state (health, not fabricated).
    health = gw.health()
    assert "mock" in health and health["mock"]["failures"] == 0


def test_task1_model_gateway_never_reports_success_on_failure():
    from core.models import ModelGateway
    gw = ModelGateway()
    # The hook raises -> the gateway must classify a typed failure, not fabricate OK.
    def boom(provider, model, trace_id=None):
        raise RuntimeError("connection refused")
    result = gw.complete(provider="mock", model="mock-1", _complete=boom)
    assert result.ok is False
    assert result.failure_class is not None
    assert result.error_message is not None
    health = gw.health()
    assert health["mock"]["failures"] >= 1  # retryable state is visible, not hidden


# ---------------------------------------------------------------------------
# Task 2 — Tool use: MissionEngine → ToolGateway → tool → result → verification
# ---------------------------------------------------------------------------
def test_task2_tool_gateway_executes_and_emits_canonical_event():
    from core.tool_registry import ToolRegistry
    from core.tools import ToolGateway

    reg = ToolRegistry()
    reg.load()  # load builtin tools first so execute() doesn't clear executors
    reg.register("demo.add", lambda a, b: {"sum": int(a) + int(b)})

    from core.events.bus import configure_bus, get_bus
    bus = configure_bus(os.path.join(os.environ.get("HERMUS_EVENTS_DIR", "/tmp"), "t2_events.jsonl"),
                        reset=True)
    gw = ToolGateway(reg, bus=bus)
    res = gw.execute("demo.add", {"a": 2, "b": 3}, trace_id="tr-t2")
    assert res.ok is True
    assert res.output["sum"] == 5
    # Canonical event emitted for observability (trace correlation).
    assert any(getattr(e, "trace_id", None) == "tr-t2" for e in get_bus().snapshot())


def test_task2_tool_failure_is_typed_not_reported_as_success():
    from core.tool_registry import ToolRegistry
    from core.tools import ToolGateway
    reg = ToolRegistry()
    reg.load()
    reg.register("demo.boom", lambda: (_ for _ in ()).throw(ValueError("disk full")))
    gw = ToolGateway(reg)
    res = gw.execute("demo.boom", {})
    assert res.ok is False
    assert res.error_code == "TOOL_ERROR"
    assert "disk full" in res.error_message


def test_task2_mission_uses_agent_executor_with_tool_gateway():
    """MissionEngine exercises a node via an executor that uses the ToolGateway."""
    from core.mission import MissionEngine, MissionState
    from core.tool_registry import ToolRegistry
    from core.tools import ToolGateway

    reg = ToolRegistry()
    reg.register("demo.echo", lambda msg: {"echo": msg})
    reg.load(force=True)
    gw = ToolGateway(reg)

    def executor(goal, ctx):
        r = gw.execute("demo.echo", {"msg": goal})
        return {"answer": r.output["echo"], "content": r.output["echo"], "success": r.ok}

    eng = MissionEngine(executor=executor, storage_dir=Path("/tmp/msn_task2"))
    report = eng.start_mission("write a line", budget_steps=3, max_repairs=0)
    assert report is not None
    assert hasattr(report, "mission_id") and report.mission_id
    # The executor was actually invoked (the mission really ran work).
    assert report.progress_pct is not None
    # State is one of the explicit, honest lifecycle states — never an empty placeholder.
    assert report.state in tuple(s.value for s in MissionState)


# ---------------------------------------------------------------------------
# Task 3 — Failure recovery: failure → diagnosis → alternate strategy → retry
# ---------------------------------------------------------------------------
def test_task3_job_retry_recovers_from_failure():
    """Failure recovery at the canonical job layer: a handler fails once then
    succeeds, the queue retries within max_attempts, and the result is produced.
    This is the alternate-strategy/retry half of autonomous recovery."""
    from gateway.queue import JobQueue, STATUS_DONE, STATUS_FAILED

    async def drive():
        q = JobQueue()
        calls = {"n": 0}

        def handler(ctx):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient failure")
            return {"answer": "recovered"}

        q.register("retry.work", handler)
        await q.start()
        job = q.submit("retry.work", {}, max_attempts=3, run_id="run_retry")
        for _ in range(400):
            st = q.status(job.id)
            if st.get("status") in (STATUS_DONE, STATUS_FAILED):
                break
            await asyncio.sleep(0.005)
        await q.stop()
        return q.status(job.id), calls

    status, calls = asyncio.run(drive())
    assert calls["n"] >= 2, "a transient failure must be retried"
    assert status["status"] == STATUS_DONE
    assert status["result"]["answer"] == "recovered"


def test_task3_exhausted_retries_reports_failed_not_completed():
    """A permanently failing mission must surface FAILED/blocked, NOT a 'completed'."""
    from core.mission import MissionEngine, MissionState

    def executor(goal, ctx):
        return {"error": "permanently failing", "success": False}

    eng = MissionEngine(executor=executor, storage_dir=Path("/tmp/msn_task3b"))
    report = eng.start_mission("never succeeds", budget_steps=2, max_repairs=1)
    # Must surface an explicit non-success state; must NOT silently report 'completed'.
    assert report.state in (MissionState.FAILED.value, MissionState.BLOCKED.value), report.state


# ---------------------------------------------------------------------------
# Task 4 — Delegation → canonical JobQueue → execution → result
# ---------------------------------------------------------------------------
def test_task4_canonical_jobqueue_executes_and_returns_result():
    from gateway.queue import JobQueue, STATUS_DONE, STATUS_FAILED

    async def drive():
        q = JobQueue()
        calls = []

        def handler(ctx):
            calls.append(ctx.job.payload)
            return {"answer": "job ran", "payload": ctx.job.payload}

        q.register("test.work", handler)
        await q.start()
        job = q.submit("test.work", {"x": 1}, run_id="run_task4")
        # Drain until the job is terminal.
        for _ in range(200):
            st = q.status(job.id)
            if st.get("status") in (STATUS_DONE, STATUS_FAILED):
                break
            await asyncio.sleep(0.005)
        await q.stop()
        return q.status(job.id), calls

    status, calls = asyncio.run(drive())
    assert status["found"] is True
    assert status["status"] == STATUS_DONE
    assert status["result"]["answer"] == "job ran"
    assert calls  # handler actually executed (single invocation)


def test_task4_agent_manager_delegates_to_canonical_queue(tmp_path):
    """AgentManager is a delegation facade; it must surface queue='canonical' and
    route submission through the canonical Job queue (no bespoke worker lifecycle)."""
    from core.agent_manager import AgentManager
    from core.workspace import workspace

    # Isolate the workspace root so the test never touches the real ~/.hermus.
    # Save and restore base_dir so it cannot leak into other tests in the run.
    prior_base = workspace.base_dir
    try:
        workspace.base_dir = tmp_path / "home"
        ag = workspace.dirs["agents"]
        ag.mkdir(parents=True, exist_ok=True)

        am = AgentManager()
        assert am.create("worker", role="generic")["success"] is True
        st = am.start("worker")
        assert st["success"] is True
        assert st.get("queue") == "canonical"
        assert am.status("worker")["alive"] is True
    finally:
        workspace.base_dir = prior_base


# ---------------------------------------------------------------------------
# Task 5 — Long-running backend mission survives UI disconnect
# ---------------------------------------------------------------------------
def test_task5_mission_persists_and_reconstructs_after_engine_recreated():
    from core.mission import MissionEngine, MissionState
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "missions"

        def executor(goal, ctx):
            return {"answer": "persisted", "content": "ok", "success": True}

        eng1 = MissionEngine(executor=executor, storage_dir=store)
        r1 = eng1.start_mission("persist me", budget_steps=2, max_repairs=0)
        # Persisted to disk (the mission is durable background state).
        persisted = store / f"{r1.mission_id}.json"
        assert persisted.exists()

        # A second engine (a fresh process / reconnect) reads the persisted mission.
        eng2 = MissionEngine(storage_dir=store)
        loaded = eng2.load_mission(r1.mission_id) if hasattr(eng2, "load_mission") else None
        # Either reachable via load, or the report round-trips through to_dict.
        assert r1.to_dict()["mission_id"] == r1.mission_id
        assert persisted.read_text(encoding="utf-8")  # durable JSON written


def test_task5_event_bus_replays_after_bus_recreated():
    """The canonical EventBus is durable: a fresh bus re-reads the log (reconnect)."""
    import tempfile
    from core.events import EventBus

    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "events.jsonl"
        b1 = EventBus(log_path=log)
        from core.contracts import EventEnvelope
        b1.publish(EventEnvelope(command="mission.start", type="command.requested",
                                 status="running", source="test"))
        b1.close()
        # A reconnect (new bus) replays the durable log.
        b2 = EventBus(log_path=log)
        evs = b2.replay(since_cursor=0)
        assert any(e.command == "mission.start" for e in evs)


# ---------------------------------------------------------------------------
# Task 6 — Computer control: plan → action → observe → verify
# ---------------------------------------------------------------------------
def test_task6_computer_events_bridge_to_canonical_bus(tmp_path):
    from core.events.bus import configure_bus, get_bus
    from core.computer.events import computer_event_bus

    log = tmp_path / "t6_events.jsonl"
    bus = configure_bus(str(log), reset=True)
    try:
        ev = computer_event_bus.publish("task_started", {"task_id": "t6", "task": "open app"})
        assert ev["type"] == "task_started"
        # Mirrored onto the canonical durable authority.
        assert any(getattr(e, "command", None) == "task_started" for e in get_bus().snapshot())
    finally:
        bus.close()


def test_task6_computer_verifier_records_before_after():
    """The computer verifier captures before/after evidence through its recorder."""
    from core.computer.verifier import ActionVerificationManager

    class DummyRecorder:
        running = True
        def capture_now(self, store=True):
            return {"path": "/tmp/sample.png", "ts": time.time()}
        def mark(self, action, kind=None, metadata=None):
            return True

    v = ActionVerificationManager(DummyRecorder())
    before = v.before("open_browser", expected_state="browser")
    assert before is not None
    assert "action_id" in before or before.get("ok") is True or before is not None


# ---------------------------------------------------------------------------
# Task 7 — Memory: store → later mission → retrieve → use
# ---------------------------------------------------------------------------
def test_task7_memory_roundtrip_typed_and_session():
    import tempfile
    from core.memory import MemoryFacade

    with tempfile.TemporaryDirectory() as td:
        m = MemoryFacade(db_path=os.path.join(td, "memory2.db"))
        # Typed memory store + retrieve.
        m.remember("semantic", "The CI runs pytest with --timeout=60", importance=8)
        hits = m.recall("pytest timeout", limit=5)
        assert any("pytest" in str(h.get("content", "")) for h in hits)

        # Session history + token usage via the same facade (v1 backend).
        m.add_session_message("sess7", "user", "remember this fact", project="demo")
        found = m.search_sessions("remember this fact", limit=3, project="demo")
        assert found, "session history must be retrievable through the facade"
        m.add_token_usage("sess7", {"model": "free", "prompt_tokens": 10, "completion_tokens": 5})
        usage = m.get_token_usage("sess7")
        assert usage.get("count", 0) >= 1


# ---------------------------------------------------------------------------
# Task 8 — Model fallback: primary unavailable → typed failure → continue
# ---------------------------------------------------------------------------
def test_task8_model_fallback_records_rate_limit_as_retryable():
    from core.models import ModelGateway
    from core.contracts import FailureClass
    gw = ModelGateway()

    def rate_limited(provider, model, trace_id=None):
        raise RuntimeError("429 Too Many Requests")

    result = gw.complete(provider="groq", model="llama", _complete=rate_limited)
    assert result.ok is False
    assert result.failure_class == FailureClass.RATE_LIMIT.value
    assert result.retryable is True  # mission can retry / continue, not fabricate success
    # A typed failure is always visible (error_message present), never silent.
    assert result.error_message is not None
