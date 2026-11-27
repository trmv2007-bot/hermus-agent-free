"""Bounds on a turn: every path that used to wait forever now ends and says so.

Three measured hangs on this machine drove these (2026-11-27):

* a council member's model call blocked in ``requests`` while
  ``CouncilSession.proposals`` waited on ``as_completed`` with no deadline, so
  ``run()`` never returned;
* the inline ``POST /command`` awaited the whole turn with no deadline at all;
* ``HermusAgent._backend_available`` called Ollama "available" whenever the
  server answered, even for a model that was never pulled, so a mission spent
  minutes walking 404s instead of reporting the honest blocker.

Offline: every model here is a stub.
"""

from __future__ import annotations

import time

import pytest


# --------------------------------------------------------------- council budget
class _SlowLLM:
    """A member whose model never answers in time."""

    def __init__(self, delay: float = 2.0):
        self.delay = delay

    def chat(self, messages, tools=None):
        time.sleep(self.delay)

        class _Resp:
            content = "late"
            tool_calls = []

        return _Resp()


def test_council_budget_ends_a_session_whose_members_stall(monkeypatch):
    from core.counsel.council import CouncilSession

    session = CouncilSession(
        "Research the best free password manager and outline a migration plan.",
        model="mock/mock",
        difficulty=4,
        max_members=3,
        max_rounds=2,
        execute=False,
        timeout_seconds=0.6,
    )
    monkeypatch.setattr(CouncilSession, "_member_llm", lambda self, member: _SlowLLM())

    started = time.monotonic()
    summary = session.run()
    elapsed = time.monotonic() - started

    assert summary["timed_out"] is True, summary
    assert elapsed < 3.0, f"a 0.6s budget must not hold the turn for {elapsed:.1f}s"
    # It still owes the person an answer, made from what it has.
    assert summary["final_answer"]
    assert any("budget" in t["content"].lower() for t in session.transcript)


def test_council_budget_does_not_shorten_a_healthy_session():
    from core.counsel.council import CouncilSession

    session = CouncilSession(
        "List two python libraries for web scraping.",
        model="mock/mock",
        difficulty=3,
        max_members=3,
        max_rounds=2,
        execute=False,
        timeout_seconds=60,
    )
    summary = session.run()
    assert summary["timed_out"] is False
    assert summary["final_answer"]


# ------------------------------------------------------------ inline /command
def test_inline_command_returns_an_explicit_timeout(monkeypatch):
    """A turn that outlives its budget answers 504 with a reason, not silence."""
    from starlette.testclient import TestClient

    import core.runtime as runtime
    from core.config import config
    from gateway.gateway import app

    def _never_returns(*a, **kw):
        time.sleep(3)
        return {"response": "too late"}

    monkeypatch.setattr(runtime, "execute", _never_returns)
    monkeypatch.setattr(config, "gateway_queue_timeout", 0.5)

    with TestClient(app) as client:
        resp = client.post("/command", json={"platform": "cli", "user_id": "bound", "text": "hello"})

    assert resp.status_code == 504, resp.text
    body = resp.json()
    assert body["timeout"] is True
    assert "budget" in body["error"]
    assert body["run_id"]


# ------------------------------------------------------- honest backend check
def test_ollama_serves_normalises_bare_names():
    from core.agent import _ollama_serves

    tags = {"llama3.1:latest", "lfm2.5:8b", "sparkllm/spark-x2.6-4b:q4_k_m"}
    assert _ollama_serves(tags, "llama3.1")
    assert _ollama_serves(tags, "Llama3.1:latest")
    assert _ollama_serves(tags, "lfm2.5:8b")
    assert _ollama_serves(tags, "SparkLLM/Spark-X2.6-4B:q4_k_m")
    # A namespace that was stripped, or a model never pulled, is not servable.
    assert not _ollama_serves(tags, "Spark-X2.6-4B:q4_k_m")
    assert not _ollama_serves(tags, "llama3.1:8b")


def test_backend_unavailable_when_the_model_was_never_pulled(monkeypatch):
    import requests

    from core import agent as agent_mod
    from core.config import config

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"models": [{"name": "lfm2.5:8b"}, {"name": "moondream:latest"}]}

    class _LLM:
        provider = "ollama"
        model_name = "llama3.1:8b"

    class _Agent:
        llm = _LLM()

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(config, "ollama_base_url", "http://ollama.test")

    agent = _Agent()
    assert agent_mod.HermusAgent._backend_available(agent) is False
    # The refusal says which model is missing and how to fix it.
    assert "llama3.1:8b" in agent._backend_detail
    assert "ollama pull llama3.1:8b" in agent._backend_detail

    _LLM.model_name = "lfm2.5:8b"
    assert agent_mod.HermusAgent._backend_available(_Agent()) is True


def test_namespaced_model_names_survive_the_provider_split():
    from core.llm import FreeLLM

    namespaced = FreeLLM(model="SparkLLM/Spark-X2.6-4B:q4_k_m", provider="ollama")
    assert namespaced.model_name == "SparkLLM/Spark-X2.6-4B:q4_k_m"

    prefixed = FreeLLM(model="ollama/llama3.1:8b", provider="ollama")
    assert prefixed.model_name == "llama3.1:8b"


@pytest.mark.parametrize("requested", ["mock/mock"])
def test_council_honours_an_explicit_model(requested):
    """An explicit model is the caller's choice, not a suggestion."""
    from core.counsel.council import CouncilSession

    session = CouncilSession("Plan a two-step task.", model=requested, difficulty=3, max_members=3, max_rounds=1, execute=False)
    session.convene()
    assert session.members
    assert {m.model for m in session.members} == {requested}
