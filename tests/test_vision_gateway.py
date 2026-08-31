"""Tools must not talk to a model backend directly — vision goes via ModelGateway.

These assert the vision tool's *contract* is preserved while its traffic is
routed through the canonical ModelGateway (image -> text), and that a model call
never fabricates a description on failure.
"""
from __future__ import annotations

import inspect
import os
import tempfile

from core.contracts import FailureClass
from core.models import ModelGatewayError


def _fake_image() -> str:
    fd, path = tempfile.mkstemp(suffix=".png")
    os.write(fd, b"\x89PNG\r\n\x1a\nfake-image-bytes")
    os.close(fd)
    return path


def test_vision_tool_does_not_call_the_backend_directly():
    """The vision tool must not issue its own request to a model endpoint."""
    import tools.vision as v

    src = inspect.getsource(v)
    assert "requests.post" not in src
    assert "requests.get" not in src
    assert "/api/generate" not in src and "/api/chat" not in src


def test_vision_analyze_success_contract(monkeypatch):
    import tools.vision as v

    image = _fake_image()
    monkeypatch.setattr(
        v, "get_model_gateway",
        lambda: _FakeGW(content="A cat sits on a rug."),
    )
    try:
        out = v.vision_analyze(image, prompt="describe", model="llava:7b")
    finally:
        os.unlink(image)
    assert out["success"] is True
    assert out["description"] == "A cat sits on a rug."
    assert out["description_truncated"] == "A cat sits on a rug."
    assert out["model"] == "llava:7b" and out["image"]


def test_vision_analyze_model_not_found(monkeypatch):
    import tools.vision as v

    image = _fake_image()
    monkeypatch.setattr(
        v, "get_model_gateway",
        lambda: _FakeGW(err="Model llava:7b not found.",
                        fc=FailureClass.MODEL_UNAVAILABLE.value),
    )
    try:
        out = v.vision_analyze(image, model="llava:7b")
    finally:
        os.unlink(image)
    assert out["success"] is False
    assert "not found" in out["error"] and "ollama pull" in out["error"]


def test_vision_analyze_network_error(monkeypatch):
    import tools.vision as v

    image = _fake_image()
    monkeypatch.setattr(
        v, "get_model_gateway",
        lambda: _FakeGW(err="refused", fc=FailureClass.NETWORK.value),
    )
    try:
        out = v.vision_analyze(image, model="llava:7b")
    finally:
        os.unlink(image)
    assert out["success"] is False
    assert "Ollama not running" in out["error"]


def test_vision_available_models_contract(monkeypatch):
    import tools.vision as v

    monkeypatch.setattr(
        v, "get_model_gateway",
        lambda: _FakeGW(models=["llava:7b", "llama3.1:8b", "bakllava:7b"]),
    )
    out = v.vision_available_models()
    assert "llava:7b" in out["vision_models"]
    assert "bakllava:7b" in out["vision_models"]
    assert "llama3.1:8b" in out["all_models"]


class _FakeGW:
    def __init__(self, content: "str | None" = None, err: "str | None" = None,
                 fc: "str | None" = None, models: "list[str] | None" = None):
        self.content = content
        self.err = err
        self.fc = fc
        self.models = models or []

    def vision_complete(self, image_base64, prompt, *, model="llava:7b",
                        provider="ollama", **kw):
        if self.err:
            raise ModelGatewayError(self.err, failure_class=self.fc,
                                    provider="ollama", model=model)
        return self.content

    def vision_models(self, base_url=None):
        return self.models
