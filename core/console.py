"""Console manifest — one declarative table for every Hermus subsystem.

The control room renders itself from this module. A row declares what a
subsystem is (``label``/``group``), who really owns it (``source``), how to read
its live state (``probe``), which HTTP endpoints already serve it
(``endpoints``) and what a human may trigger from the browser (``actions``).

Three rules this module exists to enforce:

* **One table, no bespoke panels.** A subsystem that has a CLI verb and a route
  gets a console panel for free; adding a capability is one row, not a new UI
  surface, a new route and a new JavaScript handler.
* **No UI-owned truth.** A panel never caches or invents state. The gateway
  calls the declared owner and returns exactly what it returned.
* **Honest failure.** A probe that raises reports ``unavailable`` with the real
  error text. The console never renders a green cell it did not read.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

MANIFEST_VERSION = "1.0"

GROUPS = (
    ("runtime", "Runtime", "Engine, queue, diagnostics and persistence"),
    ("agents", "Agents & devices", "Autonomy, crews, computers, channels"),
    ("memory", "Memory & learning", "Recall, skills, lessons and evolution"),
    ("models", "Models & keys", "Selection, providers, fleet, routing"),
    ("capabilities", "Capabilities", "Tools, plugins, connectors, web and speech"),
    ("safety", "Safety & recovery", "Red lines, permissions, sandbox, rollback"),
    ("workspace", "Workspace", "Projects, profiles and evaluation"),
)

#: ``id, label, group, source, probe, summary``
#:
#: ``probe`` is ``module:attr.path`` — the attribute chain is resolved and the
#: final callable is called with no arguments. ``@name`` refers to a shaped
#: provider in :data:`_SHAPED` (used where the owner's raw return needs
#: counting or joining). Both forms hit the canonical owner; neither is a
#: re-implementation.
_TABLE = (
    (
        "engine",
        "Local engine",
        "runtime",
        "core/nollama.py",
        "core.nollama:nollama_manager.status",
        "Local model engine health, installed models and downloads",
    ),
    (
        "doctor",
        "Doctor",
        "runtime",
        "core/doctor.py",
        "@doctor",
        "Runtime error triage and stuck run/job reaping",
    ),
    (
        "runtime-issues",
        "Runtime issues",
        "runtime",
        "core/runtime.py",
        "core.run_events:recent_issues",
        "Failures recorded by the canonical runtime",
    ),
    (
        "queue",
        "Job queue",
        "runtime",
        "gateway/queue.py",
        "@queue",
        "Durable job log: queued, running, failed and recent results",
    ),
    (
        "cache",
        "Cache",
        "runtime",
        "core/cache.py",
        "core.cache:get_cache_stats",
        "LLM, memory-search, web and tool-result caches",
    ),
    (
        "database",
        "Databases",
        "runtime",
        "core/db_registry.py",
        "core.db_registry:db_registry.stats",
        "Open SQLite handles, owners and generation counter",
    ),
    (
        "update",
        "Updater",
        "runtime",
        "core/updater.py",
        "core.updater:updater.get_local_commit",
        "Local vs remote commit and pull state",
    ),
    (
        "modes",
        "Modes",
        "runtime",
        "core/modes.py",
        "core.modes:list_modes",
        "Declared operating modes and their tool policy",
    ),
    (
        "missions",
        "Missions",
        "agents",
        "core/mission.py",
        "@missions",
        "Mission engine: plans, DAGs, approvals and progress",
    ),
    (
        "agents",
        "Agent registry",
        "agents",
        "core/agent_manager.py",
        "@agents",
        "Registered agents and their live status",
    ),
    (
        "delegation",
        "Delegation",
        "agents",
        "core/delegation.py",
        "core.delegation:delegation.status",
        "Sub-agent trees, fan-out and aggregation strategies",
    ),
    (
        "crew",
        "Agent crew",
        "agents",
        "core/harness/swarm.py",
        "core.harness.swarm:status",
        "Concurrent worker sessions and their outcomes",
    ),
    (
        "harness",
        "Harness sessions",
        "agents",
        "core/harness/sessions.py",
        "@harness",
        "Turn preparation, compaction and live sessions",
    ),
    (
        "computer",
        "Computer control",
        "agents",
        "core/computer/computer_agent.py",
        "@computer",
        "Desktop control: tasks, pauses and the emergency brake",
    ),
    (
        "android",
        "Android device",
        "agents",
        "core/android/tool.py",
        "@android",
        "Consent-gated device control and granted operations",
    ),
    (
        "channels",
        "Channels",
        "agents",
        "gateway/channels.py",
        "gateway.channels:get_channel_status",
        "Telegram/Discord delivery and channel status",
    ),
    (
        "swe",
        "SWE mode",
        "agents",
        "core/swe_mode.py",
        "@swe",
        "Inspect, edit, build, test and debug workflows",
    ),
    (
        "verifiers",
        "Domain verifiers",
        "agents",
        "core/verifier_registry.py",
        "@verifiers",
        "Registered domains and their verification rules",
    ),
    (
        "memory",
        "Memory",
        "memory",
        "core/memory/__init__.py",
        "@memory",
        "Session history, curated memory, user model and token usage",
    ),
    (
        "memory2",
        "Typed memory",
        "memory",
        "core/memory2.py",
        "@memory2",
        "Semantic, working and procedural recall with scoring",
    ),
    (
        "embeddings",
        "Embeddings",
        "memory",
        "core/embeddings.py",
        "@embeddings",
        "Vector index health and hybrid search",
    ),
    (
        "skills",
        "Skills",
        "memory",
        "core/skill_manager.py",
        "@skills",
        "Reusable skills, health and usage",
    ),
    (
        "forge",
        "Skill forge",
        "memory",
        "core/skill_forge.py",
        "core.skill_forge:skill_forge.stats",
        "Distilled, validated and quarantined generated skills",
    ),
    (
        "learning",
        "Learning",
        "memory",
        "core/learning/facade.py",
        "core.learning.facade:get_learning().ledger",
        "Promotion ledger behind learned skills",
    ),
    (
        "lessons",
        "Lessons",
        "memory",
        "core/reasoning/lessons.py",
        "core.reasoning.lessons:lessons_store.stats",
        "Distilled lessons from failures and corrections",
    ),
    (
        "trajectory",
        "Trajectories",
        "memory",
        "core/trajectory.py",
        "core.trajectory:trajectory_manager.stats",
        "Recorded turn trajectories available for distillation",
    ),
    (
        "evolution",
        "Evolution policy",
        "memory",
        "core/evolution.py",
        "@evolution",
        "Red-line protected files and self-change assessments",
    ),
    (
        "world",
        "World state",
        "memory",
        "core/state/world.py",
        "@world",
        "Persisted situational awareness used by connectors",
    ),
    (
        "models",
        "Model gateway",
        "models",
        "core/models/gateway.py",
        "@models",
        "Selection, health and typed outcomes per model",
    ),
    (
        "providers",
        "Providers",
        "models",
        "core/providers.py",
        "@providers",
        "Configured providers and their availability",
    ),
    (
        "keys",
        "Keys",
        "models",
        "core/multi_key.py",
        "core.multi_key:multi_key_manager.get_all_entries",
        "Multi-key pool health, rates and per-provider models",
    ),
    (
        "fleet",
        "Model fleet",
        "models",
        "core/model_fleet.py",
        "@fleet",
        "Parallel model workers, strategies and races",
    ),
    (
        "router",
        "Router",
        "models",
        "core/router2.py",
        "@router",
        "Task classification and model routing decisions",
    ),
    (
        "custom-apis",
        "Custom APIs",
        "models",
        "core/custom_api.py",
        "@custom_apis",
        "User-registered HTTP APIs exposed as tools",
    ),
    (
        "response-times",
        "Response testing",
        "models",
        "core/response_tester.py",
        "core.response_tester:response_tester.get_stats",
        "Measured latency per key, provider and model",
    ),
    (
        "tools",
        "Tool registry",
        "capabilities",
        "core/tool_registry.py",
        "@tools",
        "Every tool the agent can actually call",
    ),
    (
        "plugins",
        "Plugins",
        "capabilities",
        "core/plugins/__init__.py",
        "@plugins",
        "Installed plugins and their subscription/tool surface",
    ),
    (
        "mcp",
        "MCP servers",
        "capabilities",
        "core/mcp_client.py",
        "@mcp",
        "Model Context Protocol servers and their tools",
    ),
    (
        "connectors",
        "Connectors",
        "capabilities",
        "core/connectors/registry.py",
        "@connectors",
        "Local integration connectors, disabled by default",
    ),
    (
        "public-apis",
        "Public APIs",
        "capabilities",
        "tools/public_apis.py",
        "@public_apis",
        "Free public API catalogue and search",
    ),
    (
        "web",
        "Web acquisition",
        "capabilities",
        "core/web/gateway.py",
        "@web",
        "Crawl/extract/sanitize boundary and backend capability",
    ),
    (
        "research",
        "Research",
        "capabilities",
        "core/research.py",
        "@research",
        "Multi-source research pipeline",
    ),
    (
        "speech",
        "Speech",
        "capabilities",
        "core/speech.py",
        "@speech",
        "Local TTS/STT backends and avatar readiness",
    ),
    (
        "self-improvement",
        "Self improvement",
        "capabilities",
        "core/self_improvement.py",
        "@self_improvement",
        "Idle reflection and how-to-improve research",
    ),
    (
        "critic",
        "Critic",
        "capabilities",
        "core/critic.py",
        "@critic",
        "Code review, security audit and outcome verification",
    ),
    (
        "safety",
        "Safety policy",
        "safety",
        "core/safety_policy.py",
        "@safety",
        "Safety events, reports and autonomy pre-flight",
    ),
    (
        "red-lines",
        "Red lines",
        "safety",
        "RED_LINES.md",
        "@red_lines",
        "The ten non-negotiables the agent refuses to cross",
    ),
    (
        "permissions",
        "Permissions",
        "safety",
        "core/permissions.py",
        "@permissions",
        "Pending approvals, bundles, grants and the decision log",
    ),
    (
        "capabilities",
        "Capability ledger",
        "safety",
        "core/capability_registry.py",
        "@capabilities",
        "Powers held, missing, discovered and activation state",
    ),
    (
        "sandbox",
        "Sandbox",
        "safety",
        "core/sandbox.py",
        "core.sandbox:sandbox.status",
        "Probed isolation backends and recent jailed runs",
    ),
    (
        "local-defense",
        "Local defense",
        "safety",
        "core/local_defense_scanner.py",
        "@local_defense",
        "Read-only suspicious-indicator scans of approved folders",
    ),
    (
        "rollback",
        "Rollback",
        "safety",
        "core/rollback.py",
        "@rollback",
        "Checkpoints and restore points",
    ),
    (
        "artifacts",
        "Artifacts",
        "safety",
        "core/artifact_manager.py",
        "@artifacts",
        "Mission-owned files and export bundles",
    ),
    (
        "watchdog",
        "Watchdog",
        "safety",
        "core/watchdog.py",
        "@watchdog",
        "Known-fix detection for recurring failures",
    ),
    (
        "emergency",
        "Emergency brake",
        "safety",
        "core/computer/control_center.py",
        "@emergency",
        "The global stop that refuses to be overridden",
    ),
    (
        "workspace",
        "Workspace",
        "workspace",
        "core/workspace.py",
        "@workspace",
        "Active project root and workspace policy",
    ),
    (
        "profiles",
        "Profiles",
        "workspace",
        "core/profiles.py",
        "@profiles",
        "Isolated persona memory and system prompts",
    ),
    (
        "eval",
        "Evaluation",
        "workspace",
        "core/reasoning/eval.py",
        "@eval",
        "Benchmark tasks, scores and regressions",
    ),
)

#: ``panel id -> (method, path, ...)`` — the HTTP surface that already serves the
#: panel. The gateway verifies every one of these against its own live route
#: table, so a rename shows up as ``unverified`` instead of a dead button.
_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "engine": (
        "GET /engine/status",
        "GET /engine/models",
        "GET /engine/downloads",
    ),
    "doctor": ("GET /doctor/status", "POST /doctor/run"),
    "runtime-issues": ("GET /runtime/issues",),
    "queue": ("GET /queue/status", "GET /jobs"),
    "cache": ("GET /cache/stats", "POST /cache/clear"),
    "update": ("GET /update/check", "GET /update/local", "GET /update/remote", "POST /update/pull"),
    "missions": ("GET /missions", "POST /missions"),
    "agents": ("GET /agents", "GET /agents/status", "POST /agents/start", "POST /agents/stop"),
    "computer": ("GET /computer/status", "GET /computer/tasks", "POST /computer/run", "POST /computer/stop"),
    "android": (
        "GET /android/capability",
        "GET /android/permissions",
        "POST /android/permissions/grant",
        "POST /android/permissions/revoke",
    ),
    "channels": ("GET /channels/status", "POST /channels/start", "POST /telegram/send"),
    "swe": ("POST /swe/run",),
    "verifiers": ("GET /verifiers/domains", "POST /verifiers/verify"),
    "memory": (
        "GET /memory/stats",
        "GET /memory/access-log",
        "POST /memory/hybrid",
        "POST /memory/reindex",
        "POST /memory/sweep",
        "POST /memory/remember",
    ),
    "memory2": ("POST /memory2/recall", "POST /memory2/remember"),
    "embeddings": ("POST /embeddings/search", "POST /embeddings/ingest"),
    "skills": ("GET /computer/skills",),
    "forge": ("GET /skills/forge/stats", "POST /skills/forge/run", "POST /skills/forge/harvest", "POST /skills/forge/validate"),
    "models": ("GET /models/capabilities",),
    "providers": ("GET /providers", "GET /providers/available"),
    "keys": ("GET /keys/health", "GET /keys/rates", "GET /keys/models"),
    "fleet": ("GET /fleet/workers", "POST /fleet/run"),
    "router": ("POST /router/select",),
    "custom-apis": ("GET /custom-apis/list", "POST /custom-apis/add", "POST /custom-apis/remove"),
    "response-times": ("GET /response-times", "POST /response-times/test"),
    "tools": ("GET /tools",),
    "plugins": ("GET /plugins", "POST /plugins/reload", "POST /plugins/invoke"),
    "mcp": ("GET /mcp/servers", "POST /mcp/connect"),
    "public-apis": ("GET /public-apis/categories", "GET /public-apis/search", "POST /public-apis/refresh"),
    "research": ("POST /research",),
    "speech": ("GET /speech/status", "POST /speech/synthesize", "POST /speech/transcribe"),
    "safety": ("GET /safety/events", "GET /safety/report", "POST /safety/preflight"),
    "red-lines": ("GET /red-lines/policy",),
    "permissions": ("GET /permissions/pending", "GET /permissions/bundles", "GET /permissions/log"),
    "capabilities": ("GET /capabilities/ledger", "GET /capabilities/registry"),
    "sandbox": ("GET /sandbox/status", "POST /sandbox/run"),
    "local-defense": ("GET /local-defense/reports", "POST /local-defense/scan"),
    "rollback": ("GET /rollback/checkpoints", "POST /rollback/checkpoint", "POST /rollback/restore"),
    "artifacts": ("GET /artifacts", "POST /artifacts/export"),
    "watchdog": ("POST /watchdog/handle",),
    "emergency": ("GET /emergency/status", "POST /emergency/stop", "POST /emergency/resume"),
    "workspace": ("GET /workspace", "POST /workspace/create", "POST /workspace/use"),
    "profiles": ("GET /profiles", "POST /profiles/create"),
    "eval": ("GET /eval/summary",),
    # Panels served by the console projection (see gateway/routes_console.py):
    # owners that had a CLI verb but no HTTP surface until the console existed.
    "database": ("GET /api/v1/console/projection/{panel_id}",),
    "delegation": ("GET /api/v1/console/projection/{panel_id}",),
    "crew": ("GET /api/v1/console/projection/{panel_id}",),
    "harness": ("GET /api/v1/console/projection/{panel_id}",),
    "learning": ("GET /api/v1/console/projection/{panel_id}",),
    "lessons": ("GET /api/v1/console/projection/{panel_id}",),
    "trajectory": ("GET /api/v1/console/projection/{panel_id}",),
    "evolution": ("GET /api/v1/console/projection/{panel_id}",),
    "world": ("GET /api/v1/console/projection/{panel_id}",),
    "connectors": ("GET /api/v1/console/projection/{panel_id}",),
    "self-improvement": ("GET /api/v1/console/projection/{panel_id}",),
    "critic": ("GET /api/v1/console/projection/{panel_id}",),
    "modes": ("GET /api/v1/console/projection/{panel_id}",),
    "web": ("GET /api/v1/console/projection/{panel_id}",),
}

#: ``panel id -> ((label, method, path, fields), ...)``. ``fields`` is a tuple of
#: ``name`` or ``name=default`` strings the browser renders as inputs. Actions are
#: executed by the browser against the real endpoint — the console adds no
#: privileged path of its own.
_ACTIONS: dict[str, tuple[tuple[str, str, str, tuple[str, ...]], ...]] = {
    "engine": (
        ("Refresh", "POST", "/engine/refresh", ()),
        ("Install engine", "POST", "/engine/nollama/install", ()),
        ("Download model", "POST", "/engine/models/download", ("model=minicpm",)),
    ),
    "doctor": (
        ("Run diagnostics", "POST", "/doctor/run", ()),
        ("Run self-repair", "POST", "/doctor/run", ("self_repair=true",)),
    ),
    "queue": (("Reap stuck jobs", "POST", "/doctor/run", ("reap=true",)),),
    "cache": (("Clear caches", "POST", "/cache/clear", ()),),
    "update": (("Pull update", "POST", "/update/pull", ()),),
    "missions": (("Start mission", "POST", "/missions", ("goal", "dry_run=true")),),
    "agents": (
        ("Start agent", "POST", "/agents/start", ("agent_id",)),
        ("Stop agent", "POST", "/agents/stop", ("agent_id",)),
    ),
    "computer": (
        ("Start task", "POST", "/computer/run", ("goal", "dry_run=true")),
        ("Stop", "POST", "/computer/stop", ()),
    ),
    "android": (
        ("Grant operation", "POST", "/android/permissions/grant", ("op",)),
        ("Revoke operation", "POST", "/android/permissions/revoke", ("op",)),
    ),
    "channels": (("Start channel", "POST", "/channels/start", ("channel=telegram",)),),
    "verifiers": (("Verify", "POST", "/verifiers/verify", ("domain=auto", "task=")),),
    "memory": (
        ("Hybrid recall", "POST", "/memory/hybrid", ("query",)),
        ("Reindex", "POST", "/memory/reindex", ()),
        ("Sweep", "POST", "/memory/sweep", ("dry_run=true",)),
    ),
    "memory2": (
        ("Recall", "POST", "/memory2/recall", ("query",)),
        ("Remember", "POST", "/memory2/remember", ("key", "value")),
    ),
    "embeddings": (
        ("Search", "POST", "/embeddings/search", ("query",)),
        ("Ingest path", "POST", "/embeddings/ingest", ("path",)),
    ),
    "forge": (("Run forge", "POST", "/skills/forge/run", ("goal", "dry_run=true")),),
    "fleet": (("Fan out", "POST", "/fleet/run", ("goal",)),),
    "router": (("Classify task", "POST", "/router/select", ("task",)),),
    "custom-apis": (
        ("Add API", "POST", "/custom-apis/add", ("name", "url")),
        ("Remove API", "POST", "/custom-apis/remove", ("name",)),
    ),
    "response-times": (("Test provider", "POST", "/response-times/test", ("provider",)),),
    "plugins": (("Reload plugins", "POST", "/plugins/reload", ()),),
    "mcp": (("Connect server", "POST", "/mcp/connect", ("name",)),),
    "public-apis": (("Refresh catalogue", "POST", "/public-apis/refresh", ()),),
    "research": (("Research", "POST", "/research", ("query",)),),
    "speech": (
        ("Synthesize", "POST", "/speech/synthesize", ("text",)),
        ("Transcribe", "POST", "/speech/transcribe", ()),
    ),
    "safety": (
        ("Pre-flight check", "POST", "/safety/preflight", ("goal",)),
        ("Create approval prompts", "POST", "/safety/preflight/approvals", ("goal",)),
    ),
    "permissions": (
        ("Resolve pending", "POST", "/permissions/pending/resolve", ("approval_id", "decision=approve")),
        ("Revoke grant", "POST", "/permissions/revoke", ("grant_id",)),
    ),
    "capabilities": (
        ("Register capability", "POST", "/capabilities/registry/register", ("power",)),
        ("Request activation", "POST", "/capabilities/registry/request-activation", ("power",)),
    ),
    "sandbox": (("Run in sandbox", "POST", "/sandbox/run", ("command",)),),
    "local-defense": (("Scan folder", "POST", "/local-defense/scan", ("path",)),),
    "rollback": (
        ("Checkpoint", "POST", "/rollback/checkpoint", ("label",)),
        ("Restore", "POST", "/rollback/restore", ("checkpoint_id",)),
    ),
    "artifacts": (("Export bundle", "POST", "/artifacts/export", ("artifact_ids",)),),
    "watchdog": (("Handle failure", "POST", "/watchdog/handle", ("error",)),),
    "emergency": (
        ("EMERGENCY STOP", "POST", "/emergency/stop", ()),
        ("Release brake", "POST", "/emergency/resume", ()),
    ),
    "workspace": (
        ("Create", "POST", "/workspace/create", ("path",)),
        ("Use", "POST", "/workspace/use", ("path",)),
    ),
    "profiles": (("Create profile", "POST", "/profiles/create", ("name",)),),
    "connectors": (("Refresh connectors", "POST", "/api/v1/console/action/connectors/refresh", ()),),
}

#: Panels whose actions are destructive or authority-changing; the browser asks
#: for an explicit confirmation before sending them.
_CONFIRM = frozenset({"emergency", "update", "rollback", "local-defense", "permissions", "capabilities", "sandbox"})

#: ``panel id -> {action: probe}`` for the few owners that had no HTTP surface
#: at all. Everything else in :data:`_ACTIONS` already has a real endpoint, so
#: the browser calls that directly and the console adds no privileged route of
#: its own. Only these declared names are dispatchable, and the spec is resolved
#: lazily through the same static lookup as a probe.
_SERVER_ACTIONS: dict[str, dict[str, str]] = {
    "connectors": {"refresh": "core.connectors.registry:connector_registry.refresh"},
}


def server_actions(panel_id: str) -> dict[str, str]:
    return dict(_SERVER_ACTIONS.get(panel_id) or {})


def _server_action(panel_id: str, action: str) -> Callable[[], Any]:
    handlers = _SERVER_ACTIONS.get(panel_id) or {}
    if action not in handlers:
        raise KeyError(f"no server action {action!r} for panel {panel_id!r}")
    return _resolve(handlers[action])


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Action:
    label: str
    method: str
    path: str
    fields: tuple[str, ...] = ()
    confirm: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "method": self.method,
            "path": self.path,
            "fields": [
                {"name": f.split("=", 1)[0], "default": (f.split("=", 1)[1] if "=" in f else ""), "required": "=" not in f}
                for f in self.fields
            ],
            "confirm": self.confirm,
        }


@dataclass(frozen=True)
class Panel:
    id: str
    label: str
    group: str
    source: str
    probe: str
    summary: str
    endpoints: tuple[str, ...] = ()
    actions: tuple[Action, ...] = field(default_factory=tuple)

    @property
    def owner(self) -> str:
        """The canonical owner this panel reads, e.g. ``core.memory:get_memory``.

        A shaped provider (``@name``) is console-side glue over one or more
        owners, so it is reported as such rather than pretending to be a backend
        module.
        """
        if self.probe.startswith("@"):
            return f"core.console:{self.probe[1:]}"
        module, _, attr = self.probe.partition(":")
        if not attr:
            return module
        return f"{module}:{attr.split('.', 1)[0]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "source": self.source,
            "owner": self.owner,
            "summary": self.summary,
            "endpoints": list(self.endpoints),
            "actions": [a.to_dict() for a in self.actions],
        }


def _build() -> dict[str, Panel]:
    panels: dict[str, Panel] = {}
    for pid, label, group, source, probe, summary in _TABLE:
        endpoints = _ENDPOINTS.get(pid, ())
        actions = tuple(
            Action(label=label_, method=method, path=path, fields=fields, confirm=pid in _CONFIRM)
            for label_, method, path, fields in _ACTIONS.get(pid, ())
        )
        panels[pid] = Panel(
            id=pid,
            label=label,
            group=group,
            source=source,
            probe=probe,
            summary=summary,
            endpoints=endpoints,
            actions=actions,
        )
    return panels


PANELS: dict[str, Panel] = _build()


def panels() -> list[Panel]:
    return list(PANELS.values())


def get(panel_id: str) -> Panel | None:
    return PANELS.get(panel_id)


def groups() -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for panel in PANELS.values():
        counts[panel.group] = counts.get(panel.group, 0) + 1
    return [{"id": gid, "label": label, "summary": summary, "panels": counts.get(gid, 0)} for gid, label, summary in GROUPS]


def manifest() -> dict[str, Any]:
    """The whole console, without any live data."""
    return {
        "version": MANIFEST_VERSION,
        "groups": groups(),
        "panels": [p.to_dict() for p in PANELS.values()],
        "panel_count": len(PANELS),
        "action_count": sum(len(p.actions) for p in PANELS.values()),
    }


# ---------------------------------------------------------------------------
# Shaped providers — owners whose raw return needs counting or joining. Every one
# of these reads the canonical owner; none re-implements a capability.
# ---------------------------------------------------------------------------
def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception:
        return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("missions", "agents", "items", "sessions", "list", "results"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def _p_missions() -> dict[str, Any]:
    from core.mission import mission_engine

    raw = _jsonable(_as_list(mission_engine.list_missions()))
    rows = [r for r in raw if isinstance(r, dict)]
    counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("status") or row.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return {"total": len(rows), "by_status": counts, "recent": rows[-5:]}


def _p_agents() -> dict[str, Any]:
    from core.agent_manager import agent_manager

    rows = _jsonable(_as_list(_safe(agent_manager.list)))
    return {"total": len(rows), "agents": rows[:20]}


def _p_queue() -> dict[str, Any]:
    from gateway.queue import job_queue

    status = _safe(lambda: job_queue.status()) or {}
    recent = _as_list(_safe(lambda: job_queue.list_jobs(limit=20)))
    counts: dict[str, int] = {}
    for row in recent:
        state = str((row or {}).get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    flat = {k: v for k, v in status.items() if isinstance(v, (str, int, float, bool)) or v is None}
    flat["logged"] = len(recent)
    return {**flat, "recent_counts": counts, "recent": recent[-8:]}


def _p_harness() -> dict[str, Any]:
    from core.harness.sessions import list_sessions

    rows = _as_list(_safe(lambda: list_sessions()))
    return {"sessions": len(rows), "recent": rows[:10]}


def _p_computer() -> dict[str, Any]:
    from core.computer import ControlCenter

    data = _safe(ControlCenter().status) or {}
    return data if isinstance(data, dict) else {"status": data}


def _p_android() -> dict[str, Any]:
    from core.android.tool import get_android_tool

    tool = get_android_tool()
    return {
        "capability": _safe(tool.capability) or {},
        "permissions": _safe(lambda: tool.permissions.list()) or {},
    }


def _p_channels() -> dict[str, Any]:
    from gateway import channels

    return _safe(channels.status) or {"configured": sorted(getattr(channels, "CHANNELS", ()) or ())}


def _p_verifiers() -> dict[str, Any]:
    from core.verifier_registry import verifier_registry

    domains = _safe(verifier_registry.list_domains) or []
    return {"domains": domains, "count": len(domains)}


def _p_memory() -> dict[str, Any]:
    """Session history, curated memory and token usage (legacy v1 backend)."""
    from core.memory import get_memory

    memory = get_memory()
    return {
        "index": _safe(memory.index_stats) or {},
        "tokens": _jsonable(_safe(lambda: memory.get_token_usage(limit=20))) or {},
        "curated": _jsonable(_as_list(_safe(lambda: memory.get_curated_memory())))[:10],
    }


def _p_memory2() -> dict[str, Any]:
    """Typed memory, read **through the canonical facade**.

    ``core.memory`` is the only boundary allowed to touch the typed store, and
    the architecture gate enforces that — this panel asks the facade for its
    own stats rather than importing the backend.
    """
    from core.memory import get_memory

    return _jsonable(_safe(get_memory().stats)) or {"note": "typed store unavailable"}


def _p_embeddings() -> dict[str, Any]:
    from core.embeddings import embedding_store

    return {
        "available": bool(_safe(embedding_store.available)),
        "count": _safe(embedding_store.count),
        "backend": _safe(embedding_store.backend_info),
    }


def _p_skills() -> dict[str, Any]:
    from core.skill_manager import skill_manager

    rows = _as_list(_safe(skill_manager.list_skills))
    return {"count": len(rows), "skills": rows[:20]}


def _p_evolution() -> dict[str, Any]:
    from core.evolution import RED_LINE_PATTERNS, EvolutionPolicy

    policy = EvolutionPolicy()
    return {
        "protected_patterns": len(RED_LINE_PATTERNS),
        "pattern_sample": list(RED_LINE_PATTERNS[:10]),
        "assess_supported": callable(getattr(policy, "assess", None)),
    }


def _p_world() -> dict[str, Any]:
    from core.state import get_world_state

    state = get_world_state()
    return {
        "current_state": _safe(lambda: state.current_state),
        "confidence": _safe(lambda: state.confidence),
        "active_window": _safe(lambda: state.active_window),
        "active_application": _safe(lambda: state.active_application),
        "revision": _safe(lambda: state.revision),
    }


def _p_models() -> dict[str, Any]:
    from core.models.gateway import get_model_gateway

    return _jsonable(_safe(get_model_gateway().status)) or {}


def _p_providers() -> dict[str, Any]:
    from core.providers import list_providers

    rows = _as_list(_safe(list_providers))
    return {"count": len(rows), "providers": rows[:20]}


def _p_fleet() -> dict[str, Any]:
    from core.model_fleet import model_fleet

    rows = _as_list(_safe(model_fleet.list_workers))
    return {"workers": len(rows), "details": rows[:10]}


def _p_custom_apis() -> dict[str, Any]:
    from core.custom_api import custom_api_manager

    rows = _as_list(_safe(custom_api_manager.list_apis))
    return {"count": len(rows), "apis": rows[:20]}


def _p_tools() -> dict[str, Any]:
    """``list_tools()`` returns ``{count, tools, catalog, sources, errors}``."""
    from core.tool_registry import tool_registry

    payload = tool_registry.list_tools() or {}
    if not isinstance(payload, dict):
        names = _tool_names(tool_registry)
        return {"count": len(names), "tools": names}
    names = [str(n) for n in (payload.get("tools") or [])]
    catalog = payload.get("catalog") or []
    rows = [
        {
            "tool": str((row or {}).get("name") or "?"),
            "source": str((row or {}).get("source") or ""),
            "description": str((row or {}).get("description") or "")[:90],
        }
        for row in catalog
        if isinstance(row, dict)
    ]
    return {
        "count": len(names),
        "load_errors": len(payload.get("errors") or []),
        "sources": len(payload.get("sources") or {}),
        "tools": names[:20],
        "catalog": rows or names,
    }


def _p_plugins() -> dict[str, Any]:
    from core.plugins import plugin_registry

    rows = _as_list(_safe(plugin_registry.list))
    return {"count": len(rows), "plugins": rows[:20], "logs": _as_list(_safe(lambda: plugin_registry.logs(5)))}


def _p_mcp() -> dict[str, Any]:
    from core.mcp_client import mcp_manager

    rows = _as_list(_safe(mcp_manager.list_servers))
    return {"count": len(rows), "servers": rows[:20]}


def _p_connectors() -> dict[str, Any]:
    from core.connectors.registry import connector_registry

    try:
        connector_registry_statuses = connector_registry.statuses()
    except Exception:
        connector_registry_statuses = []
    rows = _as_list(connector_registry_statuses)
    enabled = [r for r in rows if str((r or {}).get("enabled")).lower() in ("true", "1", "yes")]
    return {"count": len(rows), "enabled": len(enabled), "connectors": rows[:30]}


def _p_public_apis() -> dict[str, Any]:
    from tools.public_apis import public_api_catalog

    return _safe(public_api_catalog.categories) or {}


def _p_web() -> dict[str, Any]:
    from core.web.gateway import get_web_gateway

    gateway = get_web_gateway()
    return {"capabilities": _safe(gateway.capabilities) or {}}


def _p_research() -> dict[str, Any]:
    from core.research import research_pipeline

    return {"pipeline": type(research_pipeline).__name__, "ready": True}


def _p_speech() -> dict[str, Any]:
    from core.speech import speech_engine

    return _safe(speech_engine.status) or {"backend": getattr(speech_engine, "backend", "unknown")}


def _p_critic() -> dict[str, Any]:
    from core.critic import critic_manager

    officers = [n for n in dir(critic_manager) if n.endswith(("review", "audit", "verify_outcome"))]
    return {"officers": officers, "count": len(officers)}


def _p_self_improvement() -> dict[str, Any]:
    """``get_status()`` is the structured owner API; ``get_for_panel()`` is text."""
    from core.self_improvement import self_improvement

    status = _safe(lambda: self_improvement.get_status()) or {}
    current = status.get("current_reflection") or {}
    last = status.get("last_reflection") or {}
    last_reflection = (last or {}).get("reflection") or {}
    rows = []
    for entry in _as_list(status.get("history")):
        if not isinstance(entry, dict):
            continue
        reflection = entry.get("reflection") or {}
        rows.append(
            {
                "when": str(entry.get("timestamp") or "")[:19],
                "mistakes": reflection.get("mistakes_count", 0),
                "tool_failures": reflection.get("tool_failures_count", 0),
                "fixes": len(_as_list(entry.get("fixes"))),
                "message": str(entry.get("message") or "")[:90],
            }
        )
    return {
        "reflecting": bool(status.get("is_reflecting")),
        "checker": "running" if status.get("background_checker_running") else "stopped",
        "interval_s": status.get("idle_check_interval"),
        "history_count": status.get("history_count", len(rows)),
        "stage": str(current.get("stage") or "idle"),
        "message": str(current.get("message") or status.get("message") or "idle")[:120],
        "last_at": str((last or {}).get("timestamp") or "")[:19],
        "last_mistakes": last_reflection.get("mistakes_count", 0),
        "last_fixes": len(_as_list((last or {}).get("fixes"))),
        "history": rows,
    }


def _p_safety() -> dict[str, Any]:
    from core.safety_policy import load_safety_policy

    policy = load_safety_policy()
    return {
        "version": getattr(policy, "version", ""),
        "name": getattr(policy, "name", ""),
        "summary": getattr(policy, "summary", ""),
        "zones": getattr(policy, "zones", []),
        "rules": len(getattr(policy, "rules", []) or []),
    }


def _p_red_lines() -> dict[str, Any]:
    from core.safety_policy import load_safety_policy

    policy = load_safety_policy()
    rules = [getattr(r, "__dict__", {}) for r in (getattr(policy, "rules", []) or [])]
    return {
        "policy": getattr(policy, "name", "red lines"),
        "zones": getattr(policy, "zones", []),
        "rule_count": len(rules),
        "rules": _jsonable(rules)[:12],
    }


def _p_permissions() -> dict[str, Any]:
    from core.permissions import permission_manager

    return {
        "pending": _as_list(_safe(lambda: permission_manager.pending())),
        "log": _as_list(_safe(lambda: permission_manager.log(10))),
    }


def _p_capabilities() -> dict[str, Any]:
    from core.capability_ledger import get_capability_ledger
    from core.capability_registry import get_capability_registry

    ledger = get_capability_ledger()
    registry = get_capability_registry()
    return {
        "discovered": _safe(lambda: get_capability_ledger().list_discovered()) or [],
        "registered": _safe(registry.list) or [],
        "ledger_rows": len(_as_list(_safe(ledger.list_discovered))),
    }


def _p_local_defense() -> dict[str, Any]:
    from core.local_defense_scanner import scan_folder

    return {"scanner": callable(scan_folder), "runner": "core.local_defense_scanner.scan_folder"}


def _p_rollback() -> dict[str, Any]:
    from core.rollback import rollback_manager

    rows = _as_list(_safe(rollback_manager.list_checkpoints))
    return {"count": len(rows), "checkpoints": rows[:10]}


def _p_artifacts() -> dict[str, Any]:
    from core.artifact_manager import artifact_manager

    rows = _as_list(_safe(artifact_manager.list_artifacts))
    return {"count": len(rows), "artifacts": rows[:10]}


def _p_watchdog() -> dict[str, Any]:
    from core.watchdog import Watchdog

    dog = Watchdog()
    fixes = [
        {"pattern": getattr(pattern, "pattern", str(pattern)), "fix": getattr(fn, "__name__", "fix"), "about": about}
        for pattern, fn, about in getattr(dog, "_fixes", []) or []
    ]
    return {"known_fixes": len(fixes), "history": len(getattr(dog, "history", []) or []), "fixes": fixes}


def _p_emergency() -> dict[str, Any]:
    from core.computer import control_center

    return {"emergency_stop": bool(_safe(control_center.emergency_stop))}


def _p_workspace() -> dict[str, Any]:
    from core.integrations import resolve_active_project

    return {"active": _safe(resolve_active_project) or ""}


def _p_profiles() -> dict[str, Any]:
    from core.profiles import profile_manager

    rows = _as_list(_safe(profile_manager.list))
    return {"count": len(rows), "profiles": rows[:20]}


def _p_eval() -> dict[str, Any]:
    from core.reasoning.eval import eval_harness

    return _safe(eval_harness.summary) or {"categories": _as_list(_safe(eval_harness.list_categories))}


def _p_doctor() -> dict[str, Any]:
    from core.doctor import doctor

    report = _safe(lambda: doctor.run(ask_internet=False, use_llm=False, reap=False)) or {}
    if isinstance(report, dict):
        report.pop("findings", None)
    return report


def _p_runtime_issues() -> dict[str, Any]:
    from core.run_events import recent_issues

    rows = _jsonable(_as_list(recent_issues(limit=100)))
    components: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict):
            name = str(row.get("component") or "unknown")
            components[name] = components.get(name, 0) + 1
    return {"count": len(rows), "by_component": components, "recent": rows[-10:]}


def _p_router() -> dict[str, Any]:
    from core.router2 import TASK_PROFILES, router2

    return {
        "classifier": type(router2).__name__,
        "task_types": sorted(TASK_PROFILES),
        "roles": ["coder", "researcher", "critic", "planner", "verifier"],
        "recorded_outcomes": len(getattr(router2, "outcomes", {}) or {}),
    }


def _p_swe() -> dict[str, Any]:
    from core.swe_mode import swe_mode

    return {
        "entrypoint": f"{type(swe_mode).__module__}.{type(swe_mode).__name__}.execute",
        "workflows": ["inspect", "edit", "build", "test", "debug", "review"],
    }


def _tool_names(tool_registry: Any) -> list[str]:
    """``list_tools()`` returns definitions on some builds and names on others."""
    raw = tool_registry.list_tools() or []
    if isinstance(raw, dict):
        raw = raw.get("tools") or []
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or (item.get("function") or {}).get("name")
            if name:
                names.append(str(name))
    return names


_SHAPED: dict[str, Callable[[], Any]] = {
    "missions": _p_missions,
    "agents": _p_agents,
    "queue": _p_queue,
    "harness": _p_harness,
    "computer": _p_computer,
    "android": _p_android,
    "channels": _p_channels,
    "verifiers": _p_verifiers,
    "memory": _p_memory,
    "memory2": _p_memory2,
    "embeddings": _p_embeddings,
    "skills": _p_skills,
    "evolution": _p_evolution,
    "world": _p_world,
    "models": _p_models,
    "providers": _p_providers,
    "fleet": _p_fleet,
    "custom_apis": _p_custom_apis,
    "tools": _p_tools,
    "plugins": _p_plugins,
    "mcp": _p_mcp,
    "connectors": _p_connectors,
    "public_apis": _p_public_apis,
    "web": _p_web,
    "research": _p_research,
    "speech": _p_speech,
    "critic": _p_critic,
    "self_improvement": _p_self_improvement,
    "safety": _p_safety,
    "red_lines": _p_red_lines,
    "permissions": _p_permissions,
    "capabilities": _p_capabilities,
    "local_defense": _p_local_defense,
    "rollback": _p_rollback,
    "artifacts": _p_artifacts,
    "watchdog": _p_watchdog,
    "emergency": _p_emergency,
    "workspace": _p_workspace,
    "profiles": _p_profiles,
    "eval": _p_eval,
    "doctor": _p_doctor,
    "router": _p_router,
    "runtime_issues": _p_runtime_issues,
    "swe": _p_swe,
}


def _resolve(probe: str) -> Callable[[], Any]:
    """Resolve one probe spec to a zero-argument callable.

    ``module:attr.path`` walks attributes; a trailing ``()`` on a part calls that
    intermediate (``core.learning.facade:get_learning().ledger``). The spec is a
    literal from :data:`_TABLE` — never user input — so this is a static
    lookup, not an evaluation surface.
    """
    if probe.startswith("@"):
        name = probe[1:]
        if name not in _SHAPED:
            raise KeyError(f"unknown shaped provider {name!r}")
        return _SHAPED[name]
    module_name, _, attr_path = probe.partition(":")
    value: Any = importlib.import_module(module_name)
    for part in attr_path.split("."):
        call = part.endswith("()")
        attr = part[:-2] if call else part
        value = getattr(value, attr)
        if call:
            value = value()
    if not callable(value):
        return lambda: value
    return value


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
_MAX_STR = 400
_MAX_ROWS = 25
_MAX_COLS = 12


def _jsonable(value: Any, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…"
    if depth >= 3:
        return str(value)[:_MAX_STR]
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in list(value.items())[:_MAX_COLS]}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in list(value)[:_MAX_ROWS]]
    for attr in ("to_dict", "model_dump"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return _jsonable(fn(), depth + 1)
            except Exception:
                break
    return str(value)[:_MAX_STR]


def _shape(data: Any) -> dict[str, Any]:
    """Turn an owner's return into the small view model the browser renders.

    Doing this server-side keeps one shaping implementation for sixty panels
    instead of sixty JavaScript renderers. Nested objects become dotted KPI
    labels and lists of plain values become chips, so a nested owner return is
    still readable without any per-panel renderer.
    """
    data = _jsonable(data)
    kpis: list[dict[str, Any]] = []
    chips: dict[str, list[Any]] = {}
    rows: list[dict[str, Any]] = []
    columns: list[str] = []

    def absorb(items: dict[str, Any], prefix: str = "") -> None:
        for key, value in items.items():
            label = f"{prefix}{key}"
            if isinstance(value, (str, int, float, bool)) or value is None:
                kpis.append({"label": label, "value": value})
            elif isinstance(value, list) and value and isinstance(value[0], dict) and not rows:
                columns[:] = list(value[0].keys())[:_MAX_COLS]
                rows[:] = [{k: row.get(k) for k in columns} for row in value[:_MAX_ROWS]]
            elif isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) for v in value):
                chips[label] = value[:40]
            elif isinstance(value, dict) and not prefix:
                absorb(value, prefix=f"{key}.")

    if isinstance(data, dict):
        absorb(data)
    elif isinstance(data, list):
        if data and isinstance(data[0], dict):
            columns = list(data[0].keys())[:_MAX_COLS]
            rows = [{k: row.get(k) for k in columns} for row in data[:_MAX_ROWS]]
        else:
            chips["items"] = [v for v in data[:_MAX_ROWS]]
    else:
        kpis = [{"label": "value", "value": data}]
    return {"kpis": kpis[:16], "lists": chips, "columns": columns, "rows": rows, "raw": data}


def probe(panel_id: str) -> dict[str, Any]:
    """Read one panel's live state from its canonical owner."""
    panel = PANELS.get(panel_id)
    if panel is None:
        return {"id": panel_id, "status": "unknown", "error": f"no panel {panel_id!r}", "data": None}
    started = time.perf_counter()
    try:
        data = _resolve(panel.probe)()
    except Exception as exc:  # honest failure beats a fabricated green cell
        return {
            "id": panel.id,
            "label": panel.label,
            "group": panel.group,
            "owner": panel.owner,
            "source": panel.source,
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
            "ms": round((time.perf_counter() - started) * 1000, 2),
            # Same shape as a successful probe so the browser has one contract
            # to render, with nothing in it to mistake for real data.
            "kpis": [],
            "lists": {},
            "columns": [],
            "rows": [],
            "data": None,
        }
    shaped = _shape(data)
    return {
        "id": panel.id,
        "label": panel.label,
        "group": panel.group,
        "owner": panel.owner,
        "source": panel.source,
        "status": "ready",
        "error": None,
        "ms": round((time.perf_counter() - started) * 1000, 2),
        **shaped,
    }


def probe_all(ids: list[str] | None = None) -> dict[str, Any]:
    selected = [PANELS[i] for i in ids if i in PANELS] if ids else list(PANELS.values())
    started = time.perf_counter()
    results = [probe(p.id) for p in selected]
    ready = sum(1 for r in results if r["status"] == "ready")
    return {
        "count": len(results),
        "ready": ready,
        "unavailable": len(results) - ready,
        "ms": round((time.perf_counter() - started) * 1000, 2),
        "panels": results,
    }


__all__ = [
    "Action",
    "GROUPS",
    "MANIFEST_VERSION",
    "PANELS",
    "Panel",
    "get",
    "groups",
    "manifest",
    "panels",
    "probe",
    "probe_all",
    "server_actions",
]
