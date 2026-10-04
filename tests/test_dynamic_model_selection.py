"""Tests for discovery-first model selection and the Nexus model control surface."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_catalog_only_contains_runtime_discovered_or_stored_models(monkeypatch):
    import core.models.model_catalog as catalog_mod

    monkeypatch.setattr(
        catalog_mod,
        "discover_runtime_bundles",
        lambda include_local=True: [
            {
                "provider": "fake",
                "key": "secret",
                "base_url": "http://fake",
                "models": [{"id": "runtime-model"}],
                "default_model": "preset-model",
                "source": "env",
            }
        ],
    )

    def probe(provider, api_key=None, base_url=None, timeout=12):
        assert api_key == "secret"
        return {
            "success": True,
            "models": [{"id": "live-model"}],
            "latency_ms": 7,
        }

    monkeypatch.setattr(catalog_mod, "list_models", probe)
    monkeypatch.setattr(
        catalog_mod,
        "negotiate",
        lambda ref, probe=False: type(
            "Report",
            (),
            {
                "capabilities": {"tools": "yes", "vision": "no"},
                "context_tokens": 32768,
                "notes": [],
            },
        )(),
    )
    cat = catalog_mod.ModelCatalog(ttl_seconds=5).list(probe=True, refresh=True)
    refs = {row["ref"] for row in cat["models"]}
    assert "fake/live-model" in refs
    assert "fake/runtime-model" not in refs
    assert "fake/preset-model" not in refs


def test_model_selection_route_is_runtime_validated(monkeypatch):
    from gateway.gateway import app
    import core.model_preferences as pref_mod
    import core.models.model_catalog as catalog_mod

    monkeypatch.setattr(
        catalog_mod.model_catalog,
        "list",
        lambda **kwargs: {
            "generated_at": 0,
            "providers": [{"provider": "fake", "configured": True}],
            "models": [
                {
                    "ref": "fake/tool-model",
                    "provider": "fake",
                    "id": "tool-model",
                    "capabilities": {"tools": "yes", "vision": "no"},
                    "source": "live",
                    "reachable": True,
                }
            ],
            "count": 1,
        },
    )
    pref_mod.model_preferences._data = {"version": 1, "selections": {}}
    client = TestClient(app)

    ok = client.post("/models/select", json={"role": "default", "model": "fake/tool-model"})
    assert ok.status_code == 200
    assert ok.json()["model"] == "fake/tool-model"

    bad = client.post("/models/select", json={"role": "default", "model": "fake/not-discovered"})
    assert bad.status_code == 400


def test_control_room_has_discovery_driven_model_surface():
    from pathlib import Path

    html = Path("gateway/control.html").read_text(encoding="utf-8")
    js = Path("gateway/static/nexus.js").read_text(encoding="utf-8")
    css = Path("gateway/static/nexus.css").read_text(encoding="utf-8")
    assert 'id="modelSelect"' in html
    assert "/models/catalog" in js
    assert "/models/select" in js
    assert "AUTO · best available" in js
    assert "model-surface" in css
    assert "llava:7b" not in html
    assert "llama3.1:8b" not in html


def test_gateway_llm_uses_persisted_selection(monkeypatch):
    from types import SimpleNamespace
    from core.models.gateway import ModelGateway
    import core.models.model_catalog as catalog_mod
    import core.model_preferences as pref_mod

    class FakeCatalog:
        def list(self, **_kwargs):
            return {
                "models": [{
                    "ref": "fake/runtime-42",
                    "provider": "fake",
                    "id": "runtime-42",
                    "source": "live",
                    "reachable": True,
                    "capabilities": {"tools": "yes"},
                }]
            }

    class FakePrefs:
        def get(self, role="default"):
            return "fake/runtime-42"

    seen = {}

    def builder(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(catalog_mod, "model_catalog", FakeCatalog())
    monkeypatch.setattr(pref_mod, "model_preferences", FakePrefs())

    gateway = ModelGateway(llm_builder=builder)
    gateway.llm()
    assert seen["provider"] == "fake"
    assert seen["model"] == "runtime-42"
