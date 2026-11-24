"""Routing decisions, scored — deterministic, no model.

The classifier and the over/under-routing counts are pure functions, so they are
tested as such: same input, same decision, with the reason a person would read.
"""

from __future__ import annotations

import json

from core.thinking.routing import (
    FREE_HOSTED_DEFAULT_MODELS,
    OVERROUTE_MAX_STEPS,
    TIER_FREE_HOSTED,
    TIER_LOCAL,
    TIER_UNAVAILABLE,
    UNDERROUTE_FAILED_CALLS,
    RoutingLedger,
    decide_route,
    routing_outcome,
    tier_of,
)

LOCAL = "ollama/llama3.2:1b"
HOSTED = "groq/llama-3.1-8b-instant"


def test_tier_of_recognises_local_and_free_hosted():
    assert tier_of(LOCAL) == TIER_LOCAL
    assert tier_of("nollama/MiniCPM5-1B-int4-g128-ov") == TIER_LOCAL
    assert tier_of(HOSTED) == TIER_FREE_HOSTED
    assert tier_of("openrouter/meta-llama/llama-3.3-70b-instruct:free") == TIER_FREE_HOSTED


def test_paid_and_unknown_providers_are_not_routable():
    for ref in ("openai/gpt-4o", "anthropic/claude-3-5-sonnet", "typo-model", ""):
        assert tier_of(ref) == TIER_UNAVAILABLE


def test_easy_question_stays_local_even_when_a_free_tier_is_configured():
    decision = decide_route("what is the capital of France?", local_model=LOCAL, hosted_models=[HOSTED])
    assert decision.tier == TIER_LOCAL
    assert decision.model == LOCAL
    assert decision.difficulty < 3
    assert "free tier is not spent" in decision.reason


def test_multi_step_task_escalates_to_the_free_tier():
    text = (
        "Refactor the retry logic in core/net.py, then migrate the callers, "
        "add regression tests for each path, and audit the timeout handling."
    )
    decision = decide_route(text, local_model=LOCAL, hosted_models=[HOSTED])
    assert decision.tier == TIER_FREE_HOSTED
    assert decision.model == HOSTED
    assert decision.difficulty >= 3
    assert "escalating" in decision.reason


def test_hard_task_without_a_key_says_the_local_model_may_be_too_weak():
    decision = decide_route(
        "Debug and fix the concurrency bug in the mission engine, then audit the lock ordering.",
        local_model=LOCAL,
        hosted_models=[],
    )
    assert decision.tier == TIER_LOCAL
    assert decision.difficulty >= 3
    assert "no free-tier key" in decision.reason
    assert "too weak" in decision.reason


def test_no_model_at_all_is_unavailable_with_a_reason():
    decision = decide_route("hello", local_model="", hosted_models=[])
    assert decision.tier == TIER_UNAVAILABLE
    assert decision.model == ""
    assert "nothing free to run on" in decision.reason


def test_a_paid_reference_never_becomes_a_candidate():
    decision = decide_route(
        "Audit and refactor the whole auth module, then migrate every caller.",
        local_model=LOCAL,
        hosted_models=["openai/gpt-4o"],
    )
    assert decision.tier == TIER_LOCAL
    assert decision.hosted_models == []


def test_forced_local_is_honoured_and_explained():
    decision = decide_route(
        "Refactor and audit the whole module.", local_model=LOCAL, hosted_models=[HOSTED], forced_tier="local"
    )
    assert decision.tier == TIER_LOCAL
    assert decision.forced is True
    assert "forced to local" in decision.reason


def test_forced_tier_with_nothing_behind_it_is_unavailable_not_silent_fallback():
    decision = decide_route("hello", local_model="", hosted_models=[], forced_tier="free_hosted")
    assert decision.tier == TIER_UNAVAILABLE
    assert "no free-tier key is configured" in decision.reason


def test_free_hosted_defaults_are_declared_for_every_provider_we_can_pick():
    from core.thinking.routing import FREE_HOSTED_PROVIDERS

    assert set(FREE_HOSTED_DEFAULT_MODELS) == set(FREE_HOSTED_PROVIDERS)


def test_over_routing_is_a_hosted_turn_that_was_cheap():
    decision = decide_route("Audit, refactor and migrate the module.", local_model=LOCAL, hosted_models=[HOSTED])
    outcome = routing_outcome(decision, steps=OVERROUTE_MAX_STEPS, failed_calls=0, verified=True)
    assert outcome["over_routed"] is True
    assert outcome["under_routed"] is False
    assert "local model was likely enough" in outcome["note"]


def test_a_long_hosted_turn_is_not_over_routing():
    decision = decide_route("Audit, refactor and migrate the module.", local_model=LOCAL, hosted_models=[HOSTED])
    outcome = routing_outcome(decision, steps=9, failed_calls=0, verified=True)
    assert outcome["over_routed"] is False


def test_under_routing_is_a_local_turn_that_kept_failing():
    decision = decide_route("what is 7 times 8?", local_model=LOCAL, hosted_models=[HOSTED])
    outcome = routing_outcome(decision, steps=4, failed_calls=UNDERROUTE_FAILED_CALLS, verified=False)
    assert outcome["under_routed"] is True
    assert outcome["over_routed"] is False
    assert "needed a stronger model" in outcome["note"]


def test_one_failed_call_is_not_under_routing_yet():
    decision = decide_route("what is 7 times 8?", local_model=LOCAL, hosted_models=[HOSTED])
    assert routing_outcome(decision, steps=3, failed_calls=1)["under_routed"] is False


def test_the_ledger_records_and_counts(tmp_path):
    ledger = RoutingLedger(tmp_path / "routing.jsonl")
    local = decide_route("what is 7 times 8?", local_model=LOCAL, hosted_models=[HOSTED])
    hosted = decide_route("Audit, refactor and migrate the module.", local_model=LOCAL, hosted_models=[HOSTED])
    ledger.record(local, routing_outcome(local, steps=3, failed_calls=2, verified=False), session_id="s1")
    ledger.record(hosted, routing_outcome(hosted, steps=1, failed_calls=0, verified=True), session_id="s2")

    summary = ledger.summary()
    assert summary["turns"] == 2
    assert summary["tiers"] == {TIER_LOCAL: 1, TIER_FREE_HOSTED: 1}
    assert summary["under_routed"] == 1
    assert summary["over_routed"] == 1
    assert summary["verified"] == 1
    assert summary["avg_steps"] == 2.0

    rows = ledger.recent(5)
    assert rows[0]["decision"]["tier"] == TIER_LOCAL
    assert rows[1]["outcome"]["tier"] == TIER_FREE_HOSTED
    assert rows[1]["session_id"] == "s2"


def test_the_ledger_survives_a_corrupt_line(tmp_path):
    path = tmp_path / "routing.jsonl"
    path.write_text('{"decision": {"tier": "local"}, "outcome": null}\nnot json\n', encoding="utf-8")
    ledger = RoutingLedger(path)
    assert len(ledger.recent(10)) == 1
    assert ledger.summary()["turns"] == 0


def test_a_decision_is_json_serialisable_for_the_turn_result():
    decision = decide_route("build a small script that renames files", local_model=LOCAL, hosted_models=[HOSTED])
    payload = json.dumps(decision.to_dict())
    assert "reason" in json.loads(payload)


def _fake_availability(monkeypatch, answers: dict[str, tuple[int, str]]):
    """Answer the availability probe per model name, without a network."""
    from core import model_health

    def transport(request, timeout):
        name = json.loads(request.data.decode("utf-8"))["model"]
        return answers.get(name, (0, "offline"))

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key-not-a-real-one")
    monkeypatch.setenv("HERMUS_MODEL_PROBE", "1")
    monkeypatch.setattr(model_health, "transport", transport)
    model_health.reset_cache()
    return model_health


def test_an_escalation_target_is_the_model_the_key_answers(monkeypatch):
    """The catalog ranks Mistral's Small first; a free key can still 429 it."""
    from core.thinking.routing import answering_hosted_ref

    _fake_availability(
        monkeypatch,
        {
            "mistral-small-latest": (429, '{"message":"Rate limit exceeded","code":"1300"}'),
            "ministral-8b-latest": (200, '{"choices":[{"message":{"content":"hi"}}]}'),
        },
    )
    assert answering_hosted_ref("mistral/mistral-small-latest") == "mistral/ministral-8b-latest"


def test_a_tier_that_answers_nothing_returns_no_target(monkeypatch):
    from core.thinking.routing import answering_hosted_ref

    _fake_availability(
        monkeypatch,
        {
            name: (429, '{"message":"Rate limit exceeded"}')
            for name in ("mistral-small-latest", "ministral-8b-latest", "open-mistral-nemo", "ministral-3b-latest")
        },
    )
    assert answering_hosted_ref("mistral/mistral-small-latest") == ""


def test_an_unmeasurable_tier_keeps_the_catalog_choice(monkeypatch):
    """Offline is not evidence against a model, so nothing is re-picked."""
    from core.thinking.routing import answering_hosted_ref

    _fake_availability(monkeypatch, {})
    assert answering_hosted_ref("mistral/mistral-small-latest") == "mistral/mistral-small-latest"


def test_an_answered_model_is_remembered_for_the_next_decision(monkeypatch):
    """A warm measurement costs nothing: the second ask sends no request."""
    from core.thinking.routing import answering_hosted_ref

    calls: list[str] = []

    def transport(request, timeout):
        calls.append(json.loads(request.data.decode("utf-8"))["model"])
        if calls[-1] == "mistral-small-latest":
            return 429, '{"message":"Rate limit exceeded"}'
        return 200, '{"choices":[{"message":{"content":"hi"}}]}'

    from core import model_health

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key-not-a-real-one")
    monkeypatch.setenv("HERMUS_MODEL_PROBE", "1")
    monkeypatch.setattr(model_health, "transport", transport)
    model_health.reset_cache()
    assert answering_hosted_ref("mistral/mistral-small-latest") == "mistral/ministral-8b-latest"
    before = len(calls)
    assert answering_hosted_ref("mistral/mistral-small-latest") == "mistral/ministral-8b-latest"
    assert len(calls) == before, "the cached measurement must be reused"


def test_a_turn_already_on_the_free_tier_does_not_hop_to_another_hosted_model():
    """A second model on the same tier is a second request, not an upgrade."""
    decision = decide_route(
        "Audit, refactor and migrate the module.",
        local_model="mistral/ministral-8b-latest",
        hosted_models=["mistral/mistral-small-latest"],
    )
    assert decision.tier == TIER_FREE_HOSTED
    assert decision.model == "mistral/ministral-8b-latest"
    assert "already on the free tier" in decision.reason


def test_a_local_turn_still_escalates_to_the_configured_hosted_model():
    decision = decide_route(
        "Audit, refactor and migrate the module.",
        local_model=LOCAL,
        hosted_models=["mistral/mistral-small-latest"],
    )
    assert decision.model == "mistral/mistral-small-latest"
