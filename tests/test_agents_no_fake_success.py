"""core/agents must report failure honestly (audit 2026-09, Batch 3).

``run_task`` used to sleep 0.5s and return ``"Completed: <task>"``, and
``_handle_collaboration`` replied ``"Collaborating on: <msg>"`` — both claimed
work that never happened, and ``run_task`` is reachable from
``POST /api/v1/agents/{agent_id}/task``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agents.agent import Agent


class _Resp:
    def __init__(self, content):
        self.content = content


class _Gateway:
    def __init__(self, content=None, boom=None):
        self._content = content
        self._boom = boom

    def llm(self, **kwargs):
        if self._boom:
            raise self._boom

        outer = self

        class _LLM:
            def chat(self, wire):
                return _Resp(outer._content)

        return _LLM()


def _stub_gateway(monkeypatch, gateway):
    import core.models as models

    monkeypatch.setattr(models, "get_model_gateway", lambda: gateway, raising=False)


@pytest.mark.asyncio
async def test_run_task_returns_the_real_model_answer(monkeypatch):
    _stub_gateway(monkeypatch, _Gateway(content="the actual answer"))
    agent = Agent(name="Honest")
    out = await agent.run_task("what is 2+2?")
    assert out == "the actual answer"
    assert agent.stats.tasks_completed == 1
    assert agent.stats.tasks_failed == 0


@pytest.mark.asyncio
async def test_run_task_does_not_claim_success_on_mock_fallback(monkeypatch):
    """core.llm emits this text when no provider is reachable — not a result."""
    _stub_gateway(monkeypatch, _Gateway(content="Fallback mock for: what is 2+2?"))
    agent = Agent(name="Honest2")
    out = await agent.run_task("what is 2+2?")
    assert "Task failed" in out
    assert not out.startswith("Completed:")
    assert agent.stats.tasks_completed == 0
    assert agent.stats.tasks_failed == 1


@pytest.mark.asyncio
async def test_run_task_reports_a_provider_error_honestly(monkeypatch):
    _stub_gateway(monkeypatch, _Gateway(boom=RuntimeError("connection refused")))
    agent = Agent(name="Honest3")
    out = await agent.run_task("anything")
    assert "Task failed" in out and "connection refused" in out
    assert agent.stats.tasks_completed == 0


@pytest.mark.asyncio
async def test_collaboration_declines_instead_of_pretending(monkeypatch):
    agent = Agent(name="Honest4")
    sent: list[str] = []

    async def fake_reply(target, content, **kwargs):
        sent.append(content)
        return True

    monkeypatch.setattr(agent, "reply", fake_reply)
    await agent._handle_collaboration({"content": "merge our findings", "sender_id": "peer-1"})
    assert sent and "not implemented" in sent[0]


@pytest.mark.asyncio
async def test_specialized_agent_factories_construct():
    """core/agents/specialization.py used to call uuid.uuid4() and json.dumps()
    without importing either, so every factory raised NameError. ruff's F821
    found it; nothing else did."""
    from core.agents.specialization import create_coder, create_researcher

    researcher = await create_researcher(name="R1")
    coder = await create_coder(name="C1")
    assert researcher.config.name == "R1"
    assert coder.config.name == "C1"
