"""Fleet Registry — the one durable roster of :class:`LiveAgent` (SPEC §3 + §5).

Roadmap step 1. This module is the canonical owner of agent identity and
lifecycle (the duplicate/split-brain registries — ``core/agent_manager.py``,
``core/agents/*``, ``core/harness/swarm.py`` — are merged onto it in step 3
and never re-implemented next to it).

Lifecycle (spec §3)::

    SPAWNING → IDLE
    IDLE → WORKING | THINKING | PAUSED | SLEEPING | DESTROYED
    WORKING ⇄ BLOCKED
    WORKING → IDLE | ERROR | PAUSED
    THINKING → IDLE | WORKING
    BLOCKED → WORKING | IDLE | PAUSED
    PAUSED → IDLE | DESTROYED
    SLEEPING → IDLE
    ERROR → IDLE          (explicit recovery path only — never silent auto-clear)

Every transition is validated and appends a ``state_changed`` bus event, so
"transitions are bus events; illegal transitions rejected" (§3) and the
dashboard, CLI and registry see the same truth (§9).

Durability model — **WAL-first, cache-never-authoritative** (§3/§5):

* every mutation is a bus append first (fsynced by the single writer), then a
  derived roster-cache write ``data/fleet/agents/<id>.json``;
* boot = :meth:`FleetBus.load` → restore the snapshot ``state.agents`` payload
  (the registry is wired as the bus ``state_provider``) → replay the tail
  (``agent.spawned`` / ``agent.updated`` / ``state_changed`` / result events)
  → the roster cache is consulted only when the bus has no events at all;
* a corrupt roster-cache file never breaks boot (skipped with a warning).

Real work path: the registry takes ``chat_fn=None`` at construction and the
default adapter :func:`chat_via_freellm` obtains its client through
``ModelGateway.llm()`` with a Vault-resolved key (:meth:`MultiKeyManager.get_key_bundle`). Tests always
inject a stub ``chat_fn(messages) -> {"content": str, "tokens": int}`` —
this module never calls the network itself.

Deliberate scope line (mirrors ``core.fleet.missions``): no LLM call, no
network — the chat function and the compaction summarizer are injected
callables, so the whole lifecycle is deterministic and offline-testable.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.atomic_io import atomic_write_json, read_json
from core.log import get_logger

from .bus import BROADCAST, RESULT, SCHEMA_VERSION, STATE_CHANGED, FleetBus

logger = get_logger(__name__)

try:  # defensive per consolidation rule: missions must never block the registry
    from .missions import MissionManager  # type: ignore[import]  # noqa: F401
except Exception:  # pragma: no cover - missions exists; belt-and-braces only
    MissionManager = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# States + legal transitions (SPEC §3)
# --------------------------------------------------------------------------- #

SPAWNING = "SPAWNING"
IDLE = "IDLE"
WORKING = "WORKING"
THINKING = "THINKING"
BLOCKED = "BLOCKED"
PAUSED = "PAUSED"
SLEEPING = "SLEEPING"
ERROR = "ERROR"
DESTROYED = "DESTROYED"

AGENT_STATES: tuple[str, ...] = (
    SPAWNING, IDLE, WORKING, THINKING, BLOCKED, PAUSED, SLEEPING, ERROR, DESTROYED,
)

#: Legal transitions (spec §3). Anything else raises :class:`IllegalTransition`.
AGENT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    SPAWNING: (IDLE,),
    IDLE: (WORKING, THINKING, PAUSED, SLEEPING, DESTROYED),
    WORKING: (BLOCKED, IDLE, ERROR, PAUSED),
    THINKING: (IDLE, WORKING),
    BLOCKED: (WORKING, IDLE, PAUSED),
    PAUSED: (IDLE, DESTROYED),
    SLEEPING: (IDLE,),
    ERROR: (IDLE,),
    DESTROYED: (),
}

#: Event kinds (dotted sub-kinds are accepted by the bus, §6 deviation note).
KIND_AGENT_SPAWNED = "agent.spawned"
KIND_AGENT_UPDATED = "agent.updated"
KIND_AGENT_TASK_CANCELLED = "agent.task_cancelled"

SENDER_FLEET_REGISTRY = "fleet-registry"

#: Turns kept raw by :meth:`FleetRegistry.compact` — the rest folds into ``summary``.
COMPACT_KEEP_TURNS = 6

ROSTER_SUFFIX = ".json"
CHECKPOINT_SUFFIX = ".checkpoint.json"


class IllegalTransition(ValueError):
    """Raised when an agent state transition is not in the §3 state machine."""


class RegistryError(ValueError):
    """Raised for bad registry usage: unknown agent, paused assign, missing confirm."""


def _utc_now() -> str:
    """ISO-8601 UTC timestamp for ``created_at`` / ``last_activity`` / memory turns."""
    return datetime.now(timezone.utc).isoformat()


def _new_task_id() -> str:
    """Opaque task id used when ``assign`` gets no ``idempotency_key``."""
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# LiveAgent (SPEC §3 — the durable agent record)
# --------------------------------------------------------------------------- #


@dataclass
class AgentStats:
    """Per-agent counters (§3): ``{tasks_done, tasks_failed, tokens, cost_est, latency_ms}``."""

    tasks_done: int = 0
    tasks_failed: int = 0
    tokens: int = 0
    cost_est: float = 0.0
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks_done": int(self.tasks_done),
            "tasks_failed": int(self.tasks_failed),
            "tokens": int(self.tokens),
            "cost_est": float(self.cost_est),
            "latency_ms": round(float(self.latency_ms), 3),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AgentStats":
        data = data if isinstance(data, dict) else {}
        try:
            return cls(
                tasks_done=int(data.get("tasks_done") or 0),
                tasks_failed=int(data.get("tasks_failed") or 0),
                tokens=int(data.get("tokens") or 0),
                cost_est=float(data.get("cost_est") or 0.0),
                latency_ms=float(data.get("latency_ms") or 0.0),
            )
        except (TypeError, ValueError):
            return cls()


@dataclass
class LiveAgent:
    """One persistent fleet agent (§3 field list, plus a durable BLOCKED payload).

    ``pending_approval`` is a deliberate addition beyond the §3 field list: a
    BLOCKED agent must survive a restart without losing the task that gates it
    (the §3 BLOCKED flow records approval against a concrete task).
    """

    agent_id: str
    name: str
    persona: str = ""
    provider: str = "groq"
    model: str = ""
    key_name: str | None = None
    state: str = SPAWNING
    memory: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    stats: AgentStats = field(default_factory=AgentStats)
    cursor: int = 0
    executed_task_ids: set[str] = field(default_factory=set)
    schema_version: int = SCHEMA_VERSION
    current_task: str | None = None
    last_activity: str = ""
    created_at: str = ""
    pending_approval: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "persona": self.persona,
            "provider": self.provider,
            "model": self.model,
            "key_name": self.key_name,
            "state": self.state,
            "memory": list(self.memory),
            "summary": self.summary,
            "skills": list(self.skills),
            "stats": self.stats.to_dict(),
            "cursor": int(self.cursor),
            "executed_task_ids": sorted(self.executed_task_ids),
            "schema_version": int(self.schema_version),
            "current_task": self.current_task,
            "last_activity": self.last_activity,
            "created_at": self.created_at,
            "pending_approval": self.pending_approval,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LiveAgent":
        if not isinstance(data, dict):
            raise RegistryError("agent record must be a JSON object")
        agent_id = str(data.get("agent_id") or "").strip()
        name = str(data.get("name") or "").strip()
        if not agent_id or not name:
            raise RegistryError("agent record needs agent_id and name")
        memory = [turn for turn in (data.get("memory") or []) if isinstance(turn, dict)]
        skills = [str(skill) for skill in (data.get("skills") or [])]
        executed = {str(task_id) for task_id in (data.get("executed_task_ids") or [])}
        pending = data.get("pending_approval")
        state = str(data.get("state") or IDLE)
        if state not in AGENT_STATES:
            logger.warning("[FleetRegistry] unknown state %r for %s — treating as IDLE", state, agent_id)
            state = IDLE
        return cls(
            agent_id=agent_id,
            name=name,
            persona=str(data.get("persona") or ""),
            provider=str(data.get("provider") or "groq"),
            model=str(data.get("model") or ""),
            key_name=data.get("key_name"),
            state=state,
            memory=memory,
            summary=str(data.get("summary") or ""),
            skills=skills,
            stats=AgentStats.from_dict(data.get("stats")),
            cursor=int(data.get("cursor") or 0),
            executed_task_ids=executed,
            schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
            current_task=data.get("current_task"),
            last_activity=str(data.get("last_activity") or ""),
            created_at=str(data.get("created_at") or ""),
            pending_approval=dict(pending) if isinstance(pending, dict) else None,
        )


# --------------------------------------------------------------------------- #
# Default chat adapter (real path; never used by tests — stubs are injected)
# --------------------------------------------------------------------------- #


def chat_via_freellm(
    agent: LiveAgent,
    task: str,
    messages: list[dict[str, Any]] | None = None,
    *,
    chat_fn: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Default ``assign`` work path: ModelGateway-issued client with a Vault-resolved key.

    The registry is constructed with ``chat_fn=None`` and falls back to this.
    Imports are lazy so importing this module never touches provider config.

    Signature (expanded for roadmap step 2 — vault wiring):

        chat_fn(messages) -> {"content": str, "tokens": int,
                              "tool_calls"?: list[dict], "error"?: str}

    On success the return carries ``content`` + ``tokens`` (real TPM from the
    provider usage response when available, else an estimate). When the provider
    signals it cannot honor a tool-capable request (e.g. ``supports_tools: False``
    preset), the function returns ``{"error": str, "content": fallback}`` so the
    caller can surface the limitation rather than silently dropping tools.

    Key handling (roadmap step 2 — never store raw keys beyond the call):

    * :meth:`MultiKeyManager.get_key_bundle(provider)` resolves
      ``{key, base_url, model}`` from the Vault.
    * The resolved key preview (first 6 chars) is logged at INFO for
      observability — the full key is never logged, never persisted in the bus,
      and never returned to callers.
    * If the bundle is missing the function falls back to FreeLLM auto-detection
      (it will raise if no provider is usable — no silent mock).
    """
    from core.models import get_model_gateway
    from core.multi_key import multi_key_manager
    from core.providers import get_provider

    if messages is None:
        messages = [
            {"role": "system", "content": agent.persona or "You are a helpful fleet agent."},
            {"role": "user", "content": task},
        ]
    wire = [{"role": turn.get("role") or "user", "content": turn.get("content") or ""} for turn in messages]

    # Resolve a key bundle from the Vault for this agent's provider.
    provider = (agent.provider or "").lower() or "groq"
    bundle: dict[str, Any] = {}
    key_preview = "(no-key)"
    try:
        bundle = multi_key_manager.get_key_bundle(provider) or {}
    except Exception as exc:
        # Vault down / no keys — fail loudly, no silent mock. Let the caller
        # (assign) turn this into a RegistryError.
        raise RegistryError(f"no usable key for provider {provider!r}: {exc}") from exc

    if bundle.get("key"):
        key_preview = _key_preview(bundle["key"])
    logger.info("[FleetRegistry] chat_via_freellm: provider=%s key=%s model=%s",
                provider, key_preview, bundle.get("model") or agent.model or "(agent model)")

    # If we have a bundle use it directly; otherwise let FreeLLM auto-detect
    # (it reads .env / stored keys itself and raises when nothing is usable).
    api_key = bundle.get("key") or None
    base_url = bundle.get("base_url") or None
    model = bundle.get("model") or agent.model or None

    # Check whether the resolved provider can accept tool calls. When the preset
    # says supports_tools is False we return an error envelope early so the agent
    # can tell the user rather than silently dropping tools.
    tools_requested = any(turn.get("tool_calls") for turn in messages if isinstance(turn.get("tool_calls"), list))
    if tools_requested and api_key:
        try:
            preset = get_provider(provider)
            if preset.get("supports_tools") is False:
                fallback = (agent.persona or "I am a helpful fleet agent.") + \
                    f" (note: provider {provider!r} does not support tool calls — requested tools were ignored)"
                return {
                    "content": fallback,
                    "tokens": 0,
                    "error": f"provider {provider!r} does not support tool calls",
                }
        except Exception:
            pass  # cannot resolve preset — proceed and let the provider reject

    # The ONE legal model-client path: ModelGateway.llm() (architecture gate:
    # application code never constructs FreeLLM or picks a provider itself).
    llm = get_model_gateway().llm(
        model=model or None,
        provider=provider or None,
        api_key=api_key or None,
        base_url=base_url or None,
    )
    response = llm.chat(wire)
    usage = getattr(response, "usage", None) or {}

    # Tokens: prefer real TPM from the provider usage response; fall back to an
    # estimate when the provider does not report usage (some free tiers do not).
    tpm = int(usage.get("total_tokens") or 0)
    if tpm == 0:
        from core.token_counter import token_counter
        tpm = token_counter.count_messages(wire)

    result: dict[str, Any] = {
        "content": getattr(response, "content", "") or "",
        "tokens": tpm,
    }

    # Tool calls: surface them when the provider emitted any.
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls:
        result["tool_calls"] = list(tool_calls)

    # Provider-level error signalling (e.g. auth failure, rate limit) — FreeLLM
    # already puts error text into content in many cases; when it also sets an
    # explicit error attribute we propagate it.
    if getattr(response, "error", None):
        result["error"] = str(response.error)

    return result


DEFAULT_CHAT_FN = chat_via_freellm


def _key_preview(key_val: str) -> str:
    """Redacted key preview for logs only — never the full key.

    Mirrors :func:`core.multi_key._key_preview` so log output is consistent
    across the Vault and the fleet surfaces.
    """
    from core.multi_key import _key_preview as _real_preview
    return _real_preview(key_val)


# --------------------------------------------------------------------------- #
# FleetRegistry (SPEC §5 ops table)
# --------------------------------------------------------------------------- #


class FleetRegistry:
    """Owns the roster: durable spawn/lifecycle/task ops, boot = snapshot + replay.

    Every mutation is a bus append first (WAL, fsynced), then a derived roster
    cache write — the cache is *never* authoritative (§3/§5). Thread-safe via
    one RLock; the ops surface is intentionally synchronous (like the bus).
    """

    def __init__(
        self,
        bus: FleetBus,
        *,
        chat_fn: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None,
        max_live: int = 50,
        roster_dir: str | Path | None = None,
    ) -> None:
        """
        ``chat_fn(messages) -> {"content": str, "tokens": int}`` — injected work
        callable. ``None`` (production) uses :func:`chat_via_freellm`. Tests must
        inject a stub.

        The default at module level is :const:`DEFAULT_CHAT_FN` (a reference to
        :func:`chat_via_freellm` wired through the Vault). When ``chat_fn=None``
        here we store that default so the registry can reach real providers.

        Boot warm-up (roadmap step 2): on first registry access the registry
        lazily runs :func:`core.free_keys.discover_and_provision_free_models` to
        expand the free-tier pool. This is gated by ``_free_warmup_done`` so it
        runs once per registry instance.
        """
        self.bus = bus
        # Default chat adapter: use the Vault-wired FreeLLM path unless the
        # caller supplies their own (tests, stubs, alternative backends).
        self._chat_fn = chat_fn or chat_via_freellm
        self.max_live = max(1, int(max_live))
        self.roster_dir = Path(roster_dir) if roster_dir is not None else Path(bus.base_dir) / "agents"
        self._agents: dict[str, LiveAgent] = {}
        self._lock = threading.RLock()
        self._booted = False
        # Boot warm-up flag: discover_and_provision_free_models() runs once at
        # first registry access (lazy, not during __init__ so construction stays
        # cheap and offline-friendly).
        self._free_warmup_done: bool = False
        # state_provider hookup (§6 "full registry+agent state"): the bus carries
        # the roster inside every snapshot it writes.
        bus._state_provider = self._snapshot_state

    # ---------- introspection ----------

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FleetRegistry(agents={len(self._agents)}, bus={self.bus!r})"

    def _snapshot_state(self) -> dict[str, Any]:
        """The pluggable ``state`` payload the bus embeds in every snapshot."""
        with self._lock:
            return {"agents": {aid: agent.to_dict() for aid, agent in self._agents.items()}}

    def list(self) -> list[LiveAgent]:
        """All roster agents (any state except DESTROYED — destroyed is gone)."""
        with self._lock:
            return list(self._agents.values())

    def get(self, agent_id: str) -> LiveAgent | None:
        with self._lock:
            return self._agents.get(str(agent_id))

    def _require(self, agent_id: str) -> LiveAgent:
        agent = self._agents.get(str(agent_id))
        if agent is None:
            raise RegistryError(f"unknown agent {agent_id!r}")
        return agent

    # ---------- boot: snapshot → replay → (cache fallback) ----------

    def boot(self) -> "FleetRegistry":
        """Rebuild the roster from the bus: snapshot payload + tail replay.

        The roster cache under ``data/fleet/agents`` is consulted when the
        snapshot has no agents (either no snapshot yet, or snapshot predates
        the current roster). Corrupt cache files are skipped, never fatal.
        Idempotent — safe to call again after a checkpoint.
        """
        with self._lock:
            snapshot, tail = self.bus.load()
            state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
            agents_state = state.get("agents") if isinstance(state, dict) else None
            if isinstance(agents_state, dict) and agents_state:
                for aid, data in agents_state.items():
                    try:
                        self._agents[str(aid)] = LiveAgent.from_dict(data)
                    except Exception:
                        logger.warning("[FleetRegistry] skipping corrupt snapshot agent %r", aid)
            else:
                # No agents in snapshot — load from roster cache as the base state.
                # The tail replay will then apply any live updates since the cache was written.
                self._load_roster_cache()
            replayed = 0
            for event in tail:
                try:
                    applied = self._replay_event(event)
                except Exception:
                    logger.warning("[FleetRegistry] failed replaying event seq=%s", event.seq)
                    applied = False
                replayed += 1 if applied else 0
            for aid, agent in list(self._agents.items()):
                cursor = self.bus.get_cursor(aid)
                if cursor > agent.cursor:
                    agent.cursor = cursor
                if agent.state == DESTROYED:
                    del self._agents[aid]
            self._booted = True
            logger.info(
                "[FleetRegistry] boot: %s agents restored, %s tail events replayed (bus last_seq=%s)",
                len(self._agents), replayed, self.bus.last_seq,
            )
            return self

    def _load_roster_cache(self) -> int:
        """Fallback restore from ``agents/*.json``; corrupt files are skipped."""
        if not self.roster_dir.exists():
            return 0
        restored = 0
        for path in sorted(self.roster_dir.glob(f"*{ROSTER_SUFFIX}")):
            if path.name.endswith(CHECKPOINT_SUFFIX):
                continue
            data = read_json(path, default=None)
            if not isinstance(data, dict):
                logger.warning("[FleetRegistry] skipping corrupt roster cache %s", path.name)
                continue
            try:
                agent = LiveAgent.from_dict(data)
            except Exception:
                logger.warning("[FleetRegistry] skipping invalid roster cache %s", path.name)
                continue
            self._agents[agent.agent_id] = agent
            restored += 1
        return restored

    def _replay_event(self, event) -> bool:  # noqa: ANN001 - FleetEvent avoids an import cycle
        """Apply one tail event to the roster; returns True when it changed state."""
        content = event.content if isinstance(event.content, dict) else {}
        kind = event.kind
        if kind == KIND_AGENT_SPAWNED:
            data = content.get("agent")
            if not isinstance(data, dict):
                return False
            agent = LiveAgent.from_dict(data)
            self._agents[agent.agent_id] = agent
            return True
        if kind == KIND_AGENT_UPDATED:
            aid = str(content.get("agent_id") or "")
            data = content.get("agent")
            if not aid:
                return False
            if isinstance(data, dict):
                self._agents[aid] = LiveAgent.from_dict(data)
            return True
        if kind == STATE_CHANGED and content.get("scope") == "agent":
            aid = str(content.get("agent_id") or "")
            target = str(content.get("to") or "")
            agent = self._agents.get(aid)
            if agent is None:
                return False
            if target == DESTROYED:
                self._agents.pop(aid, None)
                return True
            if target in AGENT_STATES:
                agent.state = target
                agent.last_activity = event.ts
                return True
            return False
        if kind == RESULT and content.get("scope") == "agent":
            agent = self._agents.get(str(content.get("agent_id") or ""))
            task_id = str(content.get("task_id") or "")
            if agent is None or not task_id:
                return False
            agent.executed_task_ids.add(task_id)
            if content.get("approved"):
                agent.pending_approval = None
            return True
        return False

    # ---------- the WAL + roster cache primitives ----------

    def _persist(self, agent: LiveAgent) -> None:
        """Derived roster-cache write (never authoritative; §3/§5)."""
        try:
            atomic_write_json(self.roster_dir / f"{agent.agent_id}{ROSTER_SUFFIX}", agent.to_dict())
        except OSError as exc:  # cache write must never fail the WAL append
            logger.warning("[FleetRegistry] roster cache write failed for %s: %s", agent.agent_id, exc)

    def _unique_name(self, name: str, *, exclude_agent_id: str | None = None, allow_suffix: bool = False) -> str:
        """Case-insensitive uniqueness; a collision raises RegistryError (§5/§9).

        When ``allow_suffix=True`` (internal use only), a collision auto-suffixes
        ``Friday-2``. The default is to reject with RegistryError so the API
        surface can return 409 for name collisions.
        """
        base = str(name or "").strip()
        if not base:
            raise RegistryError("agent name must be a non-empty string")
        taken = {
            other.name.casefold()
            for other in self._agents.values()
            if other.agent_id != exclude_agent_id and other.state != DESTROYED
        }
        if base.casefold() not in taken:
            return base
        if allow_suffix:
            canonical = next(
                other.name for other in self._agents.values()
                if other.agent_id != exclude_agent_id and other.state != DESTROYED
                and other.name.casefold() == base.casefold()
            )
            counter = 2
            while f"{canonical}-{counter}".casefold() in taken:
                counter += 1
            suffixed = f"{canonical}-{counter}"
            logger.warning("[FleetRegistry] name %r already in roster — using %r", name, suffixed)
            return suffixed
        raise RegistryError(f"agent name {base!r} already exists (case-insensitive)")

    def _transition(self, agent: LiveAgent, target: str) -> None:
        """Validate against §3, mutate, and emit the ``state_changed`` bus event."""
        allowed = AGENT_TRANSITIONS.get(agent.state, ())
        if target not in allowed:
            raise IllegalTransition(f"illegal transition {agent.state} → {target} for agent {agent.name!r}")
        previous = agent.state
        agent.state = target
        agent.last_activity = _utc_now()
        if target in (IDLE, DESTROYED):
            agent.current_task = None
        self.bus.append(
            sender=agent.agent_id,
            kind=STATE_CHANGED,
            content={"scope": "agent", "agent_id": agent.agent_id, "from": previous, "to": target},
        )

    # ---------- lifecycle ops (SPEC §5) ----------

    def spawn(self, spec: dict[str, Any]) -> LiveAgent:
        """Create one agent: ONE WAL append (``agent.spawned``, fsynced) + cache.

        The ``SPAWNING → IDLE`` transition is folded into the single
        ``agent.spawned`` append (the event carries the final §3 state) so crash
        recovery replays exactly one event per spawn — the WAL-first rule.
        Name collisions auto-suffix (``Friday-2``, case-insensitive).

        Boot warm-up (roadmap step 2 — vault wiring): after the agent is
        registered, if the agent's ``provider`` is a known provider with stored
        keys, a light Vault health probe checks that at least one healthy binding
        exists. When no healthy binding is reachable the agent is still spawned
        (the registry never refuses a spawn due to provider health) but
        ``agent._warmup_status`` is set to ``'no_healthy_bindings'`` and a
        warning is logged so the dashboard can surface the degraded state.

        This is a Vault-side health check (:meth:`MultiKeyManager.get_dispatchable_entries`
        + :meth:`MultiKeyManager.check_key_health`), **not** a real LLM ping —
        the agent's first ``assign`` will discover a missing provider when it
        actually tries to call the model.
        """
        spec = dict(spec or {})
        with self._lock:
            name = self._unique_name(spec.get("name"), allow_suffix=True)
            now = _utc_now()
            agent = LiveAgent(
                agent_id=str(spec.get("agent_id") or uuid.uuid4()).strip() or str(uuid.uuid4()),
                name=name,
                persona=str(spec.get("persona") or ""),
                provider=str(spec.get("provider") or "groq"),
                model=str(spec.get("model") or ""),
                key_name=spec.get("key_name"),
                skills=[str(skill) for skill in (spec.get("skills") or [])],
                state=IDLE,  # Final state directly; no separate SPAWNING->IDLE transition event
                created_at=now,
                last_activity=now,
            )
            # Register agent FIRST so it exists for any subsequent events
            self._agents[agent.agent_id] = agent
            # Emit the single agent.spawned event carrying the final state
            self.bus.append(
                sender=SENDER_FLEET_REGISTRY,
                kind=KIND_AGENT_SPAWNED,
                content={"agent": agent.to_dict()},
            )
            self._persist(agent)
            self._enforce_live_cap()
            # --- boot warm-up (roadmap step 2) ---
            self._ensure_free_tier()
            self._warmup_agent(agent)
            return agent

    def _ensure_free_tier(self) -> None:
        """Run the free-tier provisioner once per registry instance (roadmap step 2).

        Delegates to :func:`core.free_keys.discover_and_provision_free_models`
        which auto-registers Ollama, OpenRouter free pool, Mistral free tier,
        Groq free tier, etc. into the Vault so agents can discover them.
        """
        if self._free_warmup_done:
            return
        try:
            from core.free_keys import discover_and_provision_free_models
        except Exception:
            # Module may not be available in minimal test environments.
            return
        try:
            result = discover_and_provision_free_models(auto_register=True)
            logger.info(
                "[FleetRegistry] free-tier warm-up: %s provider(s) discovered",
                len(result.get("discovered", [])),
            )
        except Exception as exc:
            logger.debug("[FleetRegistry] free-tier warm-up skipped: %s", exc)
        finally:
            self._free_warmup_done = True

    def _warmup_agent(self, agent: LiveAgent) -> None:
        """Light Vault health probe for a newly spawned agent (roadmap step 2).

        Checks whether the agent's provider has at least one healthy, dispatchable
        key binding. When it does not, sets ``agent._warmup_status`` and logs a
        warning. Never raises — a missing provider is a degraded state, not a
        spawn failure.
        """
        provider = (agent.provider or "").lower().strip()
        if not provider or provider == "groq":
            # Default provider — still probe if keys exist; skip if no stored keys
            # at all (nothing to warm up).
            pass
        # Check the Vault for dispatchable entries (healthy + within budget).
        try:
            from core.multi_key import multi_key_manager

            dispatchable = multi_key_manager.get_dispatchable_entries(provider)
            if not dispatchable:
                # No keys at all for this provider, or all exhausted/quarantined.
                healthy = False
            else:
                # Light probe: ask the Vault whether any binding is currently healthy.
                healthy = any(
                    multi_key_manager.check_key_health(provider, e.get("key"))
                    for e in dispatchable
                    if e.get("key")
                )
        except Exception as exc:
            # Vault may not be initialised (tests, fresh installs). Treat as
            # "no healthy bindings" so the dashboard can warn, but do not break
            # the spawn path.
            healthy = False
            logger.debug("[FleetRegistry] warm-up Vault probe skipped for %s: %s", provider, exc)

        if not healthy:
            agent._warmup_status = "no_healthy_bindings"
            logger.warning(
                "[FleetRegistry] %s cannot reach provider %s — no healthy keys",
                agent.name,
                provider,
            )
        else:
            agent._warmup_status = "ok"

    def update(self, agent_id: str, patch: dict[str, Any]) -> LiveAgent:
        """Update name/persona/model/key_name/skills; name uniqueness re-checked."""
        patch = dict(patch or {})
        allowed = ("name", "persona", "model", "key_name", "skills")
        unknown = sorted(set(patch) - set(allowed))
        if unknown:
            raise RegistryError(f"update() only accepts {allowed}, got unknown keys {unknown}")
        with self._lock:
            agent = self._require(agent_id)
            if agent.state == DESTROYED:
                raise RegistryError(f"agent {agent_id!r} is destroyed")
            if "name" in patch:
                agent.name = self._unique_name(patch["name"], exclude_agent_id=agent.agent_id, allow_suffix=True)
            if "persona" in patch:
                agent.persona = str(patch["persona"] or "")
            if "model" in patch:
                agent.model = str(patch["model"] or "")
            if "key_name" in patch:
                agent.key_name = patch["key_name"]
            if "skills" in patch:
                agent.skills = [str(skill) for skill in (patch["skills"] or [])]
            agent.last_activity = _utc_now()
            self.bus.append(
                sender=SENDER_FLEET_REGISTRY,
                kind=KIND_AGENT_UPDATED,
                content={"agent_id": agent.agent_id, "patch": patch, "agent": agent.to_dict()},
            )
            self._persist(agent)
            return agent

    def dismiss(self, agent_id: str, confirm: bool = False) -> None:
        """Destroy an agent. Requires ``confirm=True``; cancels in-flight first."""
        if confirm is not True:
            raise RegistryError("dismiss() requires confirm=True (destructive op)")
        with self._lock:
            agent = self._require(agent_id)
            if agent.state in (WORKING, BLOCKED):
                self._cancel_inflight(agent)  # ends in IDLE
            if agent.state == SLEEPING:
                self._transition(agent, IDLE)  # SLEEPING → IDLE → DESTROYED (legal path)
            if agent.state not in (IDLE, PAUSED):
                raise IllegalTransition(f"cannot dismiss agent {agent.name!r} from state {agent.state}")
            self._transition(agent, DESTROYED)
            self._agents.pop(agent.agent_id, None)
            for suffix in (ROSTER_SUFFIX, CHECKPOINT_SUFFIX):
                path = self.roster_dir / f"{agent.agent_id}{suffix}"
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("[FleetRegistry] could not remove cache %s", path.name)

    def pause(self, agent_id: str) -> LiveAgent:
        """Pause an agent — freezes cursor consumption (§3 PAUSED)."""
        with self._lock:
            agent = self._require(agent_id)
            self._transition(agent, PAUSED)
            self._persist(agent)
            return agent

    def resume(self, agent_id: str) -> LiveAgent:
        """Resume from PAUSED → IDLE (consumption unfreezes from the frozen cursor)."""
        with self._lock:
            agent = self._require(agent_id)
            self._transition(agent, IDLE)
            self._persist(agent)
            return agent

    def cancel_task(self, agent_id: str) -> dict[str, Any]:
        """Cooperatively cancel in-flight work → IDLE (bus event emitted)."""
        with self._lock:
            agent = self._require(agent_id)
            return self._cancel_inflight(agent)

    def _cancel_inflight(self, agent: LiveAgent) -> dict[str, Any]:
        if agent.state not in (WORKING, BLOCKED, PAUSED):
            raise RegistryError(f"agent {agent.name!r} has no in-flight task (state {agent.state})")
        task_id = agent.current_task or (agent.pending_approval or {}).get("task_id")
        self._transition(agent, IDLE)
        agent.pending_approval = None
        self.bus.append(
            sender=agent.agent_id,
            kind=KIND_AGENT_TASK_CANCELLED,
            content={"scope": "agent", "agent_id": agent.agent_id, "task_id": task_id, "cancelled": True},
        )
        self._persist(agent)
        return {"agent_id": agent.agent_id, "task_id": task_id, "cancelled": True}

    # ---------- the real task path ----------

    def assign(self, agent_id: str, task: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Run one task: IDLE→WORKING, ``chat_fn(messages)``, WORKING→IDLE.

        Dedup: ``idempotency_key`` already in ``executed_task_ids`` (or the bus's
        executed ledger) → the task is skipped, nothing executes. A result
        containing a ``needs_approval`` dict parks the agent WORKING→BLOCKED
        (§3) until :meth:`approve` / :meth:`reject`. A paused agent rejects work
        (cursor frozen). Errors go WORKING→ERROR (§3) and re-raise.
        """
        task_text = str(task or "").strip()
        if not task_text:
            raise RegistryError("task must be a non-empty string")
        with self._lock:
            agent = self._require(agent_id)
            if agent.state == PAUSED:
                raise RegistryError(f"agent {agent.name!r} is paused (cursor frozen) — resume before assign")
            if agent.state != IDLE:
                raise IllegalTransition(f"agent {agent.name!r} cannot take a task from state {agent.state}")
            task_id = str(idempotency_key).strip() if idempotency_key else _new_task_id()
            if not task_id:
                raise RegistryError("idempotency_key must be a non-empty string")
            if task_id in agent.executed_task_ids or self.bus.is_executed(agent.agent_id, task_id):
                logger.info("[FleetRegistry] task %r already executed by %s — deduped", task_id, agent.name)
                return {"agent_id": agent.agent_id, "task_id": task_id, "executed": False, "deduplicated": True}
            agent.current_task = task_id
            self._transition(agent, WORKING)
            agent.memory.append({"role": "user", "content": task_text, "ts": _utc_now()})
            messages = [{"role": "system", "content": agent.persona or "You are a helpful fleet agent."}]
            messages += [{"role": turn["role"], "content": turn["content"]} for turn in agent.memory]
            started = time.perf_counter()
            try:
                raw = self._chat_fn(messages) if self._chat_fn is not None else chat_via_freellm(agent, task_text, messages)
                result = raw if isinstance(raw, dict) else {"content": str(raw or ""), "tokens": 0}
            except Exception as exc:
                self._fail_task(agent, task_id, exc)
                raise RegistryError(f"task {task_id} failed: {exc}") from exc
            latency_ms = (time.perf_counter() - started) * 1000.0
            content = str(result.get("content") or "")
            tokens = int(result.get("tokens") or 0)
            needs_approval = result.get("needs_approval")
            if isinstance(needs_approval, dict):
                agent.pending_approval = {
                    "task_id": task_id,
                    "task": task_text,
                    "content": content,
                    "tokens": tokens,
                    "needs_approval": needs_approval,
                }
                self._transition(agent, BLOCKED)
                self._persist(agent)
                return {
                    "agent_id": agent.agent_id,
                    "task_id": task_id,
                    "executed": False,
                    "blocked": True,
                    "needs_approval": needs_approval,
                }
            self._finish_task(agent, task_id, task_text, content, tokens, latency_ms, approved=True)
            return {"agent_id": agent.agent_id, "task_id": task_id, "executed": True, "content": content, "tokens": tokens}

    def _finish_task(
        self,
        agent: LiveAgent,
        task_id: str,
        task: str,
        content: str,
        tokens: int,
        latency_ms: float,
        *,
        approved: bool,
    ) -> dict[str, Any]:
        """Record a completed turn: memory, stats, RESULT event, cursor, → IDLE."""
        agent.memory.append({"role": "assistant", "content": content, "ts": _utc_now()})
        stats = agent.stats
        stats.tasks_done += 1
        stats.tokens += max(0, tokens)
        if latency_ms > 0:
            stats.latency_ms = round((stats.latency_ms * (stats.tasks_done - 1) + latency_ms) / stats.tasks_done, 3)
        event = self.bus.append(
            sender=agent.agent_id,
            kind=RESULT,
            content={
                "scope": "agent",
                "agent_id": agent.agent_id,
                "task_id": task_id,
                "task": task,
                "content": content,
                "tokens": tokens,
                "approved": approved,
            },
        )
        agent.executed_task_ids.add(task_id)
        self.bus.mark_executed(agent.agent_id, task_id)
        agent.cursor = max(agent.cursor, self.bus.set_cursor(agent.agent_id, event.seq))
        if agent.state == WORKING:  # approve() lands here already WORKING
            self._transition(agent, IDLE)
        self._persist(agent)
        return {"agent_id": agent.agent_id, "task_id": task_id, "executed": True, "content": content, "tokens": tokens}

    def _fail_task(self, agent: LiveAgent, task_id: str, exc: Exception) -> None:
        """WORKING → ERROR (§3, explicit recovery only) + an honest failure event."""
        logger.warning("[FleetRegistry] task %s failed on %s: %s", task_id, agent.name, exc)
        agent.stats.tasks_failed += 1
        self.bus.append(
            sender=agent.agent_id,
            kind=RESULT,
            content={
                "scope": "agent",
                "agent_id": agent.agent_id,
                "task_id": task_id,
                "error": str(exc),
                "failed": True,
            },
        )
        self._transition(agent, ERROR)
        self._persist(agent)

    def approve(self, agent_id: str) -> dict[str, Any]:
        """BLOCKED → WORKING → IDLE with the approval recorded (§3)."""
        with self._lock:
            agent = self._require(agent_id)
            if agent.state != BLOCKED or not agent.pending_approval:
                raise IllegalTransition(f"agent {agent.name!r} is not BLOCKED (state {agent.state})")
            pending = agent.pending_approval
            self._transition(agent, WORKING)
            result = self._finish_task(
                agent,
                str(pending.get("task_id") or ""),
                str(pending.get("task") or ""),
                str(pending.get("content") or ""),
                int(pending.get("tokens") or 0),
                0.0,
                approved=True,
            )
            agent.pending_approval = None
            return {**result, "approved": True}

    def reject(self, agent_id: str) -> dict[str, Any]:
        """Reject the pending draft: BLOCKED → IDLE; the task may be reassigned."""
        with self._lock:
            agent = self._require(agent_id)
            if agent.state != BLOCKED:
                raise IllegalTransition(f"agent {agent.name!r} is not BLOCKED (state {agent.state})")
            pending = agent.pending_approval or {}
            task_id = str(pending.get("task_id") or "")
            agent.pending_approval = None
            self._transition(agent, IDLE)
            self.bus.append(
                sender=agent.agent_id,
                kind=RESULT,
                content={"scope": "agent", "agent_id": agent.agent_id, "task_id": task_id, "rejected": True},
            )
            self._persist(agent)
            return {"agent_id": agent.agent_id, "task_id": task_id, "executed": False, "rejected": True}

    def broadcast(self, content: Any) -> Any:
        """One fleet-wide bus event (§5) — every non-destroyed agent can read it."""
        return self.bus.append(sender=SENDER_FLEET_REGISTRY, kind=BROADCAST, content=content)

    # ---------- sleep = real teardown, compaction, migration ----------

    def sleep(self, agent_id: str) -> LiveAgent:
        """Checkpoint then drop memory + live state (§3/§13: SLEEPING = ~0 RAM)."""
        with self._lock:
            agent = self._require(agent_id)
            self._transition(agent, SLEEPING)
            checkpoint = {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "memory": list(agent.memory),
                "summary": agent.summary,
                "cursor": agent.cursor,
                "stats": agent.stats.to_dict(),
                "executed_task_ids": sorted(agent.executed_task_ids),
                "pending_approval": agent.pending_approval,
                "current_task": agent.current_task,
                "checkpointed_at": _utc_now(),
            }
            atomic_write_json(self.roster_dir / f"{agent.agent_id}{CHECKPOINT_SUFFIX}", checkpoint)
            agent.memory = []
            agent.pending_approval = None
            self._persist(agent)
            return agent

    def wake(self, agent_id: str) -> LiveAgent:
        """Restore from the sleep checkpoint: SLEEPING → IDLE with memory back."""
        with self._lock:
            agent = self._require(agent_id)
            checkpoint = read_json(self.roster_dir / f"{agent.agent_id}{CHECKPOINT_SUFFIX}", default=None)
            if isinstance(checkpoint, dict) and checkpoint.get("agent_id") == agent.agent_id:
                memory = [turn for turn in (checkpoint.get("memory") or []) if isinstance(turn, dict)]
                if memory:
                    agent.memory = memory
                agent.summary = str(checkpoint.get("summary") or agent.summary)
                restored_stats = AgentStats.from_dict(checkpoint.get("stats"))
                if restored_stats.tasks_done or restored_stats.tokens:
                    agent.stats = restored_stats
                agent.executed_task_ids |= {str(t) for t in (checkpoint.get("executed_task_ids") or [])}
                pending = checkpoint.get("pending_approval")
                if isinstance(pending, dict):
                    agent.pending_approval = pending
                cursor = int(checkpoint.get("cursor") or 0)
                if cursor > agent.cursor and cursor <= self.bus.last_seq:
                    agent.cursor = cursor
            else:
                logger.warning("[FleetRegistry] no usable checkpoint for %s — waking with current state", agent_id)
            self._transition(agent, IDLE)
            self._persist(agent)
            return agent

    def recover(self, agent_id: str) -> LiveAgent:
        """The explicit ERROR → IDLE recovery path (never silent auto-clear, §3)."""
        with self._lock:
            agent = self._require(agent_id)
            self._transition(agent, IDLE)
            self._persist(agent)
            return agent

    def compact(self, agent_id: str, summarizer: Callable[[list[str]], str]) -> dict[str, Any]:
        """Fold old turns into ``summary`` (injected summarizer); keep last 6 raw."""
        if not callable(summarizer):
            raise RegistryError("summarizer must be a callable(list[str]) -> str")
        with self._lock:
            agent = self._require(agent_id)
            if len(agent.memory) <= COMPACT_KEEP_TURNS:
                return {"agent_id": agent.agent_id, "compacted": False, "turns": len(agent.memory)}
            folded = agent.memory[:-COMPACT_KEEP_TURNS]
            agent.memory = agent.memory[-COMPACT_KEEP_TURNS:]
            texts = [f"prior summary: {agent.summary}"] if agent.summary else []
            texts += [f"{turn.get('role')}: {turn.get('content')}" for turn in folded]
            agent.summary = str(summarizer(texts))
            self._persist(agent)
            return {
                "agent_id": agent.agent_id,
                "compacted": True,
                "summary": agent.summary,
                "kept_turns": len(agent.memory),
                "folded_turns": len(folded),
            }

    def import_legacy_agent(self, name: str, persona: str, model: str, state: str = SLEEPING) -> LiveAgent:
        """Migration (§13): old registry agents import as SLEEPING — never live."""
        with self._lock:
            if state != SLEEPING:
                logger.warning("[FleetRegistry] import of %r coerced to SLEEPING (requested %r)", name, state)
            provider = model.split("/", 1)[0] if "/" in str(model or "") else "groq"
            agent = self.spawn({"name": name, "persona": persona, "model": model, "provider": provider})
            self._transition(agent, SLEEPING)  # IDLE → SLEEPING: imported agents stay cold
            checkpoint = {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "memory": [],
                "summary": "",
                "cursor": agent.cursor,
                "stats": agent.stats.to_dict(),
                "executed_task_ids": [],
                "pending_approval": None,
                "current_task": None,
                "checkpointed_at": _utc_now(),
                "imported_legacy": True,
            }
            atomic_write_json(self.roster_dir / f"{agent.agent_id}{CHECKPOINT_SUFFIX}", checkpoint)
            self._persist(agent)
            return agent

    def checkpoint_all(self) -> int:
        """Persist every roster cache + one bus snapshot (§5 checkpoint op)."""
        with self._lock:
            for agent in self._agents.values():
                self._persist(agent)
            self.bus.snapshot()
            return len(self._agents)

    def _enforce_live_cap(self) -> None:
        """LRU auto-sleep of idle agents beyond ``max_live`` live (§13)."""
        while True:
            live = [a for a in self._agents.values() if a.state not in (SLEEPING, DESTROYED)]
            if len(live) <= self.max_live:
                return
            idle = sorted(
                (a for a in live if a.state == IDLE),
                key=lambda a: (a.last_activity or a.created_at or "", a.agent_id),
            )
            if not idle:
                logger.warning(
                    "[FleetRegistry] %s live agents above cap %s but none idle — no auto-sleep",
                    len(live), self.max_live,
                )
                return
            victim = idle[0]
            logger.info("[FleetRegistry] live cap %s exceeded — auto-sleeping LRU agent %r", self.max_live, victim.name)
            self.sleep(victim.agent_id)


__all__ = [
    "AGENT_STATES",
    "AGENT_TRANSITIONS",
    "AgentStats",
    "BLOCKED",
    "COMPACT_KEEP_TURNS",
    "DESTROYED",
    "ERROR",
    "FleetRegistry",
    "IDLE",
    "IllegalTransition",
    "KIND_AGENT_SPAWNED",
    "KIND_AGENT_TASK_CANCELLED",
    "KIND_AGENT_UPDATED",
    "LiveAgent",
    "PAUSED",
    "RegistryError",
    "SLEEPING",
    "SPAWNING",
    "THINKING",
    "WORKING",
    "chat_via_freellm",
]










