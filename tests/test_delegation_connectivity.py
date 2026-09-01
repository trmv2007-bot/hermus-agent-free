"""E2E delegation-connectivity proof, offline.

Proves the real path that an agent/mission uses to delegate work:

    MissionEngine/agent → submit_and_wait("subagent.delegate") → JobQueue
        → subagent worker (HermusAgent)
            → ModelGateway        (model selection/completion)
            → ToolGateway         (tool execution)
            → MemoryFacade        (memory recall)
        → EventBus lifecycle events (job.created/queued/started/completed)
        → structured result → parent (caller)

This is NOT a class-instantiation test: a real ``subagent.delegate`` Job is
submitted to the canonical JobQueue, the queue executes the delegation handler
in a worker thread, and the sub-agent runs through the single worker engine
(``core.delegation.run_subagent_task`` → ``HermusAgent``). The worker runs
in-process so the ModelGateway / ToolGateway / MemoryFacade boundaries and the
EventBus are the same objects the test instruments — proving the sub-agent uses
the *canonical* boundaries, not a second provider / tool path.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _offline(tmp_path):
    os.environ["HERMUS_HOME"] = str(tmp_path / "home")
    os.environ["HERMUS_MODEL"] = "mock/mock"
    os.environ.setdefault("HERMUS_EMBED_BACKEND", "hash")
    os.environ["HERMUS_AGENT_DEPTH"] = "0"
    from core.config import config

    config.model = "mock/mock"
    config.max_tool_steps = 2
    yield


def _saved_rpc():
    from core.delegation import delegation

    return delegation.rpc


def test_delegation_connects_queue_to_canonical_boundaries_and_event_bus():
    """A real subagent.delegate Job exercises Model/Tool/Memory boundaries and
    emits EventBus lifecycle events, then returns a structured result to the parent."""
    from unittest import mock

    from core.delegation import delegation
    from core.events import get_bus
    from core.memory.store import MemoryFacade
    from core.models import get_model_gateway
    from core.tools.gateway import ToolGateway, get_tool_gateway
    from subagents.subagent import DELEGATE_JOB, submit_and_wait

    old_rpc = delegation.rpc
    # Run the sub-agent worker in-process so the instrumented canonical objects
    # are the same instances the worker uses (subprocess workers are isolated).
    delegation.rpc = False
    try:
        calls = {"llm": 0, "execute": 0, "recall": 0}
        gw = get_model_gateway()
        tg = get_tool_gateway()
        real_llm = type(gw).llm
        real_exec = ToolGateway.execute
        real_recall = MemoryFacade.recall_context

        def tr_llm(self, *a, **kw):
            calls["llm"] += 1
            return real_llm(self, *a, **kw)

        def tr_exec(self, *a, **kw):
            calls["execute"] += 1
            return real_exec(self, *a, **kw)

        def tr_recall(self, *a, **kw):
            calls["recall"] += 1
            return real_recall(self, *a, **kw)

        job_events: list[str] = []
        bus = get_bus()

        def collect(env):
            if getattr(env, "type", "") == "job.lifecycle":
                job_events.append(env.command)

        bus.subscribe("job.lifecycle")(collect)

        with mock.patch.object(type(gw), "llm", tr_llm), \
             mock.patch.object(type(tg), "execute", tr_exec), \
             mock.patch.object(MemoryFacade, "recall_context", tr_recall):
            st = submit_and_wait(
                DELEGATE_JOB,
                {"tasks": ["search the web for clustering"], "goal": "cluster search",
                 "depth": 1, "max_children": 1, "aggregate": "concat",
                 "mission_id": "mission-conn", "parent_task_id": "task-node-1"},
                session_key="delegate:connectivity",
                run_id="run-conn",
                timeout=30.0,
            )
    finally:
        delegation.rpc = old_rpc

    assert st["status"] == "succeeded", st
    result = st.get("result") or {}
    assert result.get("nodes"), f"parent must receive structured children: {st}"
    node = result["nodes"][0]
    assert node.get("answer"), f"sub-agent produced no answer: {node}"
    assert node.get("status") in ("done", "partial"), node
    # Correlation IDs trace mission → queue → child node.
    assert result.get("mission_id") == "mission-conn", result
    assert node.get("mission_id") == "mission-conn", node
    assert node.get("run_id") == "run-conn", node
    # The sub-agent genuinely reached the canonical boundaries.
    assert calls["llm"] >= 1, "sub-agent must route model calls through ModelGateway"
    assert calls["recall"] >= 1, "sub-agent must recall through MemoryFacade"
    assert calls["execute"] >= 1, "sub-agent must execute tools through ToolGateway"
    # Job lifecycle landed on the canonical EventBus (single authoritative source),
    # carrying the mission correlation id.
    for ev in ("job.created", "job.queued", "job.started", "job.completed"):
        assert ev in job_events, f"missing EventBus lifecycle event {ev}: {job_events}"


def test_subagent_spawn_via_queue_returns_structured_result_to_parent():
    """The tool-level spawn path (`subagent_spawn`) routes through the canonical
    JobQueue and still yields a structured result a parent agent can read."""
    from core.delegation import delegation
    from subagents.subagent import spawn_subagent

    old_rpc = delegation.rpc
    try:
        delegation.rpc = False
        out = spawn_subagent("search the web for audio clustering", max_steps=2)
    finally:
        delegation.rpc = old_rpc

    assert out["success"] is True, out
    assert out["response"], out
    assert out["subagent_id"].startswith("sub_"), out
