"""Tests for the full wiring of the architecture upgrades into the live agent
loop, tool registry, and gateway.

Offline: uses mock/mock and an isolated HERMUS_HOME. Run:
  python tests/test_integration.py   (or pytest tests/test_integration.py)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

_TMP = tempfile.mkdtemp(prefix="hermus_int_")
os.environ["HERMUS_HOME"] = _TMP

from core.config import config  # noqa: E402

config.model = "mock/mock"
# Isolate memory stores so agent tests don't pollute the shared repo data/*.db
config.memory_db_path = str(Path(_TMP) / "memory.db")
config.memory2_db_path = str(Path(_TMP) / "memory2.db")
config.trajectory_path = str(Path(_TMP) / "trajectories.jsonl")
config.user_model_path = str(Path(_TMP) / "user_model.json")
config.embeddings_db_path = str(Path(_TMP) / "embeddings.db")


# --------------------------------------------------------------------------
# Tool registry: permission enforcement + new tools
# --------------------------------------------------------------------------
def test_permission_enforcement_in_registry():
    from core.tool_registry import tool_registry
    from core.permissions import permission_manager

    # ASK tool runs (audited) under default ask_policy=allow
    r = tool_registry.execute("shell_execute", {"command": "echo hermus_ok", "timeout": 5})
    assert r.get("success") is True or "hermus_ok" in str(r.get("stdout", ""))

    # DENY tool is blocked
    denied = tool_registry.execute("credential_access", {})
    assert "Permission DENIED" in denied.get("error", "")

    # audit log recorded both
    tools_seen = [e["tool"] for e in permission_manager.recent(limit=50)]
    assert "shell_execute" in tools_seen


def test_architecture_tools_registered():
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    names = tool_registry.list_tools()["tools"]
    for expected in ("research_deep", "memory2_recall", "memory2_remember",
                     "router_choose", "workspace_list_projects",
                     "screen_record_start", "screen_record_status", "screen_record_save",
                     "screen_analyze", "screen_verify", "screen_action_before",
                     "screen_action_after", "screen_watch"):
        assert expected in names, f"{expected} not registered"

    # screen status works headless
    st = tool_registry.execute("screen_record_status", {})
    assert "running" in st

    # memory2 recall + remember roundtrip
    tool_registry.execute("memory2_remember", {"kind": "semantic", "content": "integration test fact"})
    rec = tool_registry.execute("memory2_recall", {"query": "integration test fact"})
    assert rec.get("count", 0) >= 1

    # router_choose falls back gracefully
    sel = tool_registry.execute("router_choose", {"text": "hello"})
    assert sel.get("model")


# --------------------------------------------------------------------------
# Agent loop wiring
# --------------------------------------------------------------------------
def test_agent_memory2_persist_and_recall():
    from core.agent import HermusAgent
    from core.memory2 import memory2

    agent = HermusAgent(model="mock/mock")
    agent.project = "testproj"
    res = agent.chat("remember that I prefer pytest over unittest")
    assert "response" in res
    assert res["project"] == "testproj"

    # episodic + semantic auto-persisted
    mems = memory2.store.all(project="testproj")
    kinds = {m["kind"] for m in mems}
    assert "episodic" in kinds
    assert "semantic" in kinds


def test_agent_router_skips_mock_and_profiles():
    from core.agent import HermusAgent
    from core.profiles import profile_manager

    # router skipped for mock provider (no crash, no swap)
    agent = HermusAgent(model="mock/mock")
    assert agent._apply_router("write some code") is None

    # profile persona injected into the system prompt
    profile_manager.create("reviewer_test", persona="You are a strict code reviewer.")
    config.profile = "reviewer_test"
    try:
        a2 = HermusAgent(model="mock/mock")
        prompt = a2._build_system_prompt("review this")
        assert "strict code reviewer" in prompt.lower()
        assert "reviewer_test" in prompt
    finally:
        config.profile = ""


def test_agent_autonomous_loop():
    from core.agent import HermusAgent

    agent = HermusAgent(model="mock/mock")
    report = agent.autonomous("summarize the plan", max_repairs=1)
    assert report["status"] in ("done", "failed")
    assert report["phases"][0] == "understand"
    assert report["phases"][-1] == "finish"


# --------------------------------------------------------------------------
# Gateway endpoints
# --------------------------------------------------------------------------
def test_gateway_endpoints():
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    client = TestClient(app)

    # workspace
    r = client.post("/workspace/create", json={"name": "gwtest", "description": "d"})
    assert r.status_code == 200 and r.json().get("success")

    # memory2
    r = client.post("/memory2/remember", json={"kind": "semantic", "content": "gw memory"})
    assert r.status_code == 200 and r.json().get("success")
    r = client.post("/memory2/recall", json={"query": "gw memory"})
    assert r.status_code == 200 and len(r.json().get("results", [])) >= 1

    # permissions
    r = client.post("/permissions/check", json={"tool": "shell_execute"})
    assert r.status_code == 200 and r.json()["decision"] in ("allow", "ask")
    r = client.get("/permissions/log")
    assert r.status_code == 200 and isinstance(r.json().get("log"), list)

    # research (offline: empty search → graceful)
    r = client.post("/research", json={"query": "test"})
    assert r.status_code == 200 and "answer" in r.json()

    # router
    r = client.post("/router/select", json={"text": "write code"})
    assert r.status_code == 200 and r.json().get("task_type")

    # watchdog
    r = client.post("/watchdog/handle", json={"error": "JSONDecodeError: line 3"})
    assert r.status_code == 200 and r.json().get("known") is True

    # agents
    r = client.post("/agents/create", json={"name": "gwagent", "role": "generic"})
    assert r.status_code == 200 and r.json().get("success")
    r = client.get("/agents")
    assert r.status_code == 200 and any(a.get("name") == "gwagent" for a in r.json().get("agents", []))

    # profiles
    r = client.post("/profiles/create", json={"name": "gwprofile"})
    assert r.status_code == 200 and r.json().get("success")

    # screen status
    r = client.get("/screen/status")
    assert r.status_code == 200 and "running" in r.json()


# --------------------------------------------------------------------------
# Computer Agent dashboard API
# --------------------------------------------------------------------------
def test_computer_dashboard_endpoints():
    """The /computer/* endpoints the dashboard uses must be available and
    return a coherent live view even with an empty task store."""
    from fastapi.testclient import TestClient

    from gateway.gateway import app

    client = TestClient(app)

    # status / tasks / world degrade gracefully (empty store → null task)
    r = client.get("/computer/status")
    assert r.status_code == 200
    body = r.json()
    assert "halted" in body and "current_task" in body
    assert body["current_task"] is None or isinstance(body["current_task"], dict)
    assert isinstance(body["repair_stats"], dict)
    assert isinstance(body["recent_events"], list)

    r = client.get("/computer/tasks")
    assert r.status_code == 200 and "tasks" in r.json() and "stats" in r.json()

    r = client.get("/computer/world")
    assert r.status_code == 200 and (r.json()["world"] is None or isinstance(r.json()["world"], dict))

    r = client.get("/computer/repairs")
    assert r.status_code == 200 and isinstance(r.json()["total"], int)

    r = client.get("/computer/skills")
    assert r.status_code == 200 and "skills" in r.json() and "stats" in r.json()

    # emergency stop / release round-trip
    r = client.post("/computer/stop", json={"reason": "integration test"})
    assert r.status_code == 200 and r.json().get("halted") is True
    assert client.get("/computer/status").json()["halted"] is True
    r = client.post("/computer/release")
    assert r.status_code == 200 and r.json().get("halted") is False

    # unknown resources return 404 JSON, not server errors
    for path in ("/computer/task/nope", "/computer/plan/nope",
                 "/computer/repairs/nope", "/computer/recording/nope"):
        assert client.get(path).status_code == 404
    assert client.get("/computer/recording/nope/video").status_code == 404

    # websocket stream is reachable and delivers a snapshot
    from core.computer.events import publish

    # Pre-existing history must arrive in the snapshot only - never replayed
    # afterwards as fake "live" activity on every page load / reconnect.
    stale = publish("action_completed", {"task_id": "integration-stale", "ok": True})
    with client.websocket_connect("/computer/events") as ws:
        first = ws.receive_json()
        assert first["kind"] == "snapshot" and isinstance(first["events"], list)
        assert any(e.get("id") == stale["id"] for e in first["events"])

        sent = publish("screen_event", {"task_id": "integration-live", "stage": "after_action"})
        live = ws.receive_json()
        assert live["id"] == sent["id"] and live["type"] == "screen_event"

        # the only frame after the snapshot was the new event, not the backlog
        fresh = publish("task_completed", {"task_id": "integration-live"})
        nxt = ws.receive_json()
        assert nxt["id"] == fresh["id"], f"stale event replayed as live: {nxt}"


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
