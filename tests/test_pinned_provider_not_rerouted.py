"""An explicitly pinned provider must not be rerouted to the local tier.

Proven bug: the council seats members across providers on purpose - local AND
hosted API - to get a genuinely diverse panel. Every member goes through
FreeLLM._apply_two_tier -> ModelRouter.route, which reroutes anything short to
local. So the API seat was never used: a "diverse" council was N copies of the
same small local model, and it reported success while doing it.

Live evidence before the fix:
    provider AFTER : ollama
    last_route    : reason='short_turn_saves_quota', switched=True

These tests pin the rule: quota heuristics govern the default path only.
"""

from __future__ import annotations

from core.llm import FreeLLM

SHORT = [{"role": "user", "content": "say 42"}]


def test_default_path_is_still_routed(monkeypatch) -> None:
    """The fix must not disable routing for ordinary calls."""
    import core.model_router as mr

    llm = FreeLLM()  # no provider/base_url/api_key -> default path
    assert llm._explicit_provider is False

    called = {}

    class FakeRouter:
        def route(self, messages, has_tools=False, current=None):
            called["yes"] = True
            from core.model_router import RouteDecision

            return RouteDecision(
                provider="ollama",
                model_name="spark-x2.5-4b-q4",
                reason="short_turn_saves_quota",
                switched=True,
                using_fallback=False,
            )

    monkeypatch.setattr(mr, "get_router", lambda: FakeRouter())
    llm._apply_two_tier(SHORT)
    assert called.get("yes"), "the default path must still consult the router"
    assert llm.provider == "ollama"


def test_pinned_provider_is_not_rerouted(monkeypatch) -> None:
    import core.model_router as mr

    def explode():
        raise AssertionError("router must not be consulted for a pinned seat")

    monkeypatch.setattr(mr, "get_router", explode)
    llm = FreeLLM(provider="nous", model="nous/stealth/space-bunny-alpha", base_url="https://example.invalid/v1")
    assert llm._explicit_provider is True

    llm._apply_two_tier(SHORT)
    assert llm.provider == "nous", "a pinned seat must keep its provider"
    assert llm.model_name == "stealth/space-bunny-alpha"
    assert llm.last_route is None, "not-routed must be distinguishable from routed-and-switched"


def test_pinned_by_api_key_alone_still_counts() -> None:
    llm = FreeLLM(api_key="sk-test")
    assert llm._explicit_provider is True


def test_pinned_by_base_url_alone_still_counts() -> None:
    llm = FreeLLM(base_url="https://example.invalid/v1")
    assert llm._explicit_provider is True


def test_default_construction_is_not_pinned() -> None:
    llm = FreeLLM()
    assert llm._explicit_provider is False
