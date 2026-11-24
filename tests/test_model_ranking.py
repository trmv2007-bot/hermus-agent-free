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
import os
import tempfile
from pathlib import Path

import pytest

from core import brain, free_tier, key_sources, model_catalog, model_health, onboarding
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
def _keyless_rungs_down():
    """These tests measure the keyed tiers, so both keyless rungs are marked down.

    The keyless rungs sit above the keyed ones in the ladder and would otherwise
    answer every case here, hiding the measurement each test is about. Their own
    behaviour is pinned in ``tests/test_free_tier.py``.
    """
    for provider in model_catalog.KEYLESS_PROVIDERS:
        free_tier.mark_down(provider, "marked down by this test module")
    free_tier.board.reset()
    yield
    free_tier.reset_health()
    free_tier.board.reset()


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """No key or named model leaks in, and no measurement survives a test."""
    for env_key in model_catalog.FREE_KEYS.values():
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.delenv("HERMUS_MODEL", raising=False)
    monkeypatch.setattr(key_sources, "store_keys", lambda provider: [])
    monkeypatch.setattr(key_sources, "home_env_path", lambda: Path("no-such-home-env-file"))
    monkeypatch.setattr(key_sources, "checkout_env_path", lambda: Path("no-such-checkout-env-file"))
    model_health.reset_cache()
    key_sources.reset_cache()
    yield
    model_health.reset_cache()
    key_sources.reset_cache()


def _with_key(monkeypatch, provider: str, value: str = "test-key-not-real") -> None:
    monkeypatch.setenv(model_catalog.FREE_KEYS[provider], value)
    monkeypatch.setenv("HERMUS_MODEL_PROBE", "1")
    key_sources.reset_cache()


def _transport(answers: dict[str, tuple[int, str]]):
    """A fake provider: answers by model name and counts every request made."""
    calls: list[str] = []

    def send(request, timeout):
        payload = getattr(request, "data", None)
        name = json.loads(payload)["model"] if payload else "listing"
        calls.append(name)
        return answers.get(name, (200, OK_BODY))

    return send, calls


def test_a_singleton_parked_in_the_real_home_yields_to_a_later_hermus_home(monkeypatch):
    """The shape of the incident that deleted his `onboarding.json` twice.

    A process built the workspace from the default ``~/.hermus`` before anything
    pointed the environment at a temporary home, and every later ``HERMUS_HOME``
    was ignored — so a test that "cleared its state" cleared his.
    """
    from core.workspace import workspace

    monkeypatch.setattr(workspace, "base_dir", Path(os.path.expanduser("~")) / ".hermus")
    late_home = Path(tempfile.mkdtemp(prefix="hermus-guard-"))
    monkeypatch.setenv("HERMUS_HOME", str(late_home))
    assert onboarding.state_path() == late_home / "onboarding.json"


def test_a_session_that_started_on_a_temp_home_keeps_it(monkeypatch):
    """A later module's `HERMUS_HOME` must not retarget another test's state file.

    Reading the variable unconditionally made a suite's results depend on import
    order: whichever module set it last at import owned the state path.
    """
    from core.workspace import workspace

    session_home = Path(tempfile.mkdtemp(prefix="hermus-session-"))
    monkeypatch.setattr(workspace, "base_dir", session_home)
    monkeypatch.setenv("HERMUS_HOME", str(Path(tempfile.mkdtemp(prefix="hermus-other-"))))
    assert onboarding.state_path() == session_home / "onboarding.json"


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

    # No local model, and both keyless rungs marked down by the fixture: this
    # measures the keyed rung, which is where a 429 has to be handled.
    resolved = brain.resolve_brain(status=NO_LOCAL)
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


def _transport_by_key(answers: dict[str, dict[str, tuple[int, str]]]):
    """A fake provider that answers differently per key, and counts every request."""
    calls: list[tuple[str, str]] = []

    def send(request, timeout):
        auth = request.headers.get("Authorization", "")
        key = auth.replace("Bearer ", "")
        payload = getattr(request, "data", None)
        name = json.loads(payload)["model"] if payload else "listing"
        calls.append((key, name))
        return answers.get(key, {}).get(name, (200, OK_BODY))

    return send, calls


def test_a_dead_key_does_not_win_over_the_working_one_beside_it(monkeypatch, tmp_path):
    """Two keys for one provider: the one that answers is used, and named."""
    monkeypatch.setenv("MISTRAL_API_KEY", "dead-key")
    home = tmp_path / ".env"
    home.write_text("MISTRAL_API_KEY=live-key" + chr(10), encoding="utf-8")
    monkeypatch.setattr(key_sources, "home_env_path", lambda: home)
    monkeypatch.setenv("HERMUS_MODEL_PROBE", "1")
    monkeypatch.delenv("HERMUS_NO_DOTENV", raising=False)
    key_sources.reset_cache()
    unauth = json.dumps({"detail": "Invalid API Key"})
    send, _calls = _transport_by_key(
        {
            "dead-key": {"mistral-small-latest": (401, unauth)},
            "live-key": {"mistral-small-latest": (200, OK_BODY)},
        }
    )
    monkeypatch.setattr(model_health, "transport", send)

    resolved = brain.resolve_brain(status=NO_LOCAL)
    assert resolved["model"] == "mistral/mistral-small-latest"
    resolution = key_sources.resolve("mistral")
    assert resolution.source == str(home)
    assert resolution.key == "live-key"


def test_a_refused_key_says_which_path_it_came_from(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "dead-key")
    monkeypatch.setenv("HERMUS_MODEL_PROBE", "1")
    key_sources.reset_cache()
    unauth = json.dumps({"detail": "Invalid API Key"})
    send, _calls = _transport_by_key({"dead-key": {"mistral-small-latest": (401, unauth)}})
    monkeypatch.setattr(model_health, "transport", send)
    resolved = brain.resolve_brain(status=NO_LOCAL)
    refusal = next(skip for skip in resolved["skipped"] if skip["ref"] == "mistral/mistral-small-latest")
    assert "401" in refusal["why"]
    assert "your environment" in refusal["why"]


def test_a_probe_is_cached_so_resolution_does_not_spend_requests(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, calls = _transport({"mistral-small-latest": (200, OK_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    first = brain.resolve_brain(status=NO_LOCAL)
    spent = len(calls)
    second = brain.resolve_brain(status=NO_LOCAL)
    assert first["model"] == second["model"] == "mistral/mistral-small-latest"
    assert len(calls) == spent


def test_a_refused_key_stops_after_one_request(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, calls = _transport({"mistral-small-latest": (401, json.dumps({"error": {"message": "Unauthorized"}}))})
    monkeypatch.setattr(model_health, "transport", send)
    first = brain.resolve_brain(status=NO_LOCAL)
    assert first["kind"] == "stub"
    assert len(calls) == 1
    brain.resolve_brain(status=NO_LOCAL)
    assert len(calls) == 1

    # With a local model answering, the refused key is never asked anything.
    calls.clear()
    local = brain.resolve_brain(status=SERVING)
    assert local["model"] == "ollama/lfm2.5:8b"
    assert calls == []


# ----------------------------------------------------- local versus hosted


def test_a_local_model_answers_before_a_free_tier_is_spent(monkeypatch):
    """The ladder's first rung: nothing leaves the machine while it can answer.

    This is a deliberate change from strength-first resolution. A free tier's key
    is not spent, and the person's words stay on their disk, while a model on the
    machine can answer — the rung below exists for machines that hold nothing.
    """
    _with_key(monkeypatch, "mistral")
    send, calls = _transport({"mistral-small-latest": (200, OK_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    resolved = brain.resolve_brain(status=SERVING)
    assert resolved["model"] == "ollama/lfm2.5:8b"
    assert resolved["kind"] == "local"
    assert resolved["leaves_machine"] is False
    assert calls == [], "no hosted tier is asked anything while a local model answers"
    assert resolved["ladder"][0]["rung"] == "local" and resolved["ladder"][0]["state"] == "chose"


def test_a_stronger_local_model_keeps_the_free_tier_unspent(monkeypatch):
    _with_key(monkeypatch, "mistral")
    send, calls = _transport({"mistral-small-latest": (200, OK_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    resolved = brain.resolve_brain(status=SERVING_STRONG)
    assert resolved["model"] == "ollama/qwen2.5-coder:32b"
    assert "nothing hosted is spent" not in resolved["reason"]
    assert "strongest of 2 pulled" in resolved["reason"]
    assert calls == []


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


def test_the_migration_opens_the_companion_and_names_what_it_replaced(monkeypatch, tmp_path):
    """A pending first answer is not an unfinished install (regression from #90)."""
    from gateway import routes_onboarding

    state_file = tmp_path / "onboarding.json"
    monkeypatch.setattr(onboarding, "state_path", lambda: state_file)
    _write_state(state_file, "mistral/open-mistral-nemo")
    monkeypatch.setattr(brain, "local_status", lambda force=False: SERVING)

    from core.config import config

    monkeypatch.setattr(config, "model", "mistral/open-mistral-nemo", raising=False)
    brain.apply_resolved_default()

    state = onboarding.load()
    assert state["upgrade"]["from"] == "mistral/open-mistral-nemo"
    assert state["upgrade"]["to"] == "ollama/lfm2.5:8b"
    assert routes_onboarding.redirect_target() == "/companion"
    assert routes_onboarding.state_payload()["upgrade"]["from"] == "mistral/open-mistral-nemo"


def test_an_install_with_no_recorded_model_adopts_the_best_and_still_opens(monkeypatch, tmp_path):
    from gateway import routes_onboarding

    state_file = tmp_path / "onboarding.json"
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "facts.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(onboarding, "state_path", lambda: state_file)
    monkeypatch.setattr(brain, "local_status", lambda force=False: SERVING)

    from core.config import config

    monkeypatch.setattr(config, "model", "ollama/llama3.1:8b", raising=False)
    picked = brain.apply_resolved_default()
    assert picked["recorded"] is True
    assert picked["model"] == "ollama/lfm2.5:8b"

    state = onboarding.load()
    assert state["brain"]["model"] == "ollama/lfm2.5:8b"
    assert state["upgrade"]["from"] is None
    assert onboarding.upgrade_pending(state) is True
    assert routes_onboarding.redirect_target() == "/companion"


def test_a_real_first_run_is_not_hijacked_by_a_pulled_model(monkeypatch, tmp_path):
    """A fresh install adopts the model but still records no choice it never met.

    The room opens now: a clean machine resolves a brain, and making its owner
    pick one first was the wizard this product no longer needs. What stays true is
    that nothing is recorded as *chosen* until the person or the first answer does.
    """
    from gateway import routes_onboarding

    state_file = tmp_path / "onboarding.json"
    monkeypatch.setattr(onboarding, "state_path", lambda: state_file)
    monkeypatch.setattr(brain, "local_status", lambda force=False: SERVING)

    from core.config import config

    monkeypatch.setattr(config, "model", "ollama/llama3.1:8b", raising=False)
    picked = brain.apply_resolved_default()
    assert picked["model"] == "ollama/lfm2.5:8b"
    assert "recorded" not in picked
    assert onboarding.load()["brain"] is None
    assert routes_onboarding.redirect_target() == "/companion"


def test_an_install_with_nothing_to_answer_with_still_opens_the_first_screen(monkeypatch, tmp_path):
    from gateway import routes_onboarding

    state_file = tmp_path / "onboarding.json"
    monkeypatch.setattr(onboarding, "state_path", lambda: state_file)
    monkeypatch.setattr(brain, "local_status", lambda force=False: NO_LOCAL)
    assert routes_onboarding.redirect_target() == "/welcome"


def test_a_model_the_key_is_rate_limiting_is_not_migrated_away(monkeypatch):
    """429 is capacity. Recording it as a retirement is the bug this pins.

    With a local model answering, the recorded hosted model does give way — but
    because the ladder puts local first, never because a 429 was read as a
    retirement: ``gone`` stays empty either way.
    """
    _with_key(monkeypatch, "mistral")
    send, _calls = _transport({"mistral-small-latest": (429, BUSY_BODY), "ministral-8b-latest": (429, BUSY_BODY)})
    monkeypatch.setattr(model_health, "transport", send)
    offer = brain.upgrade_offer(recorded="mistral/ministral-8b-latest", status=SERVING)
    assert offer["gone"] == ""
    assert "ladder puts first" in offer["reason"]
    assert offer["to"].startswith("ollama/")

    # With nothing local to move to, a rate-limited hosted model keeps its place.
    offer = brain.upgrade_offer(recorded="mistral/ministral-8b-latest", status=NO_LOCAL)
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
