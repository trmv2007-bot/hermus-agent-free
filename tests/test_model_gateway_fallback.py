"""ModelGateway real retry/fallback + streaming error classification (§4.2, §4.3).

These inject a *deterministic* resolver/capabilities/router + ``llm_builder`` (the
documented test seam) so the gateway's actual execute-and-fallback loop is exercised:
provider A fails -> classify -> retry -> select provider B -> response returned. They
prove the retry/fallback *logic*; they are NOT a live-provider test (that is NOT VERIFIED).
"""
from __future__ import annotations

import time

import pytest

from core.contracts import FailureClass, ModelGatewayResult, ModelRequirement
from core.models.gateway import ModelGateway, ModelGatewayError


class _FakeResp:
    def __init__(self, content, tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}


class _FakeLLM:
    """Deterministic fake completion client. ``mode`` controls behavior:
    - 'ok' returns a response; 'fail' raises; 'fail_then_ok' fails first N then ok."""
    def __init__(self, model, provider, mode="ok", fail_times=0, key="provider/model"):
        self.provider = provider
        self.model_name = model
        self.mode = mode
        self.fail_times = fail_times
        self.calls = 0
        self.key = key

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.mode == "fail":
            raise RuntimeError(f"429 Too Many Requests from {self.key}")
        if self.mode == "auth":
            raise RuntimeError(f"401 Unauthorized {self.key}")
        if self.mode == "fail_then_ok" and self.calls <= self.fail_times:
            raise RuntimeError("network timeout")
        return _FakeResp(f"response from {self.key}", tool_calls=[])


class _FakeResolver:
    def __init__(self, bundles):
        self.bundles = bundles
    def select_usable_bundle(self, require_tools=False, prefer=None):
        return self.bundles[0] if self.bundles else None
    def list_available_providers(self, probe=False):
        return [dict(b, provider=b.get("provider")) for b in self.bundles]
    def discover_runtime_bundles(self, include_local=True):
        return self.bundles


class _FakeCaps:
    def __init__(self):
        self.store = {}
    def negotiate(self, model):
        return {"capabilities": ["tools"], "tools": True, "vision": False,
                "reasoning": False, "context_window": 8000}


class _FakeRouter:
    class ModelRouter:
        def score(self, task):
            return type("Scored", (), {"provider": "", "model": "", "score": 0.0})()


def _gateway(bundles, llm_factory):
    return ModelGateway(
        resolver=_FakeResolver(bundles),
        capabilities=_FakeCaps(),
        router=_FakeRouter(),
        llm_builder=llm_factory,
    )


def test_chat_with_fallback_selects_provider_b_when_a_unavailable():
    """§4.2: provider A unavailable -> classified -> provider B selected -> response."""
    bundles = [
        {"provider": "bad", "default_model": "bad-model", "free": True},
        {"provider": "good", "default_model": "good-model", "free": True},
    ]
    made = {}
    def llm_factory(model=None, provider=None, api_key=None, base_url=None, temperature=None):
        mode = "fail" if provider == "bad" else "ok"
        obj = _FakeLLM(model, provider, mode=mode, key=f"{provider}/{model}")
        made[provider] = obj
        return obj

    gw = _gateway(bundles, llm_factory)
    resp = gw.chat_with_fallback(
        [{"role": "user", "content": "hi"}],
        req=ModelRequirement(task="chat", tools=False),
    )
    assert resp.content == "response from good/good-model"
    # The bad provider was actually attempted and classified; the good one completed.
    assert "bad" in made and made["bad"].calls >= 1
    assert made["good"].calls == 1
    # Health state records the bad provider's failure (visible, not hidden).
    health = gw.health()
    assert health.get("bad", {}).get("failures", 0) >= 1
    assert health.get("good", {}).get("failures", 0) == 0


def test_chat_with_fallback_retries_transient_then_succeeds():
    """§4.2: a retryable failure is retried within the SAME provider before falling back."""
    bundles = [{"provider": "p", "default_model": "m", "free": True}]
    def llm_factory(model=None, provider=None, **kw):
        return _FakeLLM(model, provider, mode="fail_then_ok", fail_times=1,
                        key=f"{provider}/{model}")
    gw = _gateway(bundles, llm_factory)
    resp = gw.chat_with_fallback([{"role": "user", "content": "hi"}],
                                 req=ModelRequirement(task="chat"), max_retries=3)
    assert resp.content == "response from p/m"


def test_chat_with_fallback_all_attempts_raise_typed_error():
    """§4.2: when every provider fails, a single typed ModelGatewayError surfaces."""
    bundles = [
        {"provider": "a", "default_model": "a", "free": True},
        {"provider": "b", "default_model": "b", "free": True},
    ]
    def llm_factory(model=None, provider=None, **kw):
        return _FakeLLM(model, provider, mode="fail", key=f"{provider}/{model}")
    gw = _gateway(bundles, llm_factory)
    with pytest.raises(ModelGatewayError) as exc:
        gw.chat_with_fallback([{"role": "user", "content": "hi"}],
                              req=ModelRequirement(task="chat"))
    # The error is structured: failure_class set and the message lists attempts.
    assert exc.value.failure_class in (FailureClass.RATE_LIMIT.value,)
    assert "attempt" in str(exc.value)


def test_stream_error_during_iteration_is_classified():
    """§4.3: a provider error raised WHILE iterating the stream is a typed gateway error,
    not a raw exception from the generator."""
    class _FailingStream:
        def stream_chat(self, messages, tools=None, on_delta=None):
            def gen():
                yield "hello"
                raise RuntimeError("connection reset during stream")
            return gen()
        provider = "bad"
        model_name = "m"
    def llm_factory(model=None, provider=None, **kw):
        return _FailingStream()
    gw = _gateway([], llm_factory)  # resolver irrelevant; stream takes explicit provider
    gen = gw.stream([{"role": "user", "content": "hi"}], model="m", provider="bad")
    got = []
    with pytest.raises(ModelGatewayError) as exc:
        for chunk in gen:
            got.append(chunk)
    assert got == ["hello"]
    assert exc.value.failure_class == FailureClass.NETWORK.value
