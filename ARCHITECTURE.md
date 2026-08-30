# HERMUS Architecture — Post-Consolidation Reference

This is the **canonical** architecture reference (replaces the historical
`ARCHITECTURE_UPGRADES.md` narrative). It describes ownership after the
consolidation: one public facade, one state model, one persistence owner and one
execution path per subsystem. It also explicitly lists the legacy components that
were moved to a compat/migration path so future work does not reintroduce
split-brain designs.

> Principles: **complexity with ownership.** Internal complexity is fine; having
> two parallel answers to the same question is not. A capability counts only when
> its backend execution path, persistence, verification, error handling and
> user-visible state are connected and tested.

---

## 1. One execution plan

```
INPUTS (CLI, dashboard, voice, Telegram/Discord, scheduler, API, background)
        │
        ▼
Command / Intent Gateway   ── normalize + auth + trace ──►  typed Command → canonical EventEnvelope
        │
        ▼
Universal Runtime (core.runtime.execute)  ── chat / analysis / mission
        │
        ├── chat path ─────────────────────────────► foreground turn
        └── executable objective ──► MissionEngine  (the only autonomy engine)
                                       │
                       Plan/DAG ◄── ModelGateway ──► ToolGateway ──► shell / files / web / computer
                                       │                    │
                                       │                    ▼
                                       │                 Evidence Store
                                       │                    ▼
                                       │                 Verification ── success → Learn
                                       │                    │
                                       │                    └── fail → Diagnose → Repair → Re-plan
                                       │                                    │
                                       └────────► Live State / WorldState ──► Memory / Jobs

Every component feeds the same EventBus and carries a trace_id / mission_id.
The dashboard is only a projection (snapshot + replay) — it never owns truth.
```

---

## 2. Canonical subsystems

| Subsystem | Canonical facade | Persistence owner | Execution path |
|---|---|---|---|
| **Contracts** | `core.contracts` | none (pure types) | consumed everywhere |
| **Events** | `core.events.EventBus` | durable JSONL event log | `publish → subscribe/replay` |
| **Tools** | `core.tools.ToolGateway` | ToolRegistry + evidence | only legal invocation path |
| **Models** | `core.models.ModelGateway` | provider/credential state | only provider/select path |
| **Memory** | `core.memory.MemoryFacade` | `core.memory2` MemoryStore | one writable path |
| **World state** | `core.state.WorldStateFacade` | `core.computer.world_state` | one writable path |
| **Mission** | `core.mission.MissionEngine` | mission reports/workspace | only autonomy engine |
| **Jobs** | `gateway/queue.py` `Job` (subclasses `core.contracts.Job`) + `core/agent_manager` (registry/delegation) | durable event log + results | lease/heartbeat/reaper |
| **Health** | `bootstrap.doctor()` / `core.doctor` | diagnostics | bounded recovery |
| **Bootstrap** | `bootstrap.py` | venv + data layout | one command, idempotent |
| **Gateway** | `gateway/gateway.py` | — | transport only |

---

## 3. One canonical contract per subsystem

- **EventEnvelope** — the one event model (`core.contracts.Events`). UI and
  integrations subscribe; they do not invent a second model. Secret-bearing args
  are redacted before they reach an envelope.
- **Command** — every meaningful UI action is a typed backend Command with
  correlation IDs, authorization, execution, result, audit record and live state.
- **ToolDescriptor / ToolResult** — the one tool format and result envelope. No
  caller invokes `subprocess`/`pyautogui`/Playwright/provider code directly.
- **ModelRequirement / ModelSelection / ModelGatewayResult** — the one model
  selection contract. Keywords are a score feature, never proof of capability.
- **MissionNode / MissionState** — the one node contract and state machine.
  Verification is a first-class phase; failures are typed before retry.
- **Job** — the one durable background-work schema (`core.contracts.jobs.Job`). The
  live queue's `gateway/queue.py::Job` is a subtype of it, so the §14 lease/heartbeat/
  idempotency/attempt fields are the shared contract and the queue adds only runtime
  operational fields.

---

## 4. Legacy components — relocated to compat / read-only / deleted

Clean-slate rule: **one responsibility = one production implementation.** Compatibility
code may exist only during a controlled migration and must have a deletion milestone.
The final tree must not contain two competing implementations.

| Subsystem | State | Detail |
|---|---|---|
| Autonomy | ✅ **DONE** — `core/autonomous.py` **deleted** | `MissionEngine` is the only autonomy engine; disabling the runtime is a **BLOCKED** state, never a silent downgrade. The marker verifier/diagnoser was rehomed to `core/verifiers.py` (feature depth preserved). |
| Events | 🟡 **bridged** (`core/dashboard_events.py`) | Every dashboard/gateway/speech event now funnels into the one canonical `core.events.EventBus` (durable, replayable). The dict API is kept only as a migration bridge. |
| Memory | ✅ **DONE** — `MemoryFacade` is the single writable memory path | The facade owns the typed Memory2 store plus the v1 session/curated/user-model/token backend; no competing process-level v1 singleton. |
| World state | ✅ **DONE** — `world_state_v2.py` **deleted** | `core/computer/world_state.WorldState` (v1) is canonical; `core.state.WorldStateFacade` is the single writable path. The dead V2 duplicate (exported only from `__init__`, used by no functional code) was removed. |
| Models | ✅ **verified single stack — no duplicate** | The provider layer (`providers` → `multi_key` → `provider_resolver` → `model_capabilities`) is one mutually-dependent stack with `ModelGateway` (`core/models/gateway.py`) as its single public facade. Each capability (`select_usable_bundle`, `list_available_providers`, `discover_runtime_bundles`, `negotiate`, `select_compatible_model`, `diagnose`) has exactly **one** owner. `router2` (runtime task-type model swap) and `model_fleet` (multi-model distribution) are distinct higher-level features, not duplicates. No competing production implementation exists to delete. |
| Setup | 🟡 `setup.sh`/`activate.sh` thin wrappers → `./hermus bootstrap` | one bootstrap. |

### Deletion milestones (follow-ups)
1. ~~Migrate world-state consumers → `WorldStateFacade`; delete v1.~~ ✅ done (deleted the dead `world_state_v2` duplicate; v1 is canonical).
2. Migrate run/dashboard/computer event producers + consumers to `EventEnvelope`; delete the legacy event modules.
3. Migrate legacy memory consumers → `core.memory.get_memory()` entirely; retire `core/compat/legacy_memory.py` as a public path (facade already owns the v1 backend).
4. ~~Collapse `providers`/`model_fleet`/`router2`/`multi_key`/`provider_resolver` behind `ModelGateway`.~~ **resolved — no duplicate found.** Detect-first analysis verified these are ONE mutually-dependent provider stack, not competing implementations; `ModelGateway` is the single facade. No deletion warranted. (Future nicety, not a deletion: have the live runtime's model-selection path also go through `ModelGateway`.)
5. Rebuild the UI shell on `snapshot + replay`; replace the four `dashboard*.html` surfaces. **In progress**: the canonical projection is now served at `/control` (pure snapshot + replay). Deleting the four legacy surfaces is gated on migrating the ~6 dashboard test files + JS assets (`living-deck.js`, `hermus-client.js`, `jarvis-control.js`) and the `/dashboard*`, `/jarvis`, `/api/jarvis/*`, `/computer/dashboard`, `/dashboard-assets/*` routes.
6. ~~Delete duplicate agent managers / orchestration concepts with no distinct contract.~~ ✅ **done** — `AgentManager` is now a thin named-agent registry + delegation facade; its detached subprocess `worker_loop`, and its own `state.json` heartbeat + `jobs/*.json`/`results/*.json` lifecycle were removed. Background work is executed by the canonical Job queue.

### Remaining (Final One-Shot Spec §3/§7/§16)
1. **Control-room cleanup** — make `/control` the only UI; root `/` → `/control`; migrate/delete the legacy dashboard HTML/JS/routes (`/dashboard*`, `/jarvis`, `/computer/dashboard`, `/dashboard-assets/*`, and the `dashboard.html`/`jarvis_dashboard.html`/`dashboard_computer.html`/`remote.html` + `hermus-client.js`/`jarvis-control.js`/`living-deck.*` assets).
2. **Events** — migrate remaining `dashboard_events`/`run_bus` consumers to `EventEnvelope`/`EventBus` and delete the bridge.
3. **Memory** — migrate remaining legacy `core.memory`/v1 imports to `MemoryFacade`; no second public writer.
4. **Models** — add a static check that no code path calls a provider SDK directly outside the model subsystem; `ModelGateway` is the only public boundary.
5. **Setup** — one idempotent `bootstrap`/`start`/`doctor`; confirm shell wrappers contain no business logic and never mask required dep failures.

---

## 5. Explicitly not in the architecture (anti-patterns)

The following are **forbidden** by the consolidation rules:

- **No parallel `v2`/`v3`/`new`/`final` subsystems.** Extend the canonical
  contract, do not duplicate it.
- **No shell wrappers containing business logic.** Launchers are thin.
- **No `|| true` masking of required dependencies.** Optional deps degrade to an
  explicit `unavailable` capability with a reason.
- **No display-only health/counts/progress.** Every status must come from a real
  probe, persisted state or event.
- **No silent chat downgrade** of a failed mission unless an explicit, labeled
  fallback mode is selected.
- **No self-editing the mission core during ordinary runs.**
