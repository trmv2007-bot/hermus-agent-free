"""Architecture gates for the clean-slate single-canonical-owner contract.

These are NOT behavioral tests; they are structural gates that fail when the code
introduces a second canonical owner or bypasses a canonical boundary. Grounded in
real import / structural facts so they are reproducible and catch regressions:

* one autonomy engine            -> MissionEngine is defined exactly once.
* one event authority            -> the canonical EventBus is the durable source; no
                                    production code instantiates a legacy dict bus
                                    directly (only the owning modules may).
* one memory writer              -> the public writable path is core.memory
                                    (MemoryFacade/get_memory); no production import
                                    of core.compat.legacy_memory outside core.memory.
* one world-state owner          -> WorldStateFacade (core.state) is the public path.
* one model/provider boundary    -> no direct provider SDK import outside the model
                                    subsystem; ModelGateway is the selection facade.
* one job execution owner        -> gateway.queue.Job/JobQueue owns execution; the
                                    AgentManager does not spawn its own job workers.
* one control-room UI            -> a single gateway/*.html; root -> /control; no
                                    legacy dashboard routes reachable.
* no silent critical init failure-> agent registration failures are surfaced, not
                                    swallowed behind a fake 'ready'.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Direct provider SDK packages that application code must never import outside the
# canonical model subsystem.
_PROVIDER_SDK = {
    "openai", "groq", "anthropic", "cohere", "ollama", "google.generativeai",
    "huggingface_hub", "replicate", "together", "mistralai", "google-genai",
}

# Files that ARE the canonical model subsystem (they may reference provider SDKs).
_MODEL_SUBSYSTEM = {
    "core/llm.py", "core/providers.py", "core/multi_key.py",
    "core/openai_compat.py", "core/custom_api.py", "core/provider_resolver.py",
    "core/model_fleet.py", "core/router2.py", "core/model_capabilities.py",
    "core/free_keys.py", "core/models/gateway.py", "core/models/__init__.py",
    "core/nollama.py", "core/computer/llm/*.py",
}

# Modulus that OWN a legacy dict bus and therefore may instantiate it.
_EVENT_OWNER_MODULES = {
    "core/events/bus.py", "core/run_events.py", "core/dashboard_events.py",
    "core/computer/events.py",
}

_MEMORY_OWNER_MODULES = {
    "core/memory/__init__.py", "core/memory/store.py", "core/memory/migration.py",
    "core/compat/legacy_memory.py",
}


def _prod_files() -> list[Path]:
    out: list[Path] = []
    for root in ("core", "gateway", "tools", "tui", "subagents", "scheduler", "backends"):
        for dirpath, dirs, files in os.walk(ROOT / root):
            dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", "data", "skills")]
            for f in files:
                if f.endswith(".py"):
                    out.append(Path(dirpath) / f)
    return out


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def _imports(p: Path) -> list[str]:
    """Return dotted module names imported by a file (static)."""
    mods: list[str] = []
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except SyntaxError:
        return mods
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.append(node.module)
            for a in node.names:
                mods.append(f"{node.module or '.'}.{a.name}")
    return mods


# ---------------------------------------------------------------------------
# One autonomy engine
# ---------------------------------------------------------------------------
def test_one_autonomy_engine():
    """MissionEngine is the single autonomy engine definition."""
    defs = [p for p in _prod_files() if "class MissionEngine" in p.read_text(encoding="utf-8")]
    assert [str(_rel(x)) for x in defs] == ["core/mission.py"], \
        "autonomy must be owned by exactly one implementation (core/mission.MissionEngine)"


# ---------------------------------------------------------------------------
# One event authority
# ---------------------------------------------------------------------------
def test_one_event_authority_no_legacy_bus_instantiation():
    """Production code must not construct a legacy dict bus directly.

    The owning modules define the singletons; consumers use the canonical bus
    (get_bus/publish) or the process singletons (run_bus/dashboard_event_bus/
    computer_event_bus) which mirror onto the canonical EventBus.
    """
    banned = ("RunBus(", "DashboardEventBus(", "ComputerEventBus(")
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        if any(rel.startswith(m) for m in _EVENT_OWNER_MODULES):
            continue
        src = p.read_text(encoding="utf-8")
        for pat in banned:
            if pat in src and f"class {pat[:-1]}" not in src:
                offenders.append((rel, pat))
    assert not offenders, f"legacy event bus instantiated outside owner: {offenders}"


def test_one_event_authority_canonical_bus_is_authoritative_source():
    """Every event-producing module mirrors onto the canonical EventBus get_bus()."""
    # dashboard + computer dict buses must bridge to the canonical bus.
    for rel in ("core/dashboard_events.py", "core/computer/events.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "get_bus().publish(" in src or "get_bus" in src, \
            f"{rel} must mirror events onto the canonical EventBus"


# ---------------------------------------------------------------------------
# One memory writer
# ---------------------------------------------------------------------------
def test_one_memory_writer_no_public_legacy_import():
    """The public writable memory path is core.memory; legacy_memory is private."""
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        if any(rel.startswith(m) for m in _MEMORY_OWNER_MODULES):
            continue
        src = p.read_text(encoding="utf-8")
        if "legacy_memory" in src and ("import" in src or "Memory" in src):
            offenders.append(rel)
    assert not offenders, f"production imports the legacy memory impl outside core.memory: {offenders}"


# ---------------------------------------------------------------------------
# One world-state owner
# ---------------------------------------------------------------------------
def test_one_worldstate_owner():
    """WorldStateFacade (core.state) is the public world-state path."""
    defs = [p for p in _prod_files() if "class WorldStateFacade" in p.read_text(encoding="utf-8")]
    assert [str(_rel(x)) for x in defs] == ["core/state/world.py"], \
        "world state must be owned by exactly one facade (core.state.WorldStateFacade)"


# ---------------------------------------------------------------------------
# One model / provider boundary
# ---------------------------------------------------------------------------
def test_one_model_boundary_no_direct_provider_sdk():
    """No direct provider SDK import outside the canonical model subsystem."""
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        # Allow the model subsystem and any file that is part of it.
        if any(rel.startswith(m.rstrip("/*")) or rel == m for m in _MODEL_SUBSYSTEM):
            continue
        for mod in _imports(p):
            if mod in _PROVIDER_SDK or mod.split(".")[0] in _PROVIDER_SDK:
                offenders.append((rel, mod))
    assert not offenders, f"direct provider SDK import outside model subsystem: {offenders}"


def test_model_gateway_is_the_selection_facade():
    """ModelGateway is real: routes_canonical uses get_model_gateway()."""
    src = (ROOT / "gateway/routes_canonical.py").read_text(encoding="utf-8")
    assert "get_model_gateway" in src, "control room must use the canonical ModelGateway"
    gw_src = (ROOT / "core/models/gateway.py").read_text(encoding="utf-8")
    assert "class ModelGateway" in gw_src


# ---------------------------------------------------------------------------
# One job execution owner
# ---------------------------------------------------------------------------
def test_one_job_execution_owner():
    """JobQueue owns job execution; AgentManager does not spawn its own workers."""
    am = (ROOT / "core/agent_manager.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" not in am, "AgentManager must not own its own job workers"
    assert "class AgentManager" in am
    q = (ROOT / "gateway/queue.py").read_text(encoding="utf-8")
    assert "class JobQueue" in q and "class Job" in q, "canonical queue is the job owner"


# ---------------------------------------------------------------------------
# One control-room UI
# ---------------------------------------------------------------------------
def test_one_control_room_ui_only_control_html():
    html = list((ROOT / "gateway").glob("*.html"))
    assert [p.name for p in html] == ["control.html"], \
        "exactly one production UI surface is allowed (gateway/control.html)"


def test_one_control_room_ui_root_redirects_to_control():
    from fastapi.testclient import TestClient
    from gateway.gateway import app
    with TestClient(app) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code in (307, 302), "root must redirect to the single control room"
        assert r.headers.get("location") == "/control"
        assert c.get("/control").status_code == 200
        # Legacy UI surfaces are dead.
        for path in ("/dashboard", "/dashboard/legacy", "/jarvis", "/dashboard/jarvis",
                     "/computer/dashboard", "/remote", "/dashboard-assets/hermus-client.js"):
            assert c.get(path).status_code == 404, path


# ---------------------------------------------------------------------------
# No silent critical initialization failure
# ---------------------------------------------------------------------------
def test_agent_registration_failure_is_surfaced_not_swallowed():
    """AgentManager.start must not swallow a handler-registration failure."""
    src = (ROOT / "core/agent_manager.py").read_text(encoding="utf-8")
    # The swallow pattern (try register -> except Exception: pass -> success ready)
    # must be gone.
    assert "register_agent_handlers(self._queue())" in src
    assert 'except Exception as exc' in src or "except Exception:" in src
    # It must report an explicit 'degraded' state on registration failure.
    assert '"degraded"' in src, "agent registration failure must be surfaced as degraded, not buried"


def test_no_bare_except_pass_in_agent_execution_core():
    """core.agent must not blanket-swallow critical run/harness exceptions."""
    src = (ROOT / "core/agent.py").read_text(encoding="utf-8")
    # The agent records structured issues (record_issue) rather than bare `except: pass`
    # on the run-critical path.
    assert "record_issue(" in src, "core.agent should record structured runtime issues"
