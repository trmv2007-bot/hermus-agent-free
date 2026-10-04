"""Regression tests for discovery-first model selection."""

from __future__ import annotations


def test_gateway_does_not_fallback_to_configured_model(monkeypatch):
    import core.models.model_catalog as catalog_mod
    from core.models.gateway import ModelGateway

    monkeypatch.setattr(
        catalog_mod.model_catalog,
        "list",
        lambda **kwargs: {"models": [], "providers": [], "count": 0},
    )

    class Cfg:
        model = "ollama/some-hardcoded-model"

    # Even if legacy configuration contains a model, an empty runtime catalog
    # must not turn it into a selectable/executable fallback.
    monkeypatch.setattr("core.config.config", Cfg(), raising=False)

    gateway = ModelGateway()
    assert gateway.resolve_model("default") == (None, None)


def test_catalog_never_returns_provider_credentials(monkeypatch):
    import core.models.model_catalog as catalog_mod

    monkeypatch.setattr(
        catalog_mod,
        "discover_runtime_bundles",
        lambda include_local=True: [
            {
                "provider": "fake",
                "key": "SUPER-SECRET",
                "base_url": "http://fake",
                "models": [{"id": "runtime-model"}],
            }
        ],
    )
    monkeypatch.setattr(
        catalog_mod,
        "negotiate",
        lambda ref, probe=False: type(
            "Report",
            (),
            {"capabilities": {"tools": "yes"}, "context_tokens": 8192, "notes": []},
        )(),
    )
    monkeypatch.setattr(
        catalog_mod,
        "get_provider",
        lambda provider: {"name": "Fake"},
    )

    result = catalog_mod.ModelCatalog(ttl_seconds=5).list(refresh=True)
    serialized = str(result)
    assert "SUPER-SECRET" not in serialized
    assert "fake/runtime-model" in serialized
