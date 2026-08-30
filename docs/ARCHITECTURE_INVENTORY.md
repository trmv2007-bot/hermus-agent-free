# Architecture & Ownership Inventory

Reproducible baseline for the clean-slate single-canonical-owner contract.
Generated from the actual tree (`trmv2007-bot/hermus-agent-free`), not an old
report. **One canonical production owner per responsibility; every other
implementation is either (a) a private backend owned by the canonical facade,
(b) an active compatibility facade that forwards to the canonical owner, or
(c) dead — and removed.**

Gates enforcing these claims live in `tests/test_architecture_gates.py`.

---

## Canonical ownership table (one owner per responsibility)

| Responsibility | Canonical owner | Distinguished private backends / facades it owns |
|---|---|---|
| Autonomy / mission | `core/mission.MissionEngine` | — (single autonomy engine) |
| Durable event authority | `core/events.EventBus` (`get_bus`/`publish`) | — |
| Run live-stream / steer / cancel | `core/run_events.RunBus` (singleton `run_bus`) | mirrors onto canonical `EventBus` |
| Dict event projection (gateway) | `core.dashboard_events.dashboard_event_bus` | mirrors onto canonical `EventBus` |
| Computer event projection | `core.computer.events.computer_event_bus` | mirrors onto canonical `EventBus` (added in Phase 1) + cross-process JSONL journal |
| Typed long-term memory | `core.memory2` | — |
| Memory public writer | `core.memory.MemoryFacade` (`get_memory`/`memory`) | owns `core.memory2` (typed) + `core.compat.legacy_memory.Memory` (session/curated/user-model/token) |
| World state public path | `core.state.WorldStateFacade` (`get_world_state`) | owns `core.computer.world_state.WorldState` (backend) |
| Model selection / health boundary | `core.models.ModelGateway` (`get_model_gateway`) | delegates to `provider_resolver` / `model_capabilities` / `router2` |
| Model completion facade | `core.llm.FreeLLM` | uses `providers` / `multi_key` / `openai_compat` / `custom_api` / `model_fleet` |
| Job execution | `gateway.queue.JobQueue` / `Job` | — (AgentManager is a registry + delegation facade, owns no workers) |
| Agent registry + delegation facade | `core.agent_manager.AgentManager` | no job/worker lifecycle (migrated to canonical queue) |
| Sub-agent orchestration | `core.delegation` (JSON-RPC 2.0) | — |
| Sub-agent compat facade | `subagents.subagent` | forwards to `core.delegation` (active callers) |
| Computer agent | `core.computer.ComputerAgent` | — |
| Computer state machine / repair | `core.computer.state_machine` / `core.computer.repair`, `core.computer.verifier` | — |
| Marker verifier (post-hoc) | `core.verifiers.MarkerVerifier` | — |
| Domain verification registry | `core.verifier_registry.VerifierRegistry` | — |
| Control-room UI | `gateway/control.html` + `/control` | single production UI; root `/` → `/control` |
| Speech | `core.speech` | — |
| Doctor/diagnostics | `core.doctor` | — |
| Situational awareness model | `core.world_model.WorldModel` (facts/events for connectors) | distinct from persisted `WorldStateFacade` |

---

## Every implementation performing each responsibility

### Events
- `core/events/bus.py` — canonical durable `EventBus` (append-only JSONL + replay + subscribe).
- `core/run_events.py` — `RunBus` **run lifecycle + live SSE/WS streaming + cancel + steer + issue recording**. Distinct capability (per-run orchestration) retained; mirror onto canonical bus.
- `core/dashboard_events.py` — dict event bus for gateway realtime; bridges to canonical bus.
- `core/computer/events.py` — computer event bus + JSONL journal; bridges to canonical bus (Phase 1).
- **REMOVED (Phase 1): `core/events/legacy.py`** — the `publish_legacy`/`LegacyEventBridge` dead compatibility layer. Zero production consumers; only one test referenced it (migrated to canonical `publish`). Not a capability owner.

### Memory
- `core/memory/__init__.py`, `core/memory/store.py` — canonical `MemoryFacade` (single public writer).
- `core/memory/migration.py` — legacy-data migration reader (`MigrationReader`, `migrate_legacy`).
- `core/memory2.py` — typed memory (semantic/working/procedural/hybrid) backend.
- `core/compat/legacy_memory.py` — session history / curated memory / user model / token usage **backend**. Retained as a private backend owned by the facade — it owns a real SQLite schema (sessions FTS5, curated_memory, skill_usage, trajectories, token_usage) and a user-model JSON file. This is a **distinct capability** from memory2, not a duplicate, so it is intentionally retained behind the single public facade. It is no longer a second public `Memory` singleton.

### Model
- `core/models/gateway.py` — `ModelGateway` (selection + health + outcome recording). Stateless facade; delegates.
- `core/llm.py` — `FreeLLM` completion facade.
- `core/providers.py`, `core/multi_key.py`, `core/openai_compat.py`, `core/custom_api.py`, `core/provider_resolver.py`, `core/model_fleet.py`, `core/router2.py`, `core/model_capabilities.py`, `core/free_keys.py`, `core/nollama.py` — model/provider implementation layers under the facade.

### World state
- `core/state/world.py` — `WorldStateFacade` (public writable path).
- `core/computer/world_state.py` — `WorldState` backend owned by the facade.

### Jobs / agents
- `gateway/queue.py` — `JobQueue`/`Job` (single job execution owner).
- `core/agent_manager.py` — `AgentManager` registry + delegation facade; owns **no** job/worker lifecycle.

---

## Callers / importers

Gathered by static import scan (see `tests/test_architecture_gates.py` and the
`_prod_files`/`_imports` helpers for the reproducible mechanism).

- **Canonical EventBus** (`get_bus/publish`): `dashboard_events`, `computer/events`
  , `tools/gateway`, `gateway/routes_canonical`.
- **RunBus / run_events**: `core/agent`, `core/mission`, `core/mission_files`,
  `core/runtime`, `core/doctor`, `gateway/gateway`, `gateway/queue`,
  `gateway/realtime`, `gateway/routes_jarvis`.
- **dashboard_events**: `core/counsel/council`, `gateway/gateway`,
  `gateway/routes_engine`, `gateway/routes_speech`.
- **Memory facade** (`core.memory.memory/get_memory`): `core/tool_registry`,
  `tui/tui`.  **memory2** (typed): `core/agent`, `core/config`,
  `core/delegation`, `core/harness/memory_graph`, `core/integrations`,
  `gateway/handlers`, `gateway/realtime`, `gateway/routes_subsystems`.
- **ModelGateway**: `gateway/routes_canonical`. **FreeLLM**: `core/agent`,
  `core/computer/*`, `core/counsel/*`, `core/reasoning/*`, `core/multi_ai`,
  `core/self_improvement`, `core/skill_forge`, `core/skill_manager`,
  `core/model_fleet`, `core/multi_key`, `core/doctor`, `tui/tui`, `core/compat/legacy_memory`.
- **WorldStateFacade**: `core/state`. **WorldModel**: `core/connectors/base`.
- **VerifierRegistry**: `core/mission`, `core/swe_mode`, `gateway/realtime`,
  `tools/mission_tools`. **MarkerVerifier**: `core/agent`. **computer.verifier**:
  `core/computer/computer_agent`, `core/computer/__init__`.
- **JobQueue**: `core/agent_manager` (via `_queue()`).
- **subagents.subagent** (facade → core.delegation): `core/tool_registry`,
  `core/trajectory`, `hermus.py`, `tui/tui`.

---

## Compatibility wrappers / bridges (active)

| Module | Kind | Forwards to | Why retained |
|---|---|---|---|
| `subagents/subagent.py` | API-stable facade | `core.delegation` | active callers (`tool_registry`, `trajectory`, `hermus.py`, `tui`) |
| `core/dashboard_events.py` | dict projection | canonical `EventBus` | gateway WS / speech consume dict shape |
| `core/computer/events.py` | dict projection + journal | canonical `EventBus` | computer feed / cross-process tail |
| `core/run_events.py` | run orchestration | canonical `EventBus` (mirror) | per-run live stream / steer / cancel |

No **persistent** event store competes with the canonical EventBus: the dict
buses are in-memory projections that mirror onto it, and the computer JSONL
journal is a cross-process tail feed, not a competing authority.

---

## Safe to delete (dead / no production consumer)

- `core/events/legacy.py` — **DELETED (Phase 1)**. Only a single test referenced it.

## Unique behavior that MUST be ported before any further deletion

- `core/run_events.RunBus` — run lifecycle, cancel, steer-inbox, SSE/WS replay.
  If it is ever removed its capability must be reproduced (it is a genuine owner).
- `core/compat/legacy_memory.Memory` — session/curated/user-model/token schema.
  Retained as a private backend; porting it requires reproducing a real SQLite
  FTS5 schema + user-model file (not a duplicate).

## Files NOT deleted that are genuine capability owners (no duplicate)

`core/verifiers.py`, `core/verifier_registry.py`, `core/computer/verifier.py`
(three distinct verification concerns), `core/delegation.py`,
`core/computer/delegation.py` (dependency-graph desktop delegation - distinct),
`core/world_model.py` (situational awareness vs persisted world state),
`tests/test_computer_agent_v1.py` (recording/verification) and
`tests/test_computer_agent_v2.py` (agent/state-machine/repair) — distinct tests,
not duplicate implementations.

---

## Verified progress (this consolidation pass)

### Tool boundary — agent now routes through the canonical ToolGateway
- `core/agent._execute_tool` now delegates every tool invocation to
  `get_tool_gateway().execute()` (descriptor resolution, policy gating, typed
  `ToolResult`, timeout handling, canonical event emission + trace correlation),
  still backed by the same `core.tool_registry`. The runtime no longer calls
  `tool_registry.execute()` directly.

### Architecture gates (21 total in `tests/test_architecture_gates.py`)
- `test_one_tool_gateway_agent_runtime_uses_it` / `..._control_room_uses_it` /
  `..._no_duplicate_invoke_path` — one tool boundary, no agent bypass.
- `test_autonomy_never_silently_falls_back_to_chat` /
  `test_autonomy_crash_records_failed_not_completed` — autonomy reports explicit
  BLOCKED/FAILED, never a silent downgrade or fabricated completion.
- `test_runtime_uses_canonical_model_gateway` — capability surface uses ModelGateway.
- `test_no_committed_secrets_in_production` — security gate.
- `test_health_endpoint_probes_not_fabricated` / `test_doctor_reports_explicit_states_not_fake_ok`
  — honesty gate.

### Capability-flow integration tests (`tests/test_capability_flows.py`, 15 tests)
Realistic offline flows through the canonical owners, mapped to spec Tasks 1-8:
- T1 ModelGateway select + typed outcome recording (never fake success)
- T2 ToolGateway execute + canonical event emission + typed failure; MissionEngine
  drives a node through the gateway
- T3 failure recovery: JobQueue retries a transient failure; exhausted retries
  surface FAILED/BLOCKED (never 'completed')
- T4 canonical JobQueue runs a job to a result; AgentManager surfaces queue='canonical'
- T5 long-running backend: mission persists + EventBus durable log replays after a
  new bus (reconnect)
- T6 computer events bridge onto the canonical EventBus; verifier records
  before/after evidence
- T7 MemoryFacade round-trip: typed recall + session search + token usage
- T8 ModelGateway fallback classifies a rate-limit as retryable (not fake ok)

Suite: **626 passed** (baseline 590 → +36). Module import scan: 167 modules, 0 failures.
