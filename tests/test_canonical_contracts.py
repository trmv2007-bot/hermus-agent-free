"""Contract tests for the canonical HERMUS rebuild (Rebuild spec §8, §9, §11, §12, §14).

These prove the consolidated canonical contracts, the single Tool/Model gateways,
the unified event bus, the canonical memory/world-state facades and their
migrations, and the bootstrap capability report — without depending on any live
provider, browser, or external service.
"""

from __future__ import annotations

import importlib
import json
import os

import pytest

# Ensure core is importable.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def test_contracts_package_all_exports():
    from core.contracts import (EventEnvelope, Command, ToolDescriptor, ToolResult,
                                Evidence, MissionNode, ModelRequirement, ModelSelection,
                                ModelGatewayResult, Job, FailureClass, Capability,
                                redact, CommandStatus, EventType)
    assert ToolDescriptor.__name__ == "ToolDescriptor"
    assert MissionNode.__name__ == "MissionNode"


def test_event_envelope_defaults_and_roundtrip():
    from core.contracts import EventEnvelope
    e = EventEnvelope()
    assert e.command_id is None
    assert e.status == "pending"
    d = e.to_dict()
    e2 = EventEnvelope.from_dict(d)
    assert e2.session_id == e.session_id
    assert e2.event_id == e.event_id


def test_command_redacts_secrets():
    from core.contracts import Command, EventType
    c = Command(command="provider.set", args={"api_key": "secret", "model": "qwen"},
                idempotency_key="ui-7f1")
    env = c.to_envelope(type=EventType.COMMAND_REQUESTED.value)
    assert env.args_redacted["api_key"] == "<redacted>"
    assert env.args_redacted["model"] == "qwen"
    assert env.idempotency_key == "ui-7f1"


def test_redact_recursive_and_off():
    from core.contracts import redact
    assert redact({"a": {"token": "x"}, "b": [1]}) == {"a": {"token": "<redacted>"}, "b": [1]}
    assert redact({"key": "keep"}, enabled=False) == {"key": "keep"}


def test_tool_result_shape():
    from core.contracts import ToolResult, ToolStatus
    r = ToolResult.error("TIMEOUT", "slow", retryable=True)
    assert r.ok is False
    assert r.status == ToolStatus.ERROR.value
    assert r.retryable is True
    d = r.to_dict()
    assert d["error_code"] == "TIMEOUT"
    ok = ToolResult.ok_result("out", evidence_refs=["e1"], changed_resources=["a.txt"])
    assert ok.ok is True
    assert ok.evidence_refs == ["e1"]


def test_mission_node_and_job():
    from core.contracts import MissionNode, Job
    n = MissionNode(id="n1", goal="make tests pass", expected_output_type="execution")
    assert n.expected_output_type == "execution"
    assert n.state == "CREATED"
    j = Job(id="j1", type="mission")
    assert j.status == "queued"
    j2 = Job.from_dict(j.to_dict())
    assert j2.id == "j1"


def test_model_requirement_and_result():
    from core.contracts import ModelRequirement, ModelGatewayResult, Capability
    req = ModelRequirement(task="code", capabilities=["tooling"], tools=True, vision=False)
    assert req.requires_tools() is True
    res = ModelGatewayResult(provider="openrouter", model="qwen", ok=False,
                             failure_class="rate_limit", retryable=True)
    assert res.ok is False
    assert res.retryable is True


def test_contracts_failure_class_enum():
    from core.contracts import FailureClass, Capability
    assert FailureClass.RATE_LIMIT.value == "rate_limit"
    assert Capability.TOOLING.value == "tooling"


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------
def test_event_bus_publish_subscribe_replay(tmp_path):
    from core.events import EventBus
    from core.contracts import EventEnvelope, EventType
    bus = EventBus(tmp_path / "events.jsonl")
    seen = []
    bus.subscribe(EventType.COMMAND_STARTED.value)(seen.append)

    bus.publish(EventEnvelope(type=EventType.COMMAND_REQUESTED.value, command="c"))
    bus.publish(EventEnvelope(type=EventType.COMMAND_STARTED.value, command="c"))
    assert seen and seen[0].command == "c"

    # Replay survives a new bus instance (durable log).
    bus2 = EventBus(tmp_path / "events.jsonl")
    rp = bus2.replay(since_cursor=0)
    assert len(rp) == 2
    assert len(bus2.replay(since_cursor=2)) == 0


def test_legacy_event_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMUS_EVENTS_DIR", str(tmp_path))
    from core.events import EventBus, publish_legacy
    from core.events.bus import configure_bus, get_bus
    bus = configure_bus(tmp_path / "events.jsonl", reset=True)
    env = publish_legacy("run.started", {"command": "mission.resume", "status": "running"},
                         source="dashboard")
    assert env.source == "dashboard"
    recent = get_bus().recent(10)
    assert any(e.command == "mission.resume" for e in recent)


def test_dashboard_events_bridge_to_canonical_bus(tmp_path):
    """Dashboard events (dict API) are bridged onto the one canonical EventBus."""
    from core.events.bus import configure_bus, get_bus
    bus = configure_bus(tmp_path / "e.jsonl", reset=True)
    from core.dashboard_events import dashboard_event_bus
    dashboard_event_bus.publish("session_started", {"run_id": "r1", "label": "fix build"})
    dashboard_event_bus.publish("tool_call", {"tool": "shell", "args": "pytest"})
    # The dict API keeps working for realtime/speech consumers.
    recent = dashboard_event_bus.recent(10)
    assert recent[0]["type"] == "tool_call"
    assert recent[0]["data"]["tool"] == "shell"
    # And every event is also on the canonical durable bus.
    canon = get_bus().snapshot()
    assert any(c.command == "session_started" for c in canon)
    assert any(c.command == "tool_call" for c in canon)


# ---------------------------------------------------------------------------
# Tool gateway
# ---------------------------------------------------------------------------
def _stub_registry(name_to_fn):
    """A registry stub with the same ``execute``/``executors`` shape as ToolRegistry."""
    class Reg:
        def __init__(self):
            self.executors = dict(name_to_fn)

        def execute(self, name, args=None):
            args = args or {}
            fn = self.executors.get(name)
            if fn is None:
                return {"error": f"Unknown tool {name}", "available_sample": []}
            out = fn(**args)
            if isinstance(out, dict) and out.get("error"):
                return out
            return out if isinstance(out, dict) else {"ok": True, "output": out}

        def list_tools(self):
            return {"tools": sorted(self.executors.keys()), "catalog": []}
    return Reg()


def test_tool_gateway_success_error_missing(tmp_path):
    from core.tools import ToolGateway
    from core.events import EventBus
    reg = _stub_registry({"mock_add": lambda a, b: {"result": a + b}})
    bus = EventBus(tmp_path / "e.jsonl")
    gw = ToolGateway(reg, bus=bus)
    ok = gw.execute("mock_add", {"a": 2, "b": 3})
    assert ok.ok is True
    assert ok.output == {"result": 5}
    assert ok.duration_ms is not None
    # unknown tool -> typed error, never raises
    missing = gw.execute("nope")
    assert missing.ok is False
    assert missing.error_code == "TOOL_NOT_FOUND"
    # events recorded on the bus
    types = [e.type for e in bus.snapshot()]
    assert "command.started" in types
    assert "command.succeeded" in types


def test_tool_gateway_timeout_and_policy(tmp_path):
    from core.tools import ToolGateway
    from core.events import EventBus
    reg = _stub_registry({"boom": lambda: (_ for _ in ()).throw(TimeoutError("slow"))})
    bus = EventBus(tmp_path / "e.jsonl")
    gw = ToolGateway(reg, bus=bus)
    r = gw.execute("boom")
    assert r.ok is False
    assert r.retryable is True

    # policy returns an explicit deny for this tool
    gw2 = ToolGateway(reg, bus=bus, policy=lambda n, a: "deny" if n == "boom" else "allow")
    blocked = gw2.execute("boom")
    assert blocked.error_code == "POLICY_DENIED"


def test_tool_gateway_real_registry(tmp_path):
    from core.tools import ToolGateway
    from core.events import EventBus
    from core.tool_registry import tool_registry
    tool_registry.load()
    bus = EventBus(tmp_path / "e.jsonl")
    gw = ToolGateway(tool_registry, bus=bus)
    assert len(gw.available()) > 0
    d = gw.describe("file_read")
    assert d.name == "file_read"


# ---------------------------------------------------------------------------
# Model gateway
# ---------------------------------------------------------------------------
class _FakeResolver:
    def __init__(self):
        self.bundle = {"provider": "openrouter", "default_model": "qwen2.5-coder",
                       "supports_tools": True, "base_url": "https://x", "free": True}

    def select_usable_bundle(self, require_tools=False, prefer=None):
        return self.bundle

    def list_available_providers(self, probe=False):
        return [{"id": "openrouter", "configured": True}]

    def discover_runtime_bundles(self, include_local=True):
        return [self.bundle]


def test_model_gateway_select_and_classify(tmp_path):
    from core.models import ModelGateway
    from core.contracts import ModelRequirement, FailureClass
    gw = ModelGateway()
    gw._resolver = _FakeResolver()
    gw._probe_capability = lambda model, provider: _report()
    gw._router_candidate = lambda req: None
    gw._find_vision_candidate = lambda req: None
    sel = gw.select(ModelRequirement(task="code", capabilities=["tooling"], tools=True))
    assert sel and sel[0].provider == "openrouter"
    assert sel[0].tool_capable is True
    assert sel[0].score > 0

    # failure classification maps to typed classes with proper retryability
    out = gw.complete(provider="m", model="x", _complete=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 429 rate limit")))
    assert out.failure_class == FailureClass.RATE_LIMIT.value
    assert out.retryable is True

    # hard reliability overrides: network errors are classified + retryable
    out2 = gw.complete(provider="m", model="x", _complete=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("refused")))
    assert out2.failure_class == FailureClass.NETWORK.value
    assert out2.retryable is True


def _report():
    return {"tools": True, "vision": False, "capabilities": ["chat", "code"],
            "context_window": 32000, "reasoning": True}


# ---------------------------------------------------------------------------
# Memory facade + migration
# ---------------------------------------------------------------------------
def test_memory_facade_and_migration(tmp_path):
    from core.memory import MemoryFacade, migrate_legacy, detect_legacy
    from core.memory2 import Memory2
    from core.compat.legacy_memory import Memory as V1

    m = MemoryFacade(Memory2(db_path=str(tmp_path / "m2.db")))
    assert m.remember("semantic", "the api uses qwen", project="p1")["success"] is True
    assert len(m.recall("api", project="p1")) >= 1
    assert len(m.hybrid_recall("api", project="p1")) >= 1

    v1path = str(tmp_path / "legacy.db")
    v1 = V1(db_path=v1path)
    v1.curate_memory("project", "legacy fact", source_session="s1", importance=7)
    assert detect_legacy(v1path) is True
    res = migrate_legacy(v1path, facade=m)
    assert res.get("success") is True
    # idempotent
    res2 = migrate_legacy(v1path, facade=m)
    assert res2.get("skipped") is True


def test_memory_facade_is_single_writable_owner(tmp_path):
    """The facade is the ONLY writable path and delivers the full feature union."""
    from core.memory import MemoryFacade
    from core.memory2 import Memory2
    from core.compat.legacy_memory import Memory as V1

    v1 = V1(db_path=str(tmp_path / "sess.db"))
    m = MemoryFacade(Memory2(db_path=str(tmp_path / "m2.db")), v1_store=v1)

    # typed memory
    assert m.remember("semantic", "the plan uses pytest", project="p")["success"] is True
    assert m.recall("pytest", project="p")
    # session history
    m.add_session_message("s1", "user", "fix the failing test")
    assert m.search_sessions("failing", limit=5)
    assert m.summarize_search_results("failing", m.search_sessions("failing", limit=2))
    # curated memory
    m.curate_memory("key", "value", importance=5)
    assert m.get_curated_memory(limit=5)
    # user model
    m.update_user_model({"preferences": {"skin": "dark"}})
    assert m.load_user_model()["preferences"]["skin"] == "dark"
    # token usage
    m.add_token_usage("s1", {"prompt_tokens": 2, "completion_tokens": 2})
    assert m.get_token_usage("s1", limit=5)["count"] >= 1
    assert m.db_path  # the backend db path is exposed
    # nudges
    assert isinstance(m.periodic_nudges(), list)


def test_legacy_memory_singleton_is_facade():
    # `from core.memory import memory` is now the canonical facade (single owner).
    from core.memory import memory, get_memory
    assert type(memory).__name__ == "MemoryFacade"
    assert memory is get_memory()
    # facade exposes the union of typed + v1 methods
    for meth in ("remember", "recall", "add_session_message", "search_sessions",
                 "curate_memory", "get_curated_memory", "load_user_model",
                 "update_user_model", "get_token_usage", "periodic_nudges"):
        assert callable(getattr(memory, meth)), meth


# ---------------------------------------------------------------------------
# World-state facade + migration
# ---------------------------------------------------------------------------
def test_world_state_facade_and_migration(tmp_path):
    from core.state import WorldStateFacade, migrate_world_state, detect_legacy
    ws = WorldStateFacade()
    ws.begin_task("open browser", "PLANNING")
    assert ws.task_state == "PLANNING"

    # full V1-canonical surface is exposed through ONE facade (no second writer)
    ws.update({"active_application": "chrome", "visible_targets": ["button"]})
    assert ws.active_application == "chrome"
    assert "button" in ws.visible_targets
    ws.before_action("EXECUTING", {"click": "button"})
    ws.mark_state("clicked", True)
    assert ws.current_state == "clicked"
    ws.finish_task(True)
    assert ws.task_state == "SUCCESS"
    d = ws.to_dict()
    assert d["active_application"] == "chrome"

    legacy_path = tmp_path / "world.json"
    legacy_path.write_text(json.dumps({"active_application": "chrome", "current_state": "IDLE"}))
    assert detect_legacy(str(legacy_path)) is True
    res = migrate_world_state(str(legacy_path), out_path=str(tmp_path / "out.json"))
    assert res.get("success") is True


def test_world_state_v2_duplicate_removed():
    # The dead parallel WorldStateV2 implementation was removed; V1 is canonical.
    with pytest.raises(ImportError):
        importlib.import_module("core.computer.world_state_v2")
    from core.computer import WorldState
    from core.state import get_world_state
    assert get_world_state().canonical == "v1"


# ---------------------------------------------------------------------------
# Bootstrap capability report
# ---------------------------------------------------------------------------
def test_bootstrap_doctor_reports(monkeypatch):
    import bootstrap as boot
    # Ensure required modules report ready in the test venv.
    report = boot.doctor()
    assert "capabilities" in report
    assert "system.python" in report["capabilities"]
    # No fabrication: missing optional deps report "unavailable".
    caps = report["capabilities"]
    for k, v in caps.items():
        assert v["status"] in ("ready", "capable", "unavailable", "missing", "present")


def test_canonical_packages_import_cleanly():
    for mod in ("core.contracts", "core.events", "core.tools", "core.models",
                "core.memory", "core.state", "core.compat", "core.learning",
                "bootstrap"):
        importlib.import_module(mod)


# ---------------------------------------------------------------------------
# Learning facade (evidence-gated promotion, §16)
# ---------------------------------------------------------------------------
class _FakeSkillCandidate:
    def __init__(self, name="demo"):
        self.name = name


class _FakeForge:
    """Stub SkillForge that records observed successes and installs."""
    def __init__(self):
        self.observations = 0
        self.installed = []

    def observed_successes(self, goal, tool_names):
        return self.observations

    def install(self, candidate, validate=True):
        self.installed.append(candidate.name)
        return {"success": True, "installed": True}

    def success_ledger(self):
        return {"sessions": {}}

    def _registry(self):
        return {"demo_skill": {"name": "demo_skill"}}

    def _save_registry(self, reg):
        self._saved_reg = reg


def test_learning_gate_requires_repeated_verified_success():
    from core.learning import LearningFacade
    forge = _FakeForge()
    lr = LearningFacade(forge, min_successes=3)
    # single run -> observed but NOT promoted
    forge.observations = 1
    gate = lr.can_promote("build", ["shell"])
    assert gate["promotable"] is False
    res = lr.promote("build", ["shell"], candidate=_FakeSkillCandidate())
    assert res["promoted"] is False
    assert res["reason"] == "need_more_verified_successes"
    assert forge.installed == []  # never installed from a single run

    # enough verified successes -> promoted
    forge.observations = 3
    assert lr.can_promote("build", ["shell"])["promotable"] is True
    res2 = lr.promote("build", ["shell"], candidate=_FakeSkillCandidate())
    assert res2["promoted"] is True
    assert forge.installed == ["demo"]


def test_learning_quarantine():
    from core.learning import LearningFacade
    forge = _FakeForge()
    lr = LearningFacade(forge, min_successes=2)
    out = lr.quarantine("demo_skill", reason="repeated_failure")
    assert out["quarantined"] is True
    assert getattr(forge, "_saved_reg", {}).get("demo_skill", {}).get("quarantined") is True
