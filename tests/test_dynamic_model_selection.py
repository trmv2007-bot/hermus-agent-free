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
    import core.model_preferences as pref_mod
    import core.models.model_catalog as catalog_mod
    from gateway.gateway import app

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

    import core.model_preferences as pref_mod
    import core.models.model_catalog as catalog_mod
    from core.models.gateway import ModelGateway

    class FakeCatalog:
        def list(self, **_kwargs):
            return {
                "models": [
                    {
                        "ref": "fake/runtime-42",
                        "provider": "fake",
                        "id": "runtime-42",
                        "source": "live",
                        "reachable": True,
                        "capabilities": {"tools": "yes"},
                    }
                ]
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


def test_model_gateway_uses_dashboard_selection_when_model_is_omitted(monkeypatch, tmp_path):
    import core.model_preferences as pref_mod
    import core.models.model_catalog as catalog_mod
    from core.models.gateway import ModelGateway

    monkeypatch.setattr(
        catalog_mod.model_catalog,
        "list",
        lambda **kwargs: {
            "models": [
                {
                    "ref": "fake/selected-model",
                    "provider": "fake",
                    "id": "selected-model",
                    "capabilities": {"tools": "yes", "vision": "no"},
                    "source": "live",
                    "reachable": True,
                }
            ],
            "providers": [{"provider": "fake", "configured": True}],
            "count": 1,
        },
    )
    pref = pref_mod.ModelPreferences(path=str(tmp_path / "prefs.json"))
    pref.set("default", "fake/selected-model", validate=True)
    monkeypatch.setattr(pref_mod, "model_preferences", pref)

    built = {}

    def builder(**kwargs):
        built.update(kwargs)
        return object()

    gateway = ModelGateway(llm_builder=builder)
    gateway.llm()
    assert built["provider"] == "fake"
    assert built["model"] == "selected-model"


def test_explicit_agent_model_stays_pinned_across_turns(monkeypatch):
    from types import SimpleNamespace

    from core.agent import HermusAgent

    agent = HermusAgent.__new__(HermusAgent)
    agent._model_pinned = True
    agent.model_name = "fake/pinned-model"
    agent.llm = SimpleNamespace(provider="fake")
    agent.mode = SimpleNamespace(value="agent")

    called = {"router": False}

    class FakeRouter:
        def select(self, _text):
            called["router"] = True
            return {"success": True, "model": "fake/other-model"}

    import core.router2 as router_mod
    monkeypatch.setattr(router_mod, "router2", FakeRouter())

    result = agent._apply_router("second message")

    assert result["model"] == "fake/pinned-model"
    assert result["reason"] == "agent.model_pinned"
    assert agent.model_name == "fake/pinned-model"
    assert called["router"] is False
