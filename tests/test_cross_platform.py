"""Cross-platform behaviour: encoded output, undefined names, provider filters.

These pin the three defects that only showed up when the suite ran somewhere
other than Linux CI: a redirected stdout on Windows is cp1252, so one status
glyph crashed ``hermus doctor`` mid-output; seven agent factories called
``uuid`` without importing it; and the local-Ollama workers were appended even
when the caller named the providers it wanted.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def test_configure_stdio_turns_an_unencodable_glyph_into_output(tmp_path):
    """The doctor crash: cp1252 stdout + "✅" raised UnicodeEncodeError."""
    from core.stdio import configure_stdio

    path = tmp_path / "out.txt"
    stream = open(path, "w", encoding="cp1252")
    try:
        configure_stdio(stream)
        stream.write("✅ model ready\n")
    finally:
        stream.close()

    assert "model ready" in path.read_text(encoding="cp1252")


def test_configure_stdio_leaves_plain_encodable_output_alone(tmp_path):
    from core.stdio import configure_stdio

    path = tmp_path / "out.txt"
    stream = open(path, "w", encoding="cp1252")
    try:
        configure_stdio(stream)
        stream.write("hermus doctor: 12 checks\n")
    finally:
        stream.close()

    assert path.read_text(encoding="cp1252") == "hermus doctor: 12 checks\n"


@pytest.mark.parametrize(
    "factory",
    [
        "create_researcher",
        "create_coder",
        "create_verifier",
        "create_chair",
        "create_critic",
        "create_synthesizer",
        "create_tool_runner",
    ],
)
def test_agent_factories_build_a_name_without_a_name_error(factory):
    """Each factory built its default name from an unimported ``uuid``."""
    from core.agents import specialization

    first = asyncio.run(getattr(specialization, factory)())
    second = asyncio.run(getattr(specialization, factory)())

    assert first.name != second.name, "a factory must not hand out one shared name"


def test_auto_start_ollama_waits_for_the_server_it_started(monkeypatch):
    """The wait loop called an unimported ``asyncio``, and its own
    ``except Exception`` hid the NameError behind a silent ``False``."""
    from core import local_first

    monkeypatch.setattr(local_first.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(local_first.subprocess, "Popen", lambda *a, **k: None)

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(local_first.asyncio, "sleep", fake_sleep)

    provider = local_first.LocalFirstProvider()
    monkeypatch.setattr(provider, "_check_provider_health", lambda *a, **k: False)

    assert asyncio.run(provider.auto_start_ollama()) is False
    assert slept, "the started server must be waited on, not abandoned"


class _StubKeyManager:
    """One configured key, so the fleet has a worker to hand work to."""

    def __init__(self, provider: str = "custom"):
        self.provider = provider

    def get_all_entries(self):
        return [
            {
                "provider": self.provider,
                "key": "sk-stub-00000001",
                "base_url": "http://127.0.0.1:9/v1",
                "default_model": "m",
                "models": ["m"],
                "name": f"{self.provider}-key",
            }
        ]

    def get_key_bundle(self, provider):
        entries = [e for e in self.get_all_entries() if e["provider"] == provider]
        return entries[0] if entries else None


@pytest.fixture
def reachable_ollama(monkeypatch):
    """Pretend a local Ollama is up with two models."""
    import core.model_fleet as fleet

    monkeypatch.setattr(
        "core.openai_compat.list_models",
        lambda provider, api_key="": {"success": True, "models": [{"id": "tiny:1b"}, {"id": "small:3b"}]},
    )
    monkeypatch.setattr(fleet, "multi_key_manager", _StubKeyManager())
    return fleet


def test_explicit_provider_request_excludes_ollama_workers(reachable_ollama):
    workers = reachable_ollama._available_workers(providers=["custom"], limit=3)

    assert workers, "the requested provider's worker must be available"
    assert {w["provider"] for w in workers} == {"custom"}


def test_no_provider_request_still_offers_ollama(reachable_ollama):
    workers = reachable_ollama._available_workers(limit=8)

    assert "ollama" in {w["provider"] for w in workers}
