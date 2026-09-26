"""Behavioural tests for the two-tier model policy.

These assert routing decisions, not implementation shape: a routing table is
only trustworthy if the *decision* is right for each input.
"""

from __future__ import annotations

import time

import pytest

from core.model_router import (
    REASON_FALLBACK,
    REASON_MAIN,
    REASON_NO_LOCAL,
    REASON_SAME,
    REASON_SHORT,
    REASON_TOOLS,
    ModelRouter,
    split_model_ref,
)

MAIN = "nous/stealth/space-bunny-alpha"
LOCAL = "ollama/spark-x2.5-4b-q4"


def _router(**kw) -> ModelRouter:
    params = {"main_model": MAIN, "local_model": LOCAL, "max_chars": 280, "fallback_minutes": 10}
    params.update(kw)
    return ModelRouter(**params)


def _msgs(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


# --------------------------------------------------------------- model refs
def test_split_preserves_slashes_inside_the_model_id():
    assert split_model_ref(MAIN) == ("nous", "stealth/space-bunny-alpha")


def test_split_without_a_slash_has_no_provider():
    assert split_model_ref("llama3.1:8b") == ("", "llama3.1:8b")


def test_split_of_none_is_empty():
    assert split_model_ref(None) == ("", "")


# ------------------------------------------------------------------ routing
def test_short_turn_goes_local_to_save_quota():
    decision = _router().route(_msgs("what is 2+2?"))
    assert decision.reason == REASON_SHORT
    assert (decision.provider, decision.model_name) == ("ollama", "spark-x2.5-4b-q4")


def test_long_turn_goes_to_the_main_api_model():
    decision = _router().route(_msgs("x" * 400))
    assert decision.reason == REASON_MAIN
    assert (decision.provider, decision.model_name) == ("nous", "stealth/space-bunny-alpha")


def test_tool_calls_always_use_the_main_model():
    short = _msgs("hi")
    decision = _router().route(short, has_tools=True)
    assert decision.reason == REASON_TOOLS
    assert decision.provider == "nous"


def test_no_local_model_configured_means_everything_is_main():
    router = _router(local_model=None)
    assert router.route(_msgs("hi")).reason == REASON_MAIN
    assert router.route(_msgs("x" * 9999)).reason == REASON_MAIN


def test_local_equal_to_main_is_not_treated_as_a_second_tier():
    router = _router(local_model=MAIN)
    decision = router.route(_msgs("hi"))
    assert decision.reason == REASON_SAME
    assert decision.provider == "nous"


def test_max_chars_zero_disables_short_turn_routing():
    router = _router(max_chars=0)
    assert router.route(_msgs("hi")).reason == REASON_MAIN


def test_exact_boundary_is_local():
    router = _router(max_chars=10)
    assert router.route(_msgs("0123456789")).reason == REASON_SHORT
    assert router.route(_msgs("01234567890")).reason == REASON_MAIN


def test_multimodal_content_counts_text_parts_only():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "a" * 100},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "z" * 5000}},
            ],
        }
    ]
    assert _router().route(messages).reason == REASON_SHORT


def test_empty_message_list_is_short():
    assert _router().route([]).reason == REASON_SHORT


# ------------------------------------------------------------------ fallback
def test_a_main_model_failure_routes_to_local():
    router = _router()
    router.record_failure(MAIN)
    decision = router.route(_msgs("x" * 400))
    assert decision.reason == REASON_FALLBACK
    assert decision.using_fallback is True
    assert decision.provider == "ollama"


def test_fallback_expires_and_main_is_retried():
    router = _router(fallback_minutes=10)
    router.record_failure(MAIN)
    assert router.fallback_active() is True
    # expire it without sleeping
    router._fallback_until = time.time() - 1
    assert router.fallback_active() is False
    assert router.route(_msgs("x" * 400)).reason == REASON_MAIN


def test_success_clears_the_fallback_window():
    router = _router()
    router.record_failure(MAIN)
    router.record_success(MAIN)
    assert router.fallback_active() is False
    assert router.route(_msgs("x" * 400)).reason == REASON_MAIN


def test_zero_fallback_minutes_means_no_fallback_window():
    router = _router(fallback_minutes=0)
    router.record_failure(MAIN)
    assert router.fallback_active() is False
    assert router.route(_msgs("x" * 400)).reason == REASON_MAIN


def test_failure_of_the_local_model_does_not_trip_the_main_fallback():
    router = _router()
    router.record_failure(LOCAL)
    assert router.fallback_active() is False
    assert router.route(_msgs("x" * 400)).reason == REASON_MAIN


# ----------------------------------------------------------- switch semantics
def test_switched_is_false_when_the_caller_already_points_at_the_target():
    router = _router()
    decision = router.route(_msgs("x" * 400), current=("nous", "stealth/space-bunny-alpha"))
    assert decision.switched is False


def test_switched_is_true_when_the_caller_points_at_the_other_tier():
    router = _router()
    decision = router.route(_msgs("x" * 400), current=("ollama", "spark-x2.5-4b-q4"))
    assert decision.switched is True


# -------------------------------------------------------------------- status
def test_status_reports_both_tiers_and_no_secret():
    router = _router()
    router.route(_msgs("x" * 400))
    status = router.status()
    assert status["main_model"] == MAIN
    assert status["local_model"] == LOCAL
    assert status["last_model"] == MAIN
    assert status["last_reason"] == REASON_MAIN
    assert "token" not in str(status).lower()
    assert "key" not in str(status).lower()


def test_history_is_bounded():
    router = _router()
    for i in range(200):
        router.route(_msgs("x" * (i % 400)))
    assert len(router.history) <= 50


def test_route_never_raises_on_junk_input():
    router = _router()
    for junk in (None, [{}], [{"role": "user"}], [{"content": 12345}], "not a list"):
        decision = router.route(junk)  # type: ignore[arg-type]
        assert decision.provider in ("", "nous", "ollama")


@pytest.mark.parametrize("max_chars", [-5, 0, 280, 10_000])
def test_max_chars_never_produces_an_impossible_gate(max_chars):
    router = _router(max_chars=max_chars)
    decision = router.route(_msgs("hello"))
    assert decision.reason in (REASON_MAIN, REASON_SHORT, REASON_SAME, REASON_NO_LOCAL)
