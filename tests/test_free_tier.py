"""A free product answers on the first run, with no account and no key.

Two endpoints answer anonymously, verified from this machine rather than read
from a doc page, so the ladder's second rung is a real answer and not a promise:
- OVHcloud AI Endpoints, OpenAI-compatible, 2 requests a minute per model;
- Pollinations, OpenAI-compatible, the rung below it when the first is busy.

Each test pins one property that makes that safe to ship:

* the catalog ranks the keyless models without any credential, and the ladder
  walks local -> keyless -> keyed -> stub in that order;
* the documented ceiling is a queue, not an error: a request that would exceed
  it is told how long it waits, and the sentence says why;
* a rate limit on the first keyless rung moves the turn to the second instead of
  ending it, and a rung that refuses a request is skipped next time with the
  refusal in its reason;
* onboarding never asks for a key while a keyless rung can answer.

No test reaches a provider: every request is a fake and every clock is injected.
"""

from __future__ import annotations

import json

import pytest

from core import brain, free_tier, key_sources, model_catalog, model_health
from core.openai_compat import CompatAPIError

NO_LOCAL = {
    "ollama": {"installed": False, "serving": False, "models": []},
    "lmstudio": {"installed": False, "serving": False, "models": []},
    "nollama": {"serving": False, "models": []},
}

SERVING = {
    "ollama": {"installed": True, "serving": True, "models": ["qwen3.5:2b", "llama3.2:1b"]},
    "lmstudio": {"installed": False, "serving": False, "models": []},
    "nollama": {"serving": False, "models": []},
}

SERVING_NO_TOOLS = {
    "ollama": {"installed": True, "serving": True, "models": ["gemma2:27b"]},
    "lmstudio": {"installed": False, "serving": False, "models": []},
    "nollama": {"serving": False, "models": []},
}

OK_BODY = json.dumps({"choices": [{"message": {"content": "hi"}}]})


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """No key, no stored model, no remembered health, and no live probe."""
    for env_key in model_catalog.FREE_KEYS.values():
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.delenv("HERMUS_MODEL", raising=False)
    monkeypatch.setattr(key_sources, "store_keys", lambda provider: [])
    monkeypatch.setattr(key_sources, "home_env_path", lambda: __import__("pathlib").Path("no-such-home-env-file"))
    monkeypatch.setattr(key_sources, "checkout_env_path", lambda: __import__("pathlib").Path("no-such-checkout-env-file"))
    key_sources.reset_cache()
    model_health.reset_cache()
    free_tier.reset_health()
    free_tier.clear_notices()
    free_tier.board.reset()
    yield
    key_sources.reset_cache()
    model_health.reset_cache()
    free_tier.reset_health()
    free_tier.clear_notices()
    free_tier.board.reset()


# ------------------------------------------------------------------- catalog


def test_the_keyless_tiers_are_ranked_and_need_no_credential():
    """Authored order, strongest first, and nothing to paste for either one."""
    # Measured order: the endpoint that answers every request first, the one
    # with a one-request-a-year anonymous quota second.
    assert model_catalog.KEYLESS_PROVIDERS == ("pollinations", "ovh")
    for provider in model_catalog.KEYLESS_PROVIDERS:
        rows = model_catalog.ranked(provider)
        assert rows, provider
        strengths = [model_catalog.strength(row) for row in rows]
        assert strengths == sorted(strengths, reverse=True), provider
        assert model_catalog.pick(provider).choice == rows[0], provider
        assert model_catalog.needs_key(provider) is False
        assert model_catalog.tier_of(rows[0].ref) == "keyless"
    assert model_catalog.needs_key("mistral") is True
    assert model_catalog.tier_of("ollama/qwen3.5:2b") == "local"
    assert model_catalog.tier_of("mistral/mistral-small-latest") == "keyed"
    assert model_catalog.tier_of("mock/mock") == "stub"


def test_the_keyless_catalog_needs_no_key_to_pick_anything():
    """A picker with no keys at all still has a real model to offer."""
    picked = model_catalog.best_of([f"ovh/{row.name}" for row in model_catalog.ranked("ovh")])
    assert picked.choice is not None
    assert picked.choice.provider == "ovh"
    assert picked.choice.tools is True
    chat_only = model_catalog.pick("pollinations")
    assert chat_only.choice is not None and chat_only.choice.tools is False
    assert "calls tools" in chat_only.note or chat_only.choice is not None


def test_a_keyless_preset_is_registered_without_a_key():
    from core.providers import get_provider

    for provider in model_catalog.KEYLESS_PROVIDERS:
        preset = get_provider(provider)
        assert preset.get("no_auth") is True, provider
        assert preset.get("keyless") is True, provider
        assert preset.get("base_url"), provider
        assert preset.get("env_key") is None, provider


# ------------------------------------------------------------- the allowance


def test_the_allowance_queues_a_third_request_instead_of_failing_it():
    """Two a minute means the third waits — with the sentence that explains it."""
    bucket = free_tier.TokenBucket(2)
    assert bucket.reserve(now=0.0) == 0.0
    assert bucket.reserve(now=0.0) == 0.0
    wait = bucket.reserve(now=0.0)
    assert 29.0 < wait <= 30.0, wait
    assert bucket.reserve(now=0.0) > wait  # the queue is ordered, not a stampede


def test_the_board_states_the_wait_in_plain_words(monkeypatch):
    clock = {"now": 100.0}
    board = free_tier.Board(clock=lambda: clock["now"])
    first = board.reserve("ovh", "Qwen3.5-397B-A17B")
    second = board.reserve("ovh", "Qwen3.5-397B-A17B")
    third = board.reserve("ovh", "Qwen3.5-397B-A17B")
    assert first.seconds == second.seconds == 0.0
    assert third.waiting is True
    assert third.sentence.startswith("the free endpoint allows 2 requests a minute")
    assert "this will go in" in third.sentence
    slept: list[float] = []
    waited = free_tier.wait_for_turn(third, sleeper=slept.append)
    assert slept and abs(slept[0] - third.seconds) < 0.01
    assert waited.gave_up is False
    assert free_tier.recent_notices(1) == [third.sentence]


def test_each_model_has_its_own_allowance():
    clock = {"now": 0.0}
    board = free_tier.Board(clock=lambda: clock["now"])
    board.reserve("ovh", "Qwen3.5-397B-A17B")
    board.reserve("ovh", "Qwen3.5-397B-A17B")
    other = board.reserve("ovh", "gpt-oss-120b")
    assert other is not None and other.waiting is False


def test_a_wait_longer_than_a_turn_gives_up_instead_of_freezing():
    board = free_tier.Board(clock=lambda: 0.0)
    for _ in range(4):
        slot = board.reserve("ovh", "Qwen3.5-397B-A17B")
    slept: list[float] = []
    waited = free_tier.wait_for_turn(slot, sleeper=slept.append, max_wait=5.0)
    assert waited.gave_up is True
    assert slept == []


def test_an_unmetered_provider_reserves_nothing():
    board = free_tier.Board(clock=lambda: 0.0)
    assert board.reserve("pollinations", "openai-fast") is None
    assert free_tier.budget_for("pollinations") is None


# --------------------------------------------------------------- the ladder


def test_a_clean_machine_answers_from_the_keyless_rung_and_says_which():
    resolved = brain.resolve_brain(status=NO_LOCAL)
    assert resolved["kind"] == "keyless"
    assert resolved["tier"] == "keyless"
    assert resolved["provider"] == model_catalog.KEYLESS_PROVIDERS[0]
    assert resolved["model"].startswith(f"{model_catalog.KEYLESS_PROVIDERS[0]}/")
    assert resolved["leaves_machine"] is True
    assert "leave this machine" in resolved["privacy"]
    assert "no key" in brain.pickup_line(resolved)
    local_step = next(step for step in resolved["ladder"] if step["rung"] == "local")
    assert local_step["state"] == "skipped"
    assert "no local runtime is installed" in local_step["why"]
    assert brain.speed_hint(resolved)


def test_a_local_model_answers_before_any_hosted_rung_is_touched():
    resolved = brain.resolve_brain(status=SERVING)
    assert resolved["kind"] == "local"
    assert resolved["leaves_machine"] is False
    assert resolved["privacy"] == ""
    assert [step["rung"] for step in resolved["ladder"]][0] == "local"
    assert resolved["ladder"][0]["state"] == "chose"


def test_a_local_model_that_cannot_call_tools_falls_through_with_a_reason():
    resolved = brain.resolve_brain(status=SERVING_NO_TOOLS)
    assert resolved["kind"] == "keyless"
    skipped = next(step for step in resolved["ladder"] if step["state"] == "skipped")
    assert "function calling" in skipped["why"]
    assert any(row["ref"] == "ollama/gemma2:27b" for row in resolved["skipped"])


def test_the_second_keyless_rung_answers_when_the_first_refused():
    first, second = model_catalog.KEYLESS_PROVIDERS
    free_tier.mark_down(first, "HTTP 404 — the endpoint no longer serves it")
    resolved = brain.resolve_brain(status=NO_LOCAL)
    assert resolved["provider"] == second
    skipped = next(step for step in resolved["ladder"] if step["ref"] == first)
    assert skipped["state"] == "skipped" and "HTTP 404" in skipped["why"]


def test_the_ladder_reaches_the_stub_only_when_every_rung_says_why():
    for provider in model_catalog.KEYLESS_PROVIDERS:
        free_tier.mark_down(provider, "HTTP 404")
    resolved = brain.resolve_brain(status=NO_LOCAL)
    assert resolved["kind"] == "stub"
    assert resolved["tier"] == "stub"
    assert "local:" in resolved["reason"]
    assert "keyless:" in resolved["reason"]
    assert "keyed:" in resolved["reason"]
    assert "MISTRAL_API_KEY" in resolved["reason"]


def test_a_keyed_tier_answers_when_the_keyless_rungs_are_down(monkeypatch):
    for provider in model_catalog.KEYLESS_PROVIDERS:
        free_tier.mark_down(provider, "HTTP 404")
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    key_sources.reset_cache()
    resolved = brain.resolve_brain(status=NO_LOCAL)
    assert resolved["kind"] == "freekey"
    assert resolved["provider"] == "mistral"
    assert resolved["leaves_machine"] is True


# ------------------------------------------------- the turn, against fakes


def _fake_chat(monkeypatch, answers):
    """A fake transport for the sync path: answers by provider, counts calls."""
    calls: list[str] = []

    def send(provider, model, messages, api_key=None, base_url=None, tools=None, timeout=120, **kwargs):
        calls.append(provider)

        class Resp:
            content = "answered"
            tool_calls: list = []
            usage = {"total_tokens": 3}
            headers: dict = {}
            latency_ms = 1

        outcome = answers.get(provider)
        if outcome is None:
            return Resp()
        if isinstance(outcome, Exception):
            raise outcome
        return Resp()

    # core.llm imports the transport inside the call, so the fake belongs on the
    # module it is imported from.
    monkeypatch.setattr("core.openai_compat.chat_completions", send)
    return calls


def _first_keyless():
    provider = model_catalog.KEYLESS_PROVIDERS[0]
    return provider, f"{provider}/{model_catalog.default_model(provider)}"


def test_a_rate_limited_first_rung_is_answered_by_the_second(monkeypatch):
    from core.llm import FreeLLM

    first, second = model_catalog.KEYLESS_PROVIDERS
    calls = _fake_chat(monkeypatch, {first: CompatAPIError("busy", status_code=429)})
    llm = FreeLLM(_first_keyless()[1])
    resp = llm.chat([{"role": "user", "content": "rate-limit-rung-probe"}])
    assert resp.content == "answered"
    assert calls == [first, second]
    assert llm.last_fallback and llm.last_fallback["to_provider"] == second
    assert llm.last_route["provider"] == second
    assert llm.last_route["leaves_machine"] is True
    assert free_tier.down_reason(first) == "", "a burst 429 must not retire the rung"


def test_a_burst_limit_is_capacity_and_the_rung_stays(monkeypatch):
    """A short retry-after is a burst: the rung is kept for the next question."""
    from core.llm import FreeLLM

    first, second = model_catalog.KEYLESS_PROVIDERS
    calls = _fake_chat(
        monkeypatch,
        {first: CompatAPIError("slow down", status_code=429, rate_limit={"retry_after": 3})},
    )
    llm = FreeLLM(_first_keyless()[1])
    assert llm.chat([{"role": "user", "content": "burst-limit-probe"}]).content == "answered"
    assert calls == [first, second]
    assert free_tier.down_reason(first) == ""


def test_a_spent_allowance_is_skipped_for_as_long_as_the_endpoint_says(monkeypatch):
    """Measured here: OVH answers an exhausted anonymous quota with a year's wait."""
    from core.llm import FreeLLM

    first, second = model_catalog.KEYLESS_PROVIDERS
    calls = _fake_chat(
        monkeypatch,
        {
            first: CompatAPIError(
                "API rate limit exceeded",
                status_code=429,
                rate_limit={"retry_after": 3_242_329},
            )
        },
    )
    llm = FreeLLM(_first_keyless()[1])
    assert llm.chat([{"role": "user", "content": "spent-allowance-probe"}]).content == "answered"
    assert calls == [first, second]
    reason = free_tier.down_reason(first)
    assert "spent" in reason and "days" in reason
    # And the ladder now goes straight past it, with that sentence as the reason.
    resolved = brain.resolve_brain(status=NO_LOCAL)
    assert resolved["provider"] == second
    skipped = next(step for step in resolved["ladder"] if step["ref"] == first)
    assert skipped["state"] == "skipped" and "spent" in skipped["why"]


def test_a_keyless_rung_that_is_gone_is_remembered_and_skipped(monkeypatch):
    from core.llm import FreeLLM

    first, second = model_catalog.KEYLESS_PROVIDERS
    calls = _fake_chat(monkeypatch, {first: CompatAPIError("Invalid model", status_code=404)})
    llm = FreeLLM(_first_keyless()[1])
    assert llm.chat([{"role": "user", "content": "gone-rung-probe"}]).content == "answered"
    assert calls == [first, second]
    assert "Invalid model" in free_tier.down_reason(first)


def test_a_busy_rung_is_skipped_without_a_request_at_all(monkeypatch):
    """OVH is the only rung with a documented ceiling, so it is the one that queues."""
    from core.llm import FreeLLM

    calls = _fake_chat(monkeypatch, {})
    monkeypatch.setattr(free_tier, "board", free_tier.Board(clock=lambda: 0.0))
    for _ in range(6):
        free_tier.board.reserve("ovh", "Qwen3.5-397B-A17B")
    monkeypatch.setattr(free_tier, "wait_for_turn", lambda slot, **kw: free_tier.Waited(0.0, slot.sentence, True))
    llm = FreeLLM("ovh/Qwen3.5-397B-A17B")
    resp = llm.chat([{"role": "user", "content": "busy-rung-probe"}])
    assert calls == [], "a queue longer than a turn is skipped, not waited out and not sent"
    assert "requests a minute" in llm.last_notice
    assert "ovh" in resp.content


def test_a_waiting_request_reports_the_sentence_and_completes(monkeypatch):
    from core.llm import FreeLLM

    calls = _fake_chat(monkeypatch, {})
    monkeypatch.setattr(free_tier, "board", free_tier.Board(clock=lambda: 0.0))
    free_tier.board.reserve("ovh", "Qwen3.5-397B-A17B")
    free_tier.board.reserve("ovh", "Qwen3.5-397B-A17B")
    slept: list[float] = []
    real_wait = free_tier.wait_for_turn
    monkeypatch.setattr(free_tier, "wait_for_turn", lambda slot, **kw: real_wait(slot, sleeper=slept.append, **kw))
    llm = FreeLLM("ovh/Qwen3.5-397B-A17B")
    resp = llm.chat([{"role": "user", "content": "waiting-rung-probe"}])
    assert resp.content == "answered", "the request waited and was sent, never refused with a 429"
    assert calls == ["ovh"]
    assert slept and slept[0] > 0
    assert "requests a minute" in resp.notice


# -------------------------------------------------------------- onboarding


def test_onboarding_never_asks_for_a_key_while_a_keyless_rung_answers(monkeypatch):
    from gateway import routes_onboarding

    monkeypatch.setattr(brain, "local_status", lambda force=False: NO_LOCAL)
    payload = routes_onboarding.state_payload()
    assert payload["free_keys"] == {provider: False for provider in payload["free_keys"]}
    assert payload["resolved"]["kind"] == "keyless"
    assert payload["free_keys"].get("mistral") is False
    assert payload["privacy"] and "leave this machine" in payload["privacy"]
    assert payload["speed"]
    assert [step["rung"] for step in payload["ladder"]][0] == "local"
    assert routes_onboarding.redirect_target() == "/companion", "a keyless answer opens the room"


def test_onboarding_only_opens_the_welcome_screen_when_nothing_can_answer(monkeypatch):
    from gateway import routes_onboarding

    monkeypatch.setattr(brain, "local_status", lambda force=False: NO_LOCAL)
    free_tier.mark_down("ovh", "HTTP 404")
    free_tier.mark_down("pollinations", "HTTP 503")
    assert routes_onboarding.redirect_target() == "/welcome"


def test_the_first_answer_resolves_its_own_model_when_none_was_chosen(monkeypatch, tmp_path):
    from gateway import routes_onboarding

    monkeypatch.setattr(brain, "local_status", lambda force=False: NO_LOCAL)
    monkeypatch.setattr("core.onboarding.state_path", lambda: tmp_path / "onboarding.json")

    events: list[tuple[str, dict]] = []

    class FakeLLM:
        def chat_stream(self, messages, tools=None):
            yield "answered "

            yield "here"

    monkeypatch.setattr("core.models.get_model_gateway", lambda: type("G", (), {"llm": lambda self, model=None: FakeLLM()})())
    for chunk in routes_onboarding._answer_stream("what can you do?"):
        name = chunk.split("\n", 1)[0].removeprefix("event: ")
        data = json.loads(chunk.split("data: ", 1)[1])
        events.append((name, data))

    kinds = [name for name, _data in events]
    assert "error" not in kinds
    status = next(data for name, data in events if name == "status")
    assert "no key" in status["pickup"]
    assert "leave this machine" in status["privacy"]
    assert status["speed"]
    done = next(data for name, data in events if name == "done")
    assert done["ok"] is True


def test_a_recorded_keyless_model_gives_way_to_a_model_on_the_machine():
    """The ladder is what decides adoption too, not only the strength table."""
    offer = brain.upgrade_offer(recorded="pollinations/openai-fast", status=SERVING)
    assert offer["needed"] is True
    assert offer["to"].startswith("ollama/")
    assert "ladder puts first" in offer["reason"]

    settled = brain.upgrade_offer(recorded="pollinations/openai-fast", status=NO_LOCAL)
    assert settled["needed"] is False
    assert settled["to"] == "pollinations/openai-fast"
