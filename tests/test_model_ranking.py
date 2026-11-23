"""Model choice: the ranking, the comparison, the skip, and the migration.

The complaint these tests answer is concrete: an install on a machine holding a
4B and an 8B recorded Mistral's *smallest* model. So nothing here asserts that
"a model was chosen" — each test pins the property that was missing:

* each provider's order is authored, stable, and strongest-first;
* a local model wins only when it is at least as strong as the best free-tier
  model reachable, and the comparison is recorded either way;
* a model that cannot call tools is skipped, with the skip recorded;
* a recorded model below the best available is upgraded at startup and the run
  stays finished without a second first-run;
* nothing reachable fails to the stub and says so, never to a paid route.

Offline throughout: every probe is a fake status dict and no key value is real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import brain, model_catalog, onboarding
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


@pytest.fixture(autouse=True)
def _no_free_keys(monkeypatch):
    """No real key can leak in from the developer's environment or key store."""
    for env_key in model_catalog.FREE_KEYS.values():
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setattr(brain, "_key_in_ui_store", lambda provider: False)


def _with_key(monkeypatch, provider: str) -> None:
    monkeypatch.setenv(model_catalog.FREE_KEYS[provider], "test-key-not-called")


# ------------------------------------------------------------------ ranking


def test_each_provider_has_an_authored_order_and_it_does_not_move():
    for provider in ("mistral", "groq", "openrouter", "gemini"):
        rows = model_catalog.ranked(provider)
        assert len(rows) >= 2, provider
        assert [row.ref for row in rows] == [row.ref for row in model_catalog.ranked(provider)]
        strengths = [model_catalog.strength(row) for row in rows]
        assert strengths == sorted(strengths, reverse=True), provider
        # First is not first by accident: it is the strongest in its own list.
        assert strengths[0] == max(strengths)
        assert model_catalog.pick(provider).choice == rows[0]


def test_a_retired_model_is_kept_for_migration_but_never_offered():
    offered = {row.ref for provider in model_catalog.CATALOG for row in model_catalog.ranked(provider)}
    assert "mistral/open-mistral-nemo" not in offered
    assert model_catalog.find("mistral/open-mistral-nemo") is not None
    assert model_catalog.find("mistral/open-mistral-nemo").retired is True


def test_every_rank_carries_the_reason_it_holds():
    for row in model_catalog.as_rows():
        assert len(row["why"]) > 30, row["ref"]
        assert row["size"] if "size" in row else True


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


# ----------------------------------------------------- local versus hosted


def test_a_weaker_local_model_does_not_beat_a_stronger_free_tier(monkeypatch):
    _with_key(monkeypatch, "mistral")
    resolved = brain.resolve_brain(status=SERVING)
    assert resolved["model"] == "mistral/mistral-small-latest"
    assert "lfm2.5:8b" in resolved["reason"]
    assert "stronger than your best local model" in resolved["reason"]


def test_a_stronger_local_model_keeps_the_free_tier_unspent(monkeypatch):
    _with_key(monkeypatch, "mistral")
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
    # gemma2:27b is by far the strongest, and refused for the one thing it cannot do.
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
    monkeypatch.delenv("HERMUS_MODEL", raising=False)

    picked = brain.apply_resolved_default()
    assert picked["applied"] is True
    assert picked["from"] == "mistral/open-mistral-nemo"
    assert picked["model"] == "ollama/lfm2.5:8b"
    assert config.model == "ollama/lfm2.5:8b"

    state = onboarding.load()
    assert state["brain"]["upgraded_from"] == "mistral/open-mistral-nemo"
    assert state["upgrade"]["to"] == "ollama/lfm2.5:8b"
    assert "retired" in state["upgrade"]["reason"]
    # The migration does not pretend the new model has answered, and it does not
    # send the person back through first run either.
    assert onboarding.is_complete(state) is False
    assert onboarding.upgrade_pending(state) is True
    assert onboarding.is_usable(state) is True

    # The first real answer clears the debt and the run is finished again.
    onboarding.record_check(state, ok=True, model="lfm2.5:8b", ms=120)
    onboarding.save(state)
    assert onboarding.upgrade_pending(onboarding.load()) is False
    assert onboarding.is_complete(onboarding.load()) is True

    again = brain.apply_resolved_default()
    assert again["applied"] is False
    assert "strongest" in again["reason"]


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
