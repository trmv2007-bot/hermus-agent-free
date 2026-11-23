"""Model choice: the ranking, the measurement, the comparison, and the migration.

The complaint these tests answer is concrete: an install on a machine holding a
4B and an 8B recorded Mistral's *smallest* model. The second version of that bug
was subtler and is pinned here too — an authored table can name a model the key
answers with HTTP 429, so availability is asked of the provider, not assumed.

Each test pins a property that was missing:

* each provider's order is authored, stable, and strongest-first;
* availability is measured per candidate, cached, and a 429 is capacity while a
  404 is disappearance — the two never collapse into "retired";
* a local model wins only when it is at least as strong as the best free-tier
  model reachable, and the comparison is recorded either way;
* a model that cannot call tools is skipped, with the skip recorded;
* a recorded model below the best available is upgraded at startup and the run
  stays finished without a second first-run;
* nothing reachable fails to the stub and says so, never to a paid route.

No request leaves the machine: every provider answer is a fake transport.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import brain, model_catalog, model_health, onboarding
from core.thinking import routing

SERVING = {
    "ollama": {"installed": True, "serving": True, "models": ["llama3.2:1b", "lfm2.5:8b"]},
    "lmstudio": {"installed": False, "serving": False, "models": []},
    "nollama": {"serving": False, "models": []},
}

SERVING_STRONG = {
    "ollama": {"installed": True, "serving": True, "models": ["llama3.2:1b", "qwen2.5-coder:32b"]},
    "lmstudio": {"installed": False, "serving": False, "models": []},
    "nollama": {"serving": False, "models": []},
}

NO_LOCAL = {
    "ollama": {"installed": False, "serving": False, "models": []},
    "lmstudio": {"installed": False, "serving": False, "models": []},
    "nollama": {"serving": False, "models": []},
}

OK_BODY = json.dumps({"choices": [{"message": {"content": "hi"}}]})
BUSY_BODY = json.dumps({"error": {"message": "Rate limit exceeded on tokens per minute", "type": "rate_limited"}})
MISSING_BODY = json.dumps({"error": {"message": "Invalid model: unknown", "type": "invalid_request_error"}})


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """No key or named model leaks in, and no measurement survives a test."""
    for env_key in model_catalog.FREE_KEYS.values():
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.delenv("HERMUS_MODEL", raising=False)
    monkeypatch.setattr(brain, "_key_in_ui_store", lambda provider: False)
    monkeypatch.setattr(brain, "free_key_value", lambda provider: "")
    model_health.reset_cache()
    yield
    model_health.reset_cache()


def _with_key(monkeypatch, provider: str, value: str = "test-key-not-real") -> None:
    monkeypatch.setenv(model_catalog.FREE_KEYS[provider], value)
    monkeypatch.setenv("HERMUS_MODEL_PROBE", "1")
    monkeypatch.setattr(brain, "free_key_value", lambda name: value if name == provider else "")


def _transport(answers: dict[str, tuple[int, str]]):
    """A fake provider: answers by model name and counts every request made."""
    calls: list[str] = []

    def send(request, timeout):
        payload = getattr(request, "data", None)
        name = json.loads(payload)["model"] if payload else "listing"
        calls.append(name)
        return answers.get(name, (200, OK_BODY))

    return send, calls


# ------------------------------------------------------------------ ranking


def test_each_provider_has_an_authored_order_and_it_does_not_move():
    for provider in ("mistral", "groq", "openrouter", "gemini"):
        rows = model_catalog.ranked(provider)
        assert len(rows) >= 2, provider
        assert [row.ref for row in rows] == [row.ref for row in model_catalog.ranked(provider)]
        strengths = [model_catalog.strength(row) for row in rows]
        assert strengths == sorted(strengths, reverse=True), provider
        assert strengths[0] == max(strengths)
        assert model_catalog.pick(provider).choice == rows[0]


def test_no_rank_claims_that_a_model_is_reachable_or_dead():
    """Availability is measured, so the catalog must not assert it in prose."""
    for row in model_catalog.as_rows():
        assert len(row["why"]) > 30, row["ref"]
        assert row["size"], row["ref"]
        lowered = row["why"].lower()
        for claim in ("retired", "is available", "still works", "is dead"):
            assert claim not in lowered, row["ref"]


def test_no_catalog_default_can_route_to_a_paid_model():
    names = {row.name for provider in model_catalog.CATALOG for row in model_catalog.ranked(provider)}
    assert "auto" not in names
    openrouter = [row.name for row in model_catalog.ranked("openrouter")]
    assert openrouter and all(name.endswith(":free") for name in openrouter)


def test_the_free_tier_table_is_read_from_the_catalog():
    table = {provider: model for provider, _env, model, _url in brain.FREE_TIER}
    assert set(table) == {"mistral", "groq", "openrouter", "gemini"}
    for provider, model in table.items():
        assert model == model_catalog.default_model(provider)
    assert table["mistral"] != "open-mistral-nemo"
    assert "auto" not in table.values()


# ------------------------------------------------------- measured availability


def test_a_rate_limited_model_is_capacity_and_the_next_one_answers(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, calls = _transport(
        {
            "mistral-small-latest": (429, BUSY_BODY),
            "ministral-8b-latest": (200, OK_BODY),
        }
    )
    monkeypatch.setattr(model_health, "transport", send)

    resolved = brain.resolve_brain(status=SERVING)
    assert resolved["model"] == "mistral/ministral-8b-latest"
    assert calls[:2] == ["mistral-small-latest", "ministral-8b-latest"]
    refused = next(skip for skip in resolved["skipped"] if skip["ref"] == "mistral/mistral-small-latest")
    assert "429" in refused["why"] and "capacity" in refused["why"]
    assert model_health.probe("mistral", "k", "mistral-small-latest").state == model_health.BUSY


def test_a_model_missing_from_the_key_is_gone_and_skipped(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, _calls = _transport(
        {
            "mistral-small-latest": (404, MISSING_BODY),
            "ministral-8b-latest": (404, MISSING_BODY),
            "open-mistral-nemo": (404, MISSING_BODY),
            "ministral-3b-latest": (200, OK_BODY),
        }
    )
    monkeypatch.setattr(model_health, "transport", send)
    resolved = brain.resolve_brain(status=NO_LOCAL)
    assert resolved["model"] == "mistral/ministral-3b-latest"
    reasons = {skip["ref"]: skip["why"] for skip in resolved["skipped"]}
    assert "404" in reasons["mistral/mistral-small-latest"]
    assert "404" in reasons["mistral/ministral-8b-latest"]
    assert "404" in reasons["mistral/open-mistral-nemo"]


def test_a_probe_is_cached_so_resolution_does_not_spend_requests(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, calls = _transport({"mistral-small-latest": (200, OK_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    first = brain.resolve_brain(status=SERVING)
    spent = len(calls)
    second = brain.resolve_brain(status=SERVING)
    assert first["model"] == second["model"] == "mistral/mistral-small-latest"
    assert len(calls) == spent


def test_a_refused_key_stops_after_one_request(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, calls = _transport({"mistral-small-latest": (401, json.dumps({"error": {"message": "Unauthorized"}}))})
    monkeypatch.setattr(model_health, "transport", send)
    first = brain.resolve_brain(status=SERVING)
    assert first["model"] == "ollama/lfm2.5:8b"
    assert len(calls) == 1
    brain.resolve_brain(status=SERVING)
    assert len(calls) == 1


# ----------------------------------------------------- local versus hosted


def test_a_weaker_local_model_does_not_beat_a_stronger_free_tier(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, _calls = _transport({"mistral-small-latest": (200, OK_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    resolved = brain.resolve_brain(status=SERVING)
    assert resolved["model"] == "mistral/mistral-small-latest"
    assert "lfm2.5:8b" in resolved["reason"]
    assert "stronger than your best local model" in resolved["reason"]


def test_a_stronger_local_model_keeps_the_free_tier_unspent(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, _calls = _transport({"mistral-small-latest": (200, OK_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    resolved = brain.resolve_brain(status=SERVING_STRONG)
    assert resolved["model"] == "ollama/qwen2.5-coder:32b"
    assert "nothing hosted is spent" in resolved["reason"]


def test_the_order_models_are_discovered_in_decides_nothing():
    forward = brain.resolve_brain(status=SERVING)
    reversed_status = json.loads(json.dumps(SERVING))
    reversed_status["ollama"]["models"] = list(reversed(SERVING["ollama"]["models"]))
    backwards = brain.resolve_brain(status=reversed_status)
    assert forward["model"] == backwards["model"] == "ollama/lfm2.5:8b"


def test_a_local_model_lacking_tool_calling_is_skipped_with_a_reason():
    status = json.loads(json.dumps(SERVING))
    status["ollama"]["models"] = ["gemma2:27b", "llama3.2:1b"]
    resolved = brain.resolve_brain(status=status)
    assert resolved["model"] == "ollama/llama3.2:1b"
    assert any(skip["ref"] == "ollama/gemma2:27b" for skip in resolved["skipped"])
    assert "no function calling" in resolved["skipped"][0]["why"]


# ---------------------------------------------------------------- migration


def _write_state(path: Path, model: str, *, kind: str = "freekey", provider: str = "mistral") -> None:
    path.write_text(
        json.dumps(
            {
                "version": onboarding.STATE_VERSION,
                "name": "rover",
                "permissions": "act",
                "brain": {
                    "kind": kind,
                    "provider": provider,
                    "name": model.split("/", 1)[1],
                    "model": model,
                    "source": "recorded by the old picker",
                    "chosen_at": "2026-11-23T14:39:10+00:00",
                },
                "check": {"ok": True, "model": model.split("/", 1)[1], "reason": "", "at": None, "ms": 10},
                "completed_at": "2026-11-23T14:39:49+00:00",
            }
        ),
        encoding="utf-8",
    )


def test_a_recorded_model_below_the_best_is_upgraded_and_the_run_stays_finished(monkeypatch, tmp_path):
    state_file = tmp_path / "onboarding.json"
    monkeypatch.setattr(onboarding, "state_path", lambda: state_file)
    _write_state(state_file, "mistral/open-mistral-nemo")
    monkeypatch.setattr(brain, "local_status", lambda force=False: SERVING)

    from core.config import config

    monkeypatch.setattr(config, "model", "mistral/open-mistral-nemo", raising=False)

    picked = brain.apply_resolved_default()
    assert picked["applied"] is True
    assert picked["from"] == "mistral/open-mistral-nemo"
    assert picked["model"] == "ollama/lfm2.5:8b"
    assert config.model == "ollama/lfm2.5:8b"

    state = onboarding.load()
    assert state["brain"]["upgraded_from"] == "mistral/open-mistral-nemo"
    assert state["upgrade"]["to"] == "ollama/lfm2.5:8b"
    assert "not configured" in state["upgrade"]["reason"]
    # The migration does not pretend the new model has answered, and it does not
    # send the person back through first run either.
    assert onboarding.is_complete(state) is False
    assert onboarding.upgrade_pending(state) is True
    assert onboarding.is_usable(state) is True

    onboarding.record_check(state, ok=True, model="lfm2.5:8b", ms=120)
    onboarding.save(state)
    assert onboarding.upgrade_pending(onboarding.load()) is False
    assert onboarding.is_complete(onboarding.load()) is True

    again = brain.apply_resolved_default()
    assert again["applied"] is False
    assert "strongest" in again["reason"]


def test_a_model_the_key_is_rate_limiting_is_not_migrated_away(monkeypatch):
    """429 is capacity. Recording it as a retirement is the bug this pins."""
    _with_key(monkeypatch, "mistral")
    send, _calls = _transport({"mistral-small-latest": (429, BUSY_BODY), "ministral-8b-latest": (200, OK_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    offer = brain.upgrade_offer(recorded="mistral/ministral-8b-latest", status=SERVING)
    assert offer["needed"] is False
    assert offer["to"] == "mistral/ministral-8b-latest"


def test_a_content_model_that_answers_404_is_migrated_with_the_measured_reason(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, _calls = _transport({"open-mistral-nemo": (404, MISSING_BODY), "mistral-small-latest": (200, OK_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    offer = brain.upgrade_offer(recorded="mistral/open-mistral-nemo", status=NO_LOCAL)
    assert offer["needed"] is True
    assert offer["to"] == "mistral/mistral-small-latest"
    assert "404" in offer["reason"]


def test_a_recorded_model_that_is_still_the_best_is_left_alone(monkeypatch, tmp_path):
    state_file = tmp_path / "onboarding.json"
    monkeypatch.setattr(onboarding, "state_path", lambda: state_file)
    _write_state(state_file, "ollama/qwen2.5-coder:32b", kind="local", provider="ollama")
    monkeypatch.setattr(brain, "local_status", lambda force=False: SERVING_STRONG)

    from core.config import config

    monkeypatch.setattr(config, "model", "ollama/qwen2.5-coder:32b", raising=False)
    picked = brain.apply_resolved_default()
    assert picked["applied"] is False
    assert onboarding.load()["brain"]["model"] == "ollama/qwen2.5-coder:32b"


def test_a_pinned_model_is_never_overridden(monkeypatch):
    monkeypatch.setenv("HERMUS_MODEL", "ollama/llama3.2:1b")
    monkeypatch.setattr(brain, "local_status", lambda force=False: SERVING)
    picked = brain.apply_resolved_default()
    assert picked["applied"] is False
    assert "pins this model" in picked["reason"]


# ------------------------------------------------------------------- honest


def test_nothing_reachable_resolves_to_the_stub_and_says_why():
    resolved = brain.resolve_brain(status=NO_LOCAL)
    assert resolved["kind"] == "stub"
    assert resolved["model"] == brain.STUB_MODEL
    assert "no local model and no free-tier key" in resolved["reason"]
    assert "stub" in brain.pickup_line(resolved)


def test_the_turns_routing_record_carries_the_model_rank_reason():
    decision = routing.RouteDecision(
        tier=routing.TIER_FREE_HOSTED,
        model="mistral/mistral-small-latest",
        difficulty=3,
        reason="difficulty 3 needs more reasoning than the local model gives",
    )
    record = decision.to_dict()
    assert record["model_reason"] == model_catalog.find("mistral/mistral-small-latest").why
    assert routing.FREE_HOSTED_DEFAULT_MODELS["mistral"] == model_catalog.default_model("mistral")
    assert set(routing.FREE_HOSTED_DEFAULT_MODELS) == set(routing.FREE_HOSTED_PROVIDERS)
