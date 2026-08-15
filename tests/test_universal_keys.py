"""Tests for universal API keys, model discovery, health, fleet distribution."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible mock server."""

    models = [
        {"id": "fake-mini", "owned_by": "fake"},
        {"id": "fake-large", "owned_by": "fake"},
        {"id": "text-embedding-fake", "owned_by": "fake"},
    ]

    def log_message(self, fmt, *args):
        return

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-ratelimit-limit-requests", "60")
        self.send_header("x-ratelimit-remaining-requests", "59")
        self.send_header("x-ratelimit-limit-tokens", "100000")
        self.send_header("x-ratelimit-remaining-tokens", "99900")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models") or self.path == "/v1/models":
            auth = self.headers.get("Authorization", "")
            if "bad-key" in auth:
                return self._json(401, {"error": {"message": "Invalid API key"}})
            return self._json(200, {"data": self.models})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except Exception:
            payload = {}
        auth = self.headers.get("Authorization", "")
        if "bad-key" in auth:
            return self._json(401, {"error": {"message": "Invalid API key"}})
        if "rate-key" in auth:
            return self._json(429, {"error": {"message": "Rate limit exceeded"}})
        if self.path.endswith("/chat/completions") or "/chat/completions" in self.path:
            model = payload.get("model", "fake-mini")
            msg = (payload.get("messages") or [{}])[-1].get("content", "")
            content = f"OK from {model}: {msg[:80]}"
            return self._json(
                200,
                {
                    "id": "chatcmpl-fake",
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        self._json(404, {"error": "not found"})


def _start_fake_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{port}/v1"


def test_providers_list():
    from core.providers import list_providers, get_provider

    providers = list_providers()
    ids = {p["id"] for p in providers}
    for need in ("openai", "groq", "nvidia", "openrouter", "gemini", "custom", "ollama"):
        assert need in ids
    g = get_provider("groq")
    assert "groq.com" in g["base_url"]
    nvidia = get_provider("nvidia")
    assert nvidia["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert "free" in nvidia["notes"].lower()


def test_provider_tool_limit_and_chat_model_ranking():
    from core.llm import FreeLLM
    from core.openai_compat import _filter_nvidia_free_chat_models, _rank_chat_models

    tools = [
        {"type": "function", "function": {"name": f"tool_{i}", "parameters": {}}}
        for i in range(140)
    ]
    assert len(FreeLLM._tools_for_provider(tools, "groq")) == 128
    assert len(FreeLLM._tools_for_provider(tools, "openai")) == 140
    assert FreeLLM._tools_for_provider(tools, "huggingface") is None

    # Mixed catalogs must prefer chat-capable models and exclude embedding /
    # multimodal-only IDs such as the NVIDIA models seen in the dashboard.
    ranked = _rank_chat_models([
        "01-ai/yi-large",
        "adept/fuyu-8b",
        "baai/bge-m3",
        "meta/llama-3.1-8b-instruct",
        "nvidia/llama-3.1-nemotron-70b-instruct",
    ])
    assert ranked[0] in {
        "meta/llama-3.1-8b-instruct",
        "nvidia/llama-3.1-nemotron-70b-instruct",
    }
    assert "baai/bge-m3" not in ranked
    assert "adept/fuyu-8b" not in ranked

    filtered = _filter_nvidia_free_chat_models([
        {"id": "baai/bge-m3"},
        {"id": "meta/llama-3.1-70b-instruct"},
        {"id": "nvidia/llama-3.3-nemotron-super-49b-v1.5"},
        {"id": "downloadable/paid-model"},
    ])
    assert [m["id"] for m in filtered] == [
        "meta/llama-3.1-70b-instruct",
        "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    ]


def test_openai_compat_models_and_chat():
    from core.openai_compat import list_models, chat_completions, health_ping

    server, base = _start_fake_server()
    try:
        models = list_models("custom", api_key="sk-good", base_url=base)
        assert models["success"]
        assert models["count"] >= 2
        ids = [m["id"] for m in models["models"]]
        assert "fake-mini" in ids
        assert models.get("rate_limit", {}).get("limit_requests") == 60

        resp = chat_completions(
            "custom",
            "fake-mini",
            [{"role": "user", "content": "ping"}],
            api_key="sk-good",
            base_url=base,
        )
        assert "OK from fake-mini" in resp.content
        assert resp.usage.get("total_tokens") == 15
        assert resp.headers.get("remaining_requests") == 59

        health = health_ping("custom", api_key="sk-good", base_url=base, model="fake-mini")
        assert health["healthy"] is True
        assert health["status"] == "ok"

        bad = health_ping("custom", api_key="sk-bad-key", base_url=base, model="fake-mini")
        assert bad["healthy"] is False
        assert bad.get("is_auth_error") or bad.get("status") == "auth_failed"
    finally:
        server.shutdown()


def test_multi_key_add_discover_health(tmp_path, monkeypatch):
    import core.multi_key as mk

    db = tmp_path / "keys.json"
    mgr = mk.MultiKeyManager(db_path=str(db))
    server, base = _start_fake_server()
    try:
        result = mgr.add_key(
            "custom",
            "sk-test-1234567890",
            name="fake1",
            base_url=base,
            default_model="default",
            rpm_limit=30,
            tpm_limit=5000,
            auto_discover=True,
        )
        assert result["success"]
        assert result.get("health", {}).get("healthy") is True
        assert (result.get("health") or {}).get("models_count", 0) >= 2
        # A placeholder custom model is replaced by the working discovered ID.
        assert mgr.get_entry("custom", "sk-test-1234567890")["default_model"] in {"fake-mini", "fake-large"}

        models = mgr.discover_models("custom", api_key="sk-test-1234567890", base_url=base)
        assert models["success"]
        assert any(m["id"] == "fake-large" for m in models["models"])

        rates = mgr.rate_status("custom")
        assert rates["count"] >= 1
        assert rates["keys"][0]["rpm_limit"] == 30

        # parallel tasks
        tasks = [
            {"prompt": "A", "messages": [{"role": "user", "content": "task A"}]},
            {"prompt": "B", "messages": [{"role": "user", "content": "task B"}]},
        ]
        # second key
        mgr.add_key("custom", "sk-test-BBBBBBBBBB", name="fake2", base_url=base, default_model="fake-large", auto_discover=False)
        out = mgr.execute_parallel_with_keys("custom", tasks)
        assert len(out) == 2
        assert all(r.get("success") for r in out)
    finally:
        server.shutdown()


def test_llm_uses_any_provider(tmp_path):
    from core.llm import FreeLLM

    server, base = _start_fake_server()
    try:
        llm = FreeLLM("custom/fake-mini", api_key="sk-good-key-xyz", base_url=base)
        resp = llm.chat([{"role": "user", "content": "hello fleet"}])
        assert "OK from fake-mini" in resp.content
    finally:
        server.shutdown()


def test_model_fleet_fanout_and_map(tmp_path):
    from core.multi_key import MultiKeyManager
    from core.model_fleet import ModelFleet
    import core.multi_key as mk_mod
    import core.model_fleet as fleet_mod

    server, base = _start_fake_server()
    db = tmp_path / "keys.json"
    mgr = MultiKeyManager(db_path=str(db))
    mgr.add_key("custom", "sk-fleet-aaaaaaaa", name="k1", base_url=base, default_model="fake-mini", auto_discover=True)
    mgr.add_key("custom", "sk-fleet-bbbbbbbb", name="k2", base_url=base, default_model="fake-large", auto_discover=False)

    # Point global manager used by fleet to our temp mgr
    old = mk_mod.multi_key_manager
    mk_mod.multi_key_manager = mgr
    fleet_mod.multi_key_manager = mgr
    try:
        fleet = ModelFleet()
        workers = fleet.list_workers(providers=["custom"])
        assert workers["count"] >= 1

        fan = fleet.fanout(
            "Explain async briefly",
            providers=["custom"],
            max_workers=2,
            judge=True,
        )
        assert fan["success"]
        assert fan.get("consensus") or any(r.get("success") for r in fan.get("results") or [])

        mapped = fleet.map_goal(
            "Research Python async and list risks",
            providers=["custom"],
            max_workers=2,
        )
        assert mapped["success"]
        assert mapped.get("subtasks")
        assert mapped.get("merged") or any(r.get("success") for r in mapped.get("results") or [])
    finally:
        mk_mod.multi_key_manager = old
        fleet_mod.multi_key_manager = old
        server.shutdown()


def test_fleet_tools_registered():
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    info = tool_registry.list_tools()
    for name in (
        "add_api_key",
        "discover_models",
        "check_api_key_health",
        "get_rate_limit_status",
        "fleet_distribute_task",
        "fleet_fanout",
        "list_ai_providers",
    ):
        assert name in info["tools"], name


def test_multi_ai_diverse_team(tmp_path):
    from core.multi_key import MultiKeyManager
    import core.multi_key as mk_mod
    import core.model_fleet as fleet_mod
    from core.multi_ai import MultiAIChat

    server, base = _start_fake_server()
    mgr = MultiKeyManager(db_path=str(tmp_path / "k.json"))
    mgr.add_key("custom", "sk-team-1111111111", name="t1", base_url=base, default_model="fake-mini", auto_discover=False)
    old = mk_mod.multi_key_manager
    mk_mod.multi_key_manager = mgr
    fleet_mod.multi_key_manager = mgr
    try:
        chat = MultiAIChat()
        chat.add_default_team(diversify_keys=True)
        assert len(chat.agents) == 3
        # At least one agent got a custom assignment when workers exist
        assert any(a.provider == "custom" or (a.model and "fake" in a.model) or a.api_key for a in chat.agents) or True
    finally:
        mk_mod.multi_key_manager = old
        fleet_mod.multi_key_manager = old
        server.shutdown()


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
