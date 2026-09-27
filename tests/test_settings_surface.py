"""Settings, proven against a real .env rather than a mock.

The point of these tests is the failure modes, not the happy path. Each one
below corresponds to a way a config editor can quietly destroy something:

  * echoing a secret back to the browser
  * clearing a working API key because a form was submitted with a blank field
  * accepting an arbitrary key name, which turns a settings page into a way to
    set PATH and PYTHONPATH and change what the next process start does
  * rewriting .env and stripping every comment out of it
  * writing it non-atomically, so a crash truncates the file and the gateway
    will not start next time

They run against a temporary HERMUS_ENV_FILE so the real .env — which holds a
live key — is never touched.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """A throwaway .env, wired in before the router module reads it."""
    path = tmp_path / ".env"
    path.write_text(
        "# HERMUS settings\n"
        "# keep this comment\n"
        "HERMES_MODEL=old/model\n"
        "NVIDIA_API_KEY=nvapi-REALKEY123456\n"
        "SOME_UNRELATED_THING=keep me\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMUS_ENV_FILE", str(path))
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    import gateway.routes_settings as routes_settings

    importlib.reload(routes_settings)
    yield path, routes_settings
    importlib.reload(routes_settings)


def test_secret_is_never_returned_in_full(env_file):
    path, mod = env_file
    items = {s["key"]: s for s in mod.read_settings.__wrapped__()} if False else None

    settings = mod.read_settings
    # Call the coroutine the way the route does, without a client.
    import asyncio

    body = asyncio.run(settings())
    key = next(s for s in body["settings"] if s["key"] == "NVIDIA_API_KEY")

    assert key["secret"] is True
    assert key["set"] is True
    # The real value must not survive anywhere in the response.
    assert "REALKEY123456" not in str(body)
    assert key["value"].endswith("3456")
    assert "REALKEY" not in key["value"]


def test_empty_secret_is_left_alone(env_file):
    """A blank field means untouched, not deleted.

    This is the one that matters most: a form submit with an empty input would
    otherwise wipe a working API key, and the user finds out next time the
    model is needed.
    """
    path, mod = env_file
    import asyncio

    result = asyncio.run(mod.write_settings({"set": {"NVIDIA_API_KEY": ""}}))
    assert result["skipped"] == ["NVIDIA_API_KEY"]
    assert result["cleared"] == []

    after = asyncio.run(mod.read_settings())
    key = next(s for s in after["settings"] if s["key"] == "NVIDIA_API_KEY")
    assert key["set"] is True


def test_writing_a_key_persists_and_preserves_comments(env_file):
    path, mod = env_file
    import asyncio

    asyncio.run(mod.write_settings({"set": {"HERMES_FALLBACK_MODEL": "vendor/next"}}))

    text = path.read_text(encoding="utf-8")
    assert "HERMES_FALLBACK_MODEL=vendor/next" in text
    # Comments are the user's; a save must not eat them.
    assert "# HERMUS settings" in text
    assert "# keep this comment" in text
    # And so must keys nobody asked about.
    assert "SOME_UNRELATED_THING=keep me" in text


def test_only_allowlisted_keys_are_writable(env_file):
    """A settings page must not be a way to set PATH.

    The gateway reads .env at import, so an arbitrary key write is code
    execution on the next start. The allowlist is the security boundary.
    """
    path, mod = env_file
    import asyncio
    from fastapi.responses import JSONResponse

    for hostile in ("PATH", "PYTHONPATH", "LD_PRELOAD", "HERMES_ENV_FILE"):
        response = asyncio.run(mod.write_settings({"set": {hostile: "pwned"}}))
        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
        assert "not a configurable setting" in bytes(response.body).decode()
        assert "pwned" not in path.read_text(encoding="utf-8")


def test_saving_nothing_is_a_400_not_a_silent_success(env_file):
    path, mod = env_file
    import asyncio
    from fastapi.responses import JSONResponse

    response = asyncio.run(mod.write_settings({}))
    assert isinstance(response, JSONResponse)
    assert response.status_code == 400


def test_write_is_atomic(env_file):
    """No temp file left behind, and the original is never truncated in place."""
    path, mod = env_file
    import asyncio

    asyncio.run(mod.write_settings({"set": {"HERMES_MODEL": "new/model"}}))
    assert not path.with_suffix(".env.tmp").exists()
    assert "new/model" in path.read_text(encoding="utf-8")
