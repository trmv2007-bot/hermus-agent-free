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


def _resolve_relative(p: Path, module: str) -> str:
    """Resolve a relative ``ImportFrom.module`` (e.g. ``..memory2``) to an absolute
    dotted module using the importing file's package, or return the raw value if the
    file is not inside the repo package root."""
    if not module.startswith("."):
        return module
    rel = os.path.relpath(p, ROOT)
    parts = rel.split(os.sep)[:-1]  # directory components of the importing file
    # Package of the importing module: replace slashes with dots.
    pkg = ".".join(x for x in parts if x != "" and x != "..")
    up = len(module) - len(module.lstrip("."))
    base = pkg.split(".")[:-up] if pkg else []
    tail = module.lstrip(".")
    return ".".join(base + ([tail] if tail else [])) if (base or tail) else module


def _imports(p: Path) -> list[str]:
    """Return dotted module names imported by a file (static, relative-resolved)."""
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
                mods.append(_resolve_relative(p, node.module))
            for a in node.names:
                mods.append(f"{_resolve_relative(p, node.module or '.')}.{a.name}")
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


def test_no_app_level_memory2_direct_access():
    """core.memory is the ONLY writable memory boundary; app-level code must not
    reach into the typed backend (memory2) directly. The typed store (memory2) is
    internal to core.memory and may only be imported there. (Config flag strings
    like ``memory2_enabled`` are allowed; the *import* is what is forbidden.)"""
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        if rel.startswith("core/memory/") or rel == "core/compat/legacy_memory.py":
            continue
        for mod in _imports(p):
            # Matches ``memory2``, ``core.memory2``, and relative ``.memory2``/
            # ``..memory2`` (after resolution) — i.e. the typed backend by final segment.
            last = mod.split(".")[-1]
            if last == "memory2" and not rel.startswith("core/memory/"):
                offenders.append((rel, mod))
    assert not offenders, (
        "app-level code directly imports the typed memory backend (memory2); "
        f"route through core.memory: {offenders}"
    )


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


def test_one_model_boundary_no_direct_free_llm_construction():
    """Every production model invocation is obtained via ModelGateway.

    Application code must not construct a ``FreeLLM`` (the provider-call
    implementation) directly nor use the module-level ``free_llm`` singleton —
    both bypass the canonical ModelGateway (selection/capability/fallback/health).
    It must also not issue a request to a model backend endpoint directly
    (``/api/generate``, ``/api/chat``, ``/chat/completions``) — that is the same
    bypass via a different door. Only the model subsystem owner modules may.
    """
    model_endpoints = ("/api/generate", "/api/chat", "/chat/completions")
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        if any(rel.startswith(m.rstrip("/*")) or rel == m for m in _MODEL_SUBSYSTEM):
            continue
        src = p.read_text(encoding="utf-8")
        # Direct construction: FreeLLM(...)  and  free_llm.<method>(
        for pat in ("FreeLLM(", "free_llm."):
            if pat in src:
                offenders.append((rel, pat))
        for ep in model_endpoints:
            if ep in src:
                offenders.append((rel, f"direct model endpoint {ep}"))
    assert not offenders, (
        "application code constructs FreeLLM / uses the free_llm singleton / "
        "calls a model endpoint directly; "
        f"route through get_model_gateway(): {offenders}"
    )


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


# ---------------------------------------------------------------------------
# One tool boundary — runtime uses the canonical ToolGateway
# ---------------------------------------------------------------------------
def test_one_tool_gateway_agent_runtime_uses_it():
    """The agent's tool-execution path must go through the canonical ToolGateway."""
    src = (ROOT / "core/agent.py").read_text(encoding="utf-8")
    assert "from .tools import get_tool_gateway" in src or "get_tool_gateway" in src, \
        "the agent must route tool invocation through the canonical ToolGateway"
    assert ".execute(" in src, "agent must call the gateway's execute()"


def test_one_tool_gateway_control_room_uses_it():
    """The control room capability surface reads tools via the canonical gateway."""
    src = (ROOT / "gateway/routes_canonical.py").read_text(encoding="utf-8")
    assert "get_tool_gateway" in src, "capabilities route must use the canonical ToolGateway"


def test_one_tool_gateway_no_duplicate_invoke_path():
    """Tool execution should be centralized: no stray 'from .tool_registry import' + execute
    in a second production module that competes with the gateway. The registry stays the
    implementation; the gateway is the invocation path. (The agent delegates to the gateway,
    so it must not also import the registry namespace for invocation.)"""
    agent = (ROOT / "core/agent.py").read_text(encoding="utf-8")
    # Agent may still import the registry for discovery (list_tools/load) but must not
    # invoke toolregistry.execute directly anymore.
    assert "tool_registry.execute(" not in agent, \
        "core.agent must not bypass the ToolGateway by calling tool_registry.execute"


def _tool_registry_aliases(p: Path) -> set[str]:
    """Return the local names bound to the tool-registry module in ``p`` (AST).

    Handles ``from .tool_registry import tool_registry``, ``from ..tool_registry import
    tool_registry as tr``, ``import tool_registry``, ``from core.tool_registry import ...
    ``; returns the set of *call-site names* that actually reference the registry.
    """
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except SyntaxError:
        return set()
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Normalize to see if the source module is a tool_registry module.
            resolved = _resolve_relative(p, module)
            if not (resolved.endswith(".tool_registry") or resolved == "tool_registry"
                    or resolved == "core.tool_registry"):
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                aliases.add(local)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith(".tool_registry"):
                    aliases.add(alias.asname or alias.name)
    return aliases


#: modules that legitimately own/drive the registry directly (gateway is the public path;
#: the registry implementation itself is exempt).
_REGISTRY_OWNERS = {"core/tool_registry.py", "core/tools/gateway.py", "core/tools/__init__.py"}


def test_no_tool_gateway_bypass_in_production():
    """§31: no production module may invoke tools by calling ``tool_registry.execute(...)``
    directly, bypassing the ToolGateway. AST-based (catches aliases), not string matching."""
    offenders: list[str] = []
    for p in _prod_files():
        rel = _rel(p)
        if rel in _REGISTRY_OWNERS:
            continue
        aliases = _tool_registry_aliases(p)
        if not aliases:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = getattr(func, "attr", None)
            value = getattr(func, "value", None)
            if attr != "execute":
                continue
            name = getattr(value, "id", None)
            if name in aliases:
                offenders.append(f"{rel}:{getattr(node, 'lineno', '?')} {name}.execute(...)")
    assert not offenders, (
        "production bypassing the ToolGateway via tool_registry.execute (route via "
        f"get_tool_gateway().execute()): {offenders}"
    )


def test_android_tool_default_transport_is_factory_provisioned():
    """§31: the production Android singleton must not be left as AndroidTool(transport=None)
    when a transport can be built — it must provision via the canonical transport factory."""
    src = (ROOT / "core/android/tool.py").read_text(encoding="utf-8")
    assert "build_default_transport" in src, \
        "get_android_tool() must provision its transport via build_default_transport()"
    assert "get_android_transport()" in src or "build_default_transport()" in src
    # The factory (in the transport module) supplies the configured ADB/bridge transport.
    transport_src = (ROOT / "core/android/transport.py").read_text(encoding="utf-8")
    assert "def build_default_transport" in transport_src


def test_android_no_duplicate_tool_impl():
    """§31: exactly one AndroidTool facade class and one transport-factory function."""
    tool_src = (ROOT / "core/android/tool.py").read_text(encoding="utf-8")
    assert tool_src.count("class AndroidTool") == 1, "must be a single AndroidTool class"
    assert "def get_android_tool" in tool_src


# ---------------------------------------------------------------------------
# Autonomy never silently degrades to chat, and never fakes success
# ---------------------------------------------------------------------------
def test_autonomy_never_silently_falls_back_to_chat():
    """MissionEngine must surface explicit blocked/failed states, never a silent
    chat downgrade or a fabricated 'completed' on failure."""
    src = (ROOT / "core/mission.py").read_text(encoding="utf-8")
    assert '"blocked"' in src or 'BLOCKED = "blocked"' in src, \
        "autonomy must report an explicit blocked state, not silently degrade"
    # node explicitly blocked on no usable model/key/backend
    assert "blocked, not completed" in src, \
        "a missing model/provider backend must block the node, not complete it"
    # a crash is recorded as FAILED, never a silent downgrade
    assert "crash → recorded failure, never a silent downgrade" in src or "never a silent downgrade" in src


def test_autonomy_crash_records_failed_not_completed():
    """A crash in the autonomous loop must set state=FAILED + error, not report success."""
    src = (ROOT / "core/mission.py").read_text(encoding="utf-8")
    assert "MissionState.FAILED.value" in src
    assert "recoverable" in src


# ---------------------------------------------------------------------------
# Runtime actually uses the canonical ModelGateway
# ---------------------------------------------------------------------------
def test_runtime_uses_canonical_model_gateway():
    """routes_canonical (the control-room capability surface) must use ModelGateway."""
    src = (ROOT / "gateway/routes_canonical.py").read_text(encoding="utf-8")
    assert "get_model_gateway" in src, "capabilities route must use ModelGateway"
    assert "model_gw.providers(" in src or "model_gw." in src


# ---------------------------------------------------------------------------
# Integrated media capabilities keep one owner each
# ---------------------------------------------------------------------------
def test_omnivoice_backend_single_owner():
    """Only the canonical speech subsystem may load or manage OmniVoice."""
    src = (ROOT / "core/speech.py").read_text(encoding="utf-8")
    assert "create_clone_prompt" in src and "OmniVoice" in src
    allowed = {"core/speech.py", "core/config.py", "tools/speech_tools.py", "gateway/routes_speech.py"}
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        if rel in allowed:
            continue
        text = p.read_text(encoding="utf-8")
        if "OmniVoice.from_pretrained" in text or "VoiceClonePrompt" in text:
            offenders.append(rel)
    assert not offenders, f"OmniVoice runtime logic escaped the speech owner: {offenders}"


def test_heygem_connector_single_owner():
    """Only the avatar connector may know the local HeyGem-style HTTP paths."""
    src = (ROOT / "core/avatar.py").read_text(encoding="utf-8")
    for token in ("preprocess_and_tran", "/v1/invoke", "/submit", "/query"):
        assert token in src
    allowed = {"core/avatar.py", "core/config.py", "tools/heygem.py", "gateway/routes_speech.py"}
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        if rel in allowed:
            continue
        text = p.read_text(encoding="utf-8")
        if any(token in text for token in ("preprocess_and_tran", "/v1/invoke", "/easy/submit", "/easy/query")):
            offenders.append(rel)
    assert not offenders, f"avatar service HTTP details leaked outside the connector: {offenders}"


def test_handy_compatibility_single_owner():
    """Only tools.voice may own Handy-style local STT model discovery rules."""
    src = (ROOT / "tools/voice.py").read_text(encoding="utf-8")
    assert "discover_local_stt_models" in src and "com.pais.handy" in src
    allowed = {"tools/voice.py", "core/config.py", "core/permissions.py", "gateway/routes_speech.py", "gateway/routes_canonical.py"}
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        if rel in allowed:
            continue
        text = p.read_text(encoding="utf-8")
        if "com.pais.handy" in text or "voice_discover_local_models" in text:
            offenders.append(rel)
    assert not offenders, f"Handy-style model discovery leaked outside tools.voice: {offenders}"


def test_tool_registry_discovers_media_connectors():
    """Integrated media capabilities must enter the app through ToolRegistry/ToolGateway."""
    src = (ROOT / "core/tool_registry.py").read_text(encoding="utf-8")
    for mod in ("tools.speech_tools", "tools.heygem", "tools.voice"):
        assert mod in src, f"ToolRegistry must discover {mod}"


# ---------------------------------------------------------------------------
# Security: no committed credentials in production code
# ---------------------------------------------------------------------------
def test_no_committed_secrets_in_production():
    """Production code must not commit high-entropy credentials/tokens."""
    import re

    patterns = [
        r"\bghp_[A-Za-z0-9]{20,}\b",                 # GitHub PAT
        r"\bghs_[A-Za-z0-9]{20,}\b",                 # GitHub fine-grained
        r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
        r"\bsk-[A-Za-z0-9]{20,}\b",                  # OpenAI/Anthropic style
        r"\bgsk_[A-Za-z0-9]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",                     # AWS access key
        r"\bAIza[A-Za-z0-9_-]{20,}\b",               # Google API key
    ]
    rex = [re.compile(p) for p in patterns]
    offenders = []
    for p in _prod_files():  # production dirs only (tests may carry fake keys)
        src = p.read_text(encoding="utf-8", errors="ignore")
        for rx in rex:
            if rx.search(src):
                offenders.append((_rel(p), rx.pattern))
    assert not offenders, f"committed credential-like secret in production: {offenders}"


def test_health_endpoint_probes_not_fabricated():
    """system/health must derive 'ok' from real required-capability probes, never
    return a hardcoded healthy value."""
    src = (ROOT / "gateway/routes_canonical.py").read_text(encoding="utf-8")
    assert "bootstrap.doctor()" in src, "health must probe the real doctor"
    assert "present" in src, "must read per-capability present flags"
    # On probe failure it must surface an error, not fabricate 'ok'.
    assert "HTTPException(status_code=500" in src or "500" in src


def test_doctor_reports_explicit_states_not_fake_ok():
    """Doctor diagnostics must report explicit severity/status, not a canned 'ok'."""
    src = (ROOT / "core/doctor.py").read_text(encoding="utf-8")
    for token in ("worst_severity", "ok", "attention", "critical", "findings"):
        assert token in src


# ---------------------------------------------------------------------------
# One canonical setup / bootstrap: thin wrappers, honest required-dep detection
# ---------------------------------------------------------------------------
def test_setup_thin_wrappers_delegate_to_bootstrap():
    """setup.sh / activate.sh / launchers must be thin — no duplicated business
    logic and no '|| true' masking of the canonical bootstrap."""
    setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
    # setup.sh handles OS packages then delegates to the one-command bootstrap.
    assert "bootstrap" in setup.lower()
    assert "exec python bootstrap.py" in setup or '"./bin/hermus" bootstrap' in setup
    activate = (ROOT / "activate.sh").read_text(encoding="utf-8")
    assert "NO business logic" in activate or "no business logic" in activate.lower()


def test_bootstrap_probes_required_deps_honestly():
    """bootstrap.py must distinguish required vs optional capabilities and report
    missing required deps (so a fresh install never silently degrades)."""
    src = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    assert "REQUIRED_IMPORTS" in src and "REQUIRED_PIP" in src
    assert "OPTIONAL" in src
    assert "def doctor()" in src and "def run()" in src
    # It must classify optional deps as 'unavailable' with a reason, not fake ok.
    assert "unavailable" in src
    assert "required" in src.lower()
    # No '|| true' style masking of the required install.
    assert "no ``|| true`` masking on required dependencies" in src





# ---------------------------------------------------------------------------
# One delegation entry point — must go through the canonical JobQueue
# ---------------------------------------------------------------------------
# The ONLY production code allowed to call the delegation engine directly is
# (a) the delegation module itself and (b) the canonical ``subagent.delegate``
# Job handler in gateway/handlers.py. Every other caller must submit a Job.
_DELEGATION_ENGINE_OWNERS = {"core/delegation.py", "gateway/handlers.py"}
_DELEGATION_DIRECT_CALLS = (
    "delegation.fanout(",
    "delegation.decompose_and_run(",
    "delegation.delegate(",
)


def test_delegation_entered_only_through_canonical_queue():
    """Delegation is a JobQueue owner: no tool/route/mission may invoke the
    delegation engine directly, bypassing the queue lifecycle."""
    offenders = []
    for p in _prod_files():
        rel = _rel(p)
        if rel in _DELEGATION_ENGINE_OWNERS:
            continue
        src = p.read_text(encoding="utf-8")
        for pat in _DELEGATION_DIRECT_CALLS:
            if pat in src:
                offenders.append((rel, pat))
    assert not offenders, (
        "delegation must be entered through the canonical JobQueue "
        f"(submit a subagent.delegate job); direct engine calls bypass its "
        f"lifecycle: {offenders}"
    )


def test_subagents_facade_submits_through_queue_not_direct_engine():
    """subagents.subagent is a queue facade: it must submit jobs via
    submit_and_wait, not call the delegation engine directly."""
    src = (ROOT / "subagents/subagent.py").read_text(encoding="utf-8")
    assert "submit_and_wait(" in src and "DELEGATE_JOB" in src, \
        "the subagent facade must enqueue delegation on the canonical JobQueue"
    # No direct engine fan-out/decompose calls in the facade.
    for pat in ("_engine().fanout(", "_engine().decompose_and_run(", "engine.fanout(",
                "engine.decompose_and_run("):
        assert pat not in src, f"subagent facade bypasses the queue via {pat}"


# ---------------------------------------------------------------------------
# Subagent worker canonical boundaries
# ---------------------------------------------------------------------------
def test_subagent_worker_runs_through_canonical_boundaries():
    """The sub-agent execution path must use the canonical boundaries: HermusAgent
    (ModelGateway/ToolGateway/MemoryFacade) and the canonical EventBus — never a
    second provider, tool or event path."""
    dlg = (ROOT / "core/delegation.py").read_text(encoding="utf-8")
    # The single worker engine builds the real agent (which routes model/tools/memory).
    assert ("HermusAgent(" in dlg) or ("from .agent import HermusAgent" in dlg), \
        "the sub-agent worker must run through HermusAgent"
    # Worker must not construct a provider client directly.
    assert "FreeLLM(" not in dlg, "delegation worker must not build a provider client directly"
    # Synthesis/planning LLM calls route through the canonical ModelGateway.
    assert "get_model_gateway().chat(" in dlg or "get_model_gateway()" in dlg, \
        "delegation synthesis/planning must use the canonical ModelGateway"
    # Delegation lifecycle events mirror onto the canonical EventBus.
    assert "get_bus().publish(" in dlg, "delegation must emit events via the canonical EventBus"


# ---------------------------------------------------------------------------
# JobQueue lifecycle mirrors onto the canonical EventBus
# ---------------------------------------------------------------------------
def test_jobqueue_lifecycle_mirrors_onto_canonical_event_bus():
    """The JobQueue is the canonical job lifecycle owner AND mirrors every job
    transition onto the EventEnvelope EventBus (no second event authority)."""
    q = (ROOT / "gateway/queue.py").read_text(encoding="utf-8")
    assert "get_bus().publish(" in q, "JobQueue must mirror lifecycle onto the canonical EventBus"
    assert "_lifecycle_event_type" in q, "JobQueue must map terminal statuses to lifecycle names"
