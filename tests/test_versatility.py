"""Tests for multi-step loop, tool registry, MCP, embeddings, channels helpers."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_tool_registry_discovers_tools():
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    info = tool_registry.list_tools()
    assert info["count"] >= 20, f"expected many tools, got {info['count']}"
    # Core tools
    for name in ("web_search", "shell_execute", "file_read", "memory_search", "skill_list"):
        assert name in info["tools"], f"missing {name}"
    # Pentest full map (previously missing from execute branch)
    for name in ("sast_scan", "dast_scan", "bug_bounty_recon", "generate_compliance_report"):
        assert name in info["tools"], f"missing pentest tool {name}"
    # New versatility tools
    for name in (
        "mcp_list_servers",
        "embeddings_status",
        "embeddings_search",
        "public_api_search",
        "public_api_categories",
        "public_api_refresh",
    ):
        assert name in info["tools"], f"missing {name}"


def test_tool_registry_execute_shell():
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    result = tool_registry.execute("shell_execute", {"command": "echo hermus_ok", "timeout": 5})
    assert "hermus_ok" in (result.get("stdout") or result.get("result") or str(result))


def test_embeddings_hash_fallback_and_search():
    from core.embeddings import EmbeddingStore

    with tempfile.TemporaryDirectory() as td:
        store = EmbeddingStore(db_path=str(Path(td) / "e.db"))
        # Force hash backend
        store._backend = "hash"
        store._dim = 256
        r1 = store.add_text(
            "Hermus agent uses Ollama and DuckDuckGo for free tools",
            source="test",
            metadata={"k": 1},
        )
        assert r1["success"]
        r2 = store.add_text(
            "Completely unrelated cooking pasta recipes with tomato",
            source="test",
            metadata={"k": 2},
        )
        assert r2["success"]
        hits = store.search("free Ollama agent tools", limit=2)
        assert hits["count"] >= 1
        # Top hit should be the hermus text
        top = hits["results"][0]["content"]
        assert "Hermus" in top or "Ollama" in top


def test_embeddings_ingest_directory():
    from core.embeddings import EmbeddingStore

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "note.md").write_text("# Project Alpha\nSecret deploy uses blue-green strategy.\n")
        (root / "code.py").write_text("def hello():\n    return 'world'\n")
        store = EmbeddingStore(db_path=str(root / "e.db"))
        store._backend = "hash"
        store._dim = 256
        result = store.ingest_path(str(root), source="proj")
        assert result["success"]
        assert result["chunks"] >= 1
        hits = store.search("blue-green deploy", limit=3)
        assert any("blue-green" in (h.get("content") or "") for h in hits["results"])


def test_mcp_echo_server_end_to_end():
    from core.mcp_client import MCPManager
    from core.config import config

    echo_path = str(config.base_dir / "tools" / "mcp_echo_server.py")
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "mcp.json"
        # Patch manager to use temp config
        mgr = MCPManager()
        mgr._config = []
        # monkeypatch path via writing then reload - simpler: use add_server after patching path
        import core.mcp_client as mc

        original = mc._mcp_config_path
        mc._mcp_config_path = lambda: cfg_path
        try:
            mgr = MCPManager()
            # Replace config with only our echo server (avoid default disabled example)
            mgr._config = []
            mgr.add_server("echo", "python3", args=[echo_path], enabled=True)
            connected = mgr.connect_enabled()
            ok = [r for r in connected["results"] if r.get("name") == "echo"]
            assert ok and ok[0]["success"], connected
            defs, execs = mgr.get_tools_and_executors()
            assert any("echo" in d["function"]["name"] for d in defs)
            # Find echo tool executor
            echo_name = [n for n in execs if n.endswith("_echo")][0]
            out = execs[echo_name](message="hello hermus")
            assert out.get("success"), out
            assert "hello hermus" in (out.get("content") or "")
            add_name = [n for n in execs if n.endswith("_add")][0]
            out2 = execs[add_name](a=2, b=3)
            assert "5" in (out2.get("content") or "")
        finally:
            mc._mcp_config_path = original
            for s in list(mgr.servers.values()):
                s.stop()


def test_skill_use_passes_task_context():
    from core.tool_registry import tool_registry
    from core.skill_manager import skill_manager

    # Use existing web research skill which accepts query=
    tool_registry.load(force=True)
    skills = skill_manager.list_skills()
    names = [s["name"] for s in skills]
    if "01_web_research_agent" not in names:
        # skip soft if skills ignored by install
        return
    result = tool_registry.execute(
        "skill_use",
        {"name": "01_web_research_agent", "task": "quantum computing basics", "query": "quantum computing basics"},
    )
    assert "error" not in result or result.get("result"), result
    # Should not be the bare run() empty path only
    assert result.get("skill") == "01_web_research_agent"


def test_multi_step_agent_with_mock():
    """Mock LLM that requests a tool then finishes — verifies multi-step loop."""
    from core.agent import HermusAgent
    from core.llm import LLMResponse

    agent = HermusAgent(model="mock/mock", max_steps=5)
    calls = {"n": 0}

    def fake_chat(messages, tools=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                "searching",
                tool_calls=[{"name": "shell_execute", "arguments": {"command": "echo step1"}, "id": "1"}],
            )
        if calls["n"] == 2:
            return LLMResponse(
                "again",
                tool_calls=[{"name": "shell_execute", "arguments": {"command": "echo step2"}, "id": "2"}],
            )
        return LLMResponse("Final answer after two tool steps.")

    agent.llm.chat = fake_chat
    result = agent.chat("Please run two shell echoes then summarize")
    assert result["steps"] >= 2
    assert len(result["tool_results"]) == 2
    assert "Final answer" in result["response"]
    tools = [tr["tool"] for tr in result["tool_results"]]
    assert tools == ["shell_execute", "shell_execute"]


def test_telegram_send_without_token_errors_cleanly():
    from gateway import channels

    # Ensure no token
    old = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    try:
        # Clear config token temporarily
        from core.config import config

        prev = config.telegram_bot_token
        config.telegram_bot_token = None
        result = channels.telegram_send_message(123, "hi")
        assert result.get("ok") is False
        assert "TELEGRAM_BOT_TOKEN" in (result.get("error") or "")
        config.telegram_bot_token = prev
    finally:
        if old is not None:
            os.environ["TELEGRAM_BOT_TOKEN"] = old


def test_handle_telegram_update_with_factory():
    from gateway.channels import handle_telegram_update

    class DummyAgent:
        def chat(self, text):
            return {"response": f"echo:{text}", "tool_results": [], "steps": 1}

        def new_session(self):
            return "new"

    # Monkeypatch send so we don't hit network
    import gateway.channels as ch

    sent = {}

    def fake_send(chat_id, text, reply_to_message_id=None, parse_mode=None, token=None):
        sent["chat_id"] = chat_id
        sent["text"] = text
        return {"ok": True, "result": {"message_id": 1}}

    def fake_action(chat_id, action="typing", token=None):
        return {"ok": True}

    old_send, old_action = ch.telegram_send_message, ch.telegram_send_chat_action
    ch.telegram_send_message = fake_send
    ch.telegram_send_chat_action = fake_action
    try:
        update = {
            "message": {
                "message_id": 10,
                "text": "hello hermus",
                "chat": {"id": 42},
                "from": {"id": 99},
            }
        }
        result = handle_telegram_update(update, agent_factory=lambda p, u: DummyAgent())
        assert result.get("ok") is True
        assert sent["chat_id"] == 42
        assert "echo:hello hermus" in sent["text"]
    finally:
        ch.telegram_send_message = old_send
        ch.telegram_send_chat_action = old_action


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
