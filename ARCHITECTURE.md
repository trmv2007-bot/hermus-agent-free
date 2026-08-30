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
| **World state** | `core.state.WorldStateFacade` | `core.computer.world_state_v2` | one writable path |
| **Mission** | `core.mission.MissionEngine` | mission reports/workspace | only autonomy engine |
| **Jobs** | `core.contracts.Job` + queue | durable job store | lease/heartbeat/reaper |
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
- **Job** — the one durable background-work schema.

---

## 4. Legacy components — relocated to compat / read-only

These were **not deleted** (their consumers and the test suite still reference
them) but are no longer a writable production path for a canonical subsystem. Each
has an explicit removal milestone once its consumers are migrated.

| Legacy | Now | Why it moved |
|---|---|---|
| `core/memory.py` | `core/compat/legacy_memory.py` (read-only) | Memory 2.0 is canonical; v1 is a migration source only |
| `core/computer/world_state.py` (v1) | read-only migration source | `WorldStateV2` is canonical |
| `AutonomousRunner` (`core/autonomous.py`) | behind `mission_runtime_enabled` flag | `MissionEngine` is the only autonomy engine |
| `run_events` / `dashboard_events` / `computer.events` | bridged via `core.events.LegacyEventBridge` | one EventBus is canonical |
| `providers` / `provider_resolver` / `model_fleet` / `router2` / `multi_key` | hidden behind `ModelGateway` | one model-selection path |
| `setup.sh` / `activate.sh` | thin wrappers delegating to `./hermus bootstrap` | one bootstrap |

### Removal milestones (follow-ups)
1. Migrate legacy memory consumers → `core.memory.get_memory()`; delete `core/compat/legacy_memory.py`.
2. Migrate run/dashboard/computer event publishers → `core.events`; delete the legacy event modules.
3. Migrate world-state consumers → `WorldStateFacade`; delete v1.
4. Rebuild the UI shell on `snapshot + replay`; replace the four `dashboard*.html` surfaces.
5. Delete `core/autonomous.py` once no caller uses the legacy runner.

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
