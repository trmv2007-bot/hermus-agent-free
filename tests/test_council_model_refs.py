"""Council roster must not build un-callable model references.

_proven bug_: _discover_workers() returns rows whose "model" may already carry
its registry namespace ("ollama/SparkLLM/Spark-X2.5-4B:latest"). _assign_models
unconditionally composed f"{provider}/{model}", producing "ollama/ollama/...".
Ollama answers that with 404, so every council agent failed and the mission went
blocked for a reason unrelated to the work.

These tests assert the composition rule without needing a live model.
"""

from __future__ import annotations

import pytest

from core.counsel import members as members_mod


def _workers(monkeypatch, rows):
    monkeypatch.setattr(members_mod, "_discover_workers", lambda: rows)
    return rows


def test_provider_is_not_prepended_twice(monkeypatch) -> None:
    _workers(
        monkeypatch,
        [{"provider": "ollama", "model": "SparkLLM/Spark-X2.5-4B:latest", "key": "", "base_url": "http://x/v1"}],
    )
    spec = {"role": "chair", "name": "chair"}
    out = members_mod._assign_models([spec], model=None)[0]
    assert out["model"] == "ollama/SparkLLM/Spark-X2.5-4B:latest"
    assert not out["model"].startswith("ollama/ollama/")


def test_plain_model_name_still_gets_its_provider(monkeypatch) -> None:
    """A bare name must still be composed, or local-only setups break."""
    _workers(monkeypatch, [{"provider": "ollama", "model": "spark-x2.5-4b-q4:latest", "key": "", "base_url": "http://x/v1"}])
    out = members_mod._assign_models([{"role": "chair", "name": "chair"}], model=None)[0]
    assert out["model"] == "ollama/spark-x2.5-4b-q4:latest"


def test_prefix_match_is_case_insensitive(monkeypatch) -> None:
    _workers(monkeypatch, [{"provider": "Ollama", "model": "Ollama/some-model", "key": "", "base_url": "http://x/v1"}])
    out = members_mod._assign_models([{"role": "chair", "name": "chair"}], model=None)[0]
    assert out["model"] == "Ollama/some-model"


def test_every_assigned_ref_has_at_most_one_provider_prefix(monkeypatch) -> None:
    """The invariant, stated once: no ref may contain a doubled provider."""
    _workers(
        monkeypatch,
        [
            {"provider": "ollama", "model": "SparkLLM/Spark-X2.5-4B:latest", "key": "", "base_url": "http://x/v1"},
            {"provider": "nous", "model": "stealth/space-bunny-alpha", "key": "k", "base_url": "http://y/v1"},
            {"provider": "groq", "model": "llama-3.3-70b", "key": "k2", "base_url": "http://z/v1"},
        ],
    )
    assigned = members_mod._assign_models([{"role": f"r{i}", "name": f"r{i}"} for i in range(5)], model=None)
    for row in assigned:
        ref = row["model"]
        for known in ("ollama", "nous", "groq"):
            assert not ref.lower().startswith(known + "/" + known + "/"), f"doubled prefix in {ref!r}"


def test_pinned_spec_is_left_untouched(monkeypatch) -> None:
    """An explicitly pinned model is the caller's business, including a slash."""
    _workers(monkeypatch, [{"provider": "ollama", "model": "x", "key": "", "base_url": ""}])
    out = members_mod._assign_models([{"role": "judge", "name": "j", "model": "someone/custom-model"}], model=None)[0]
    assert out["model"] == "someone/custom-model"


def test_falls_back_to_config_model_when_no_workers(monkeypatch) -> None:
    _workers(monkeypatch, [])
    out = members_mod._assign_models([{"role": "chair", "name": "chair"}], model="fallback/model")[0]
    assert out["model"] == "fallback/model"


@pytest.mark.parametrize("model_name", ["SparkLLM/Spark-X2.5-4B:latest", "spark-x2.5-4b-q4:latest"])
def test_composed_refs_keep_the_namespace(monkeypatch, model_name: str) -> None:
    """Stripping must remove only the provider, never the model namespace."""
    _workers(monkeypatch, [{"provider": "ollama", "model": model_name, "key": "", "base_url": "http://x/v1"}])
    out = members_mod._assign_models([{"role": "chair", "name": "chair"}], model=None)[0]
    assert out["model"] == f"ollama/{model_name}"
