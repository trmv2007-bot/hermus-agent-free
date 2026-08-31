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
| **Delegation** | `subagents.subagent` (facade) `→` `subagent.delegate` Job on `gateway.queue.JobQueue` | job log + results | the queue owns lifecycle; `core.delegation` is only the worker implementation |
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

### 3.1 Delegation execution path (canonical)

Delegation is **entered only through the canonical JobQueue**; there is no second
delegation lifecycle.

```
Agent / Mission (tool: subagent_spawn, delegate_tasks)
   └─ subagents.subagent.delegate / spawn_subagent
        └─ submit_and_wait("subagent.delegate", {goal, tasks, …}, run_id, mission_id)
             └─ gateway.queue.JobQueue ── owns lifecycle
                  ├─ queued → running → succeeded / failed / cancelled
                  ├─ retry (backoff), timeout, cancel, persistence, restart recovery
                  └─ handler: gateway.handlers.make_delegate_handler
                       └─ core.delegation.Delegation.fanout / decompose_and_run   (worker impl)
                            └─ run_subagent_task → HermusAgent
                                 ├─ ModelGateway      (model selection/completion/fallback)
                                 ├─ ToolGateway       (tool execution, permission gate)
                                 ├─ MemoryFacade      (recall/persist)
                                 └─ EventBus          (job.lifecycle + delegation.activity events)
```
* Correlation: the Job carries `mission_id`/`run_id`; the handler passes them into the
  delegation tree, a per-node `DelegationNode` carries `mission_id`/`run_id`/`job_id`/
  `parent_task_id`, and the worker forwards them onto `HermusAgent` and every event.
  When an agent delegates from inside a queued turn, `current_job_context()` inherits the
  parent run/mission/task IDs automatically.
* The sub-agent workers are the transport adapter of the delegation handler (subprocess
  JSON-RPC or the in-process fallback) and are the **only** place that spawns workers;
  they are driven from within a queued `subagent.delegate` Job, never from a tool/route.
* The agent tools never invoke `core.delegation` directly and never call
  `tool_registry.execute` / `memory2` / a provider directly; all of it runs through the
  canonical boundaries.

---

## 4. Legacy components — relocated to compat / read-only / deleted

Clean-slate rule: **one responsibility = one production implementation.** Compatibility
code may exist only during a controlled migration and must have a deletion milestone.
The final tree must not contain two competing implementations.

| Subsystem | State | Detail |
|---|---|---|
| Autonomy | ✅ **DONE** — `core/autonomous.py` **deleted** | `MissionEngine` is the only autonomy engine; disabling the runtime is a **BLOCKED** state, never a silent downgrade. The marker verifier/diagnoser was rehomed to `core/verifiers.py` (feature depth preserved). |
| Events | ✅ **single canonical event authority** | The canonical `core.events.EventBus` (durable, replayable) is the single authoritative source. `dashboard_events` + `computer/events` mirror every event onto it (computer bridge added Phase 1); the dead `core/events/legacy.py` bridge was removed (zero production consumers). The dict APIs remain only as in-memory projections for realtime/SSE consumers. |
| Memory | ✅ **DONE** — `MemoryFacade` is the single writable memory path | The facade owns the typed Memory2 store plus the v1 session/curated/user-model/token backend; no competing process-level v1 singleton. **All app-level `memory2` imports are migrated to the facade** (agent/delegation/integrations/skill_forge/profiles/harness); a gate forbids app-level `memory2` imports outside `core.memory`. The `recall_context` proxy was fixed (was passing a nonexistent `budget=` and silently returning `""`); `KINDS` is exported. |
| World state | ✅ **DONE** — `world_state_v2.py` **deleted** | `core/computer/world_state.WorldState` (v1) is canonical; `core.state.WorldStateFacade` is the single writable path. The dead V2 duplicate (exported only from `__init__`, used by no functional code) was removed. |
| Models | ✅ **verified single stack — no duplicate** | The provider layer (`providers` → `multi_key` → `provider_resolver` → `model_capabilities`) is one mutually-dependent stack with `ModelGateway` (`core/models/gateway.py`) as its single public facade. Each capability (`select_usable_bundle`, `list_available_providers`, `discover_runtime_bundles`, `negotiate`, `select_compatible_model`, `diagnose`) has exactly **one** owner. `router2` (runtime task-type model swap) and `model_fleet` (multi-model distribution) are distinct higher-level features, not duplicates. No competing production implementation exists to delete. **The live runtime now builds its model client through `ModelGateway.llm()`** (construction + router swap); `ModelGateway` exposes the public selection/completion boundary (`complete/chat/stream/select_model/negotiate_capabilities/fallback/health_check/provider_status/model_status`) with structured `ModelGatewayError` + `FailureClass` codes. A behavioral gate runs a real turn and asserts the gateway is used. |
| Tools | ✅ **single invocable boundary — agent routed through it** | `core.tools.ToolGateway` is the only legal invocation path; the agent runtime (`core/agent._execute_tool`) now delegates to it (policy gate + typed `ToolResult` + canonical event emission), with `ToolRegistry` as the shared implementation. Gates enforce no agent bypass. |
| Verification / capability | ✅ **integration + behavioral gates** | Canonical owners (MissionEngine/ModelGateway/ToolGateway/JobQueue/MemoryFacade/EventBus) drive realistic offline flow tests (`tests/test_capability_flows.py`, Tasks 1-8) and 31 architecture gates (`test_architecture_gates.py`) + 3 behavioral proofs (`test_behavioral_boundary_proof.py`, which run a real agent turn and assert ModelGateway / ToolGateway / MemoryFacade were actually used) + the delegation-connectivity proof (`tests/test_delegation_connectivity.py`). Full suite: **684 passed, 2 skipped, 16 warnings (0 failures)**. |
| Security | ✅ **CORS secure-by-default** | Replaced `allow_origins=['*'] + allow_credentials=True` with a restricted default allow-list, credentials OFF unless explicitly opted in, wildcard forcing credentials OFF. No test depended on the old behavior. |
| Setup | 🟡 `setup.sh`/`activate.sh` thin wrappers → `./hermus bootstrap` | one bootstrap. |

### Deletion milestones (follow-ups)
1. ~~Migrate world-state consumers → `WorldStateFacade`; delete v1.~~ ✅ done (deleted the dead `world_state_v2` duplicate; v1 is canonical).
2. ~~Migrate run/dashboard/computer event producers + consumers to `EventEnvelope`; delete the legacy event modules.~~ ✅ **done** — the canonical `EventBus` is the authority; `dashboard_events` + `computer/events` mirror onto it; the dead `core/events/legacy.py` bridge was removed. `run_events.RunBus` retained as the genuine per-run live-stream/steer/cancel owner (mirrors onto canonical bus).
3. Migrate legacy memory consumers → `core.memory.get_memory()` entirely; retire `core/compat/legacy_memory.py` as a public path (facade already owns the v1 backend).
4. ~~Collapse `providers`/`model_fleet`/`router2`/`multi_key`/`provider_resolver` behind `ModelGateway`.~~ **resolved — no duplicate found.** Detect-first analysis verified these are ONE mutually-dependent provider stack, not competing implementations; `ModelGateway` is the single facade. No deletion warranted. (Future nicety, not a deletion: have the live runtime's model-selection path also go through `ModelGateway`.)
5. ~~Rebuild the UI shell on `snapshot + replay`; replace the four `dashboard*.html` surfaces.~~ ✅ **done** — the single production control room is `/control`; root `/` → `/control`. Real feature depth is preserved through the backend APIs it drives (`/computer/*`, `/remote/*`, `/api/jarvis/status`, `/events/recent`, `/dashboard/status`, `/dashboard/events`, `/jobs`, `/api/v1/*`, `/doctor/*`, `/speech/*`). The four legacy HTML surfaces + the `living-deck.*`/`hermus-client.js`/`jarvis-control.js` assets and the `/dashboard*`, `/jarvis`, `/dashboard-assets/*`, and the HTML-only `/computer/dashboard` + `/remote` routes were **deleted**.
6. ~~Delete duplicate agent managers / orchestration concepts with no distinct contract.~~ ✅ **done** — `AgentManager` is now a thin named-agent registry + delegation facade; its detached subprocess `worker_loop`, and its own `state.json` heartbeat + `jobs/*.json`/`results/*.json` lifecycle were removed. Background work is executed by the canonical Job queue.

### Remaining (Final One-Shot Spec §3/§7/§16)
1. ~~**Control-room cleanup**~~ ✅ **done** — `/control` is the only production control room; root → `/control`; legacy dashboard HTML/JS/routes deleted; all real capability preserved through backend APIs.
2. ~~**Events**~~ ✅ **single canonical event authority** — the canonical `core.events.EventBus` is the durable/replayable source; `dashboard_events` + `computer/events` mirror every event onto it (computer bridge added). The dead `core/events/legacy.py` bridge (`publish_legacy`/`LegacyEventBridge`) was removed — it had zero production consumers. `run_events.RunBus` kept as the genuine per-run live-stream/steer/cancel owner that also mirrors onto the canonical bus.
3. ~~**Memory**~~ ✅ **single public writer** — `MemoryFacade` (`core.memory.get_memory`) is the only public writable path; `core.compat.legacy_memory` is a private backend owned by the facade (intentionally retained, not a duplicate) and no production code imports it outside `core.memory` (enforced by gate).
4. ~~**Models**~~ ✅ **static boundary gate** — `tests/test_architecture_gates.py::test_one_model_boundary_no_direct_provider_sdk` rejects any direct provider SDK import outside the canonical model subsystem; `ModelGateway` is the public selection facade and `routes_canonical` uses it. (Enforced by gate.)
5. ~~**Setup**~~ ✅ **done** — one idempotent `bootstrap`/`start`/`doctor`. `bootstrap.py` distinguishes required vs optional deps and fails truthfully on missing required modules; `setup.sh` handles OS packages then delegates to the bootstrap; `activate.sh`/launchers are thin (no business logic, no `|| true` masking of required deps). Setup-contract gates added.
6. ~~**Android control subsystem (Spec §16–19) backend**~~ ✅ **built** — `core.android` is the single Android boundary: `AndroidTool` (facade) reached via `ToolGateway` → `android_*` tools and `/android/*` API. Real `AdbAndroidTransport` (screencap/uiautomator/tap/text/keyevent/am start) + signed companion-bridge transport; explicit consent (denied by default) + configurable allowed-ops allowlist; HMAC-SHA256 secure pairing/sign/verify; append-only audit log + EventBus mirror; honest `android_control_unavailable` reporting. ⚠️ **Device/emulator E2E remains UNTESTED** — it requires a live device + the Android Agent Companion app and is never marked WORKING on mocks.
7. ~~**Android Agent Companion (on-device half) + end-to-end control**~~ ✅ **reference built + agentic loop proven** — `android_companion/` (native Kotlin/Gradle: signed bridge server on loopback `127.0.0.1:8080`, accessibility `DeviceController`, `MediaProjection` `ScreenCapture`, consent `PairingActivity`) uses only documented permission-gated APIs (no security bypass). The backend Android path was fixed in the integration pass: ADB transport now reads binary screenshots safely (§8), retrieves the real UI tree by dumping then cat-ing the XML and parsing it (§9), resolves the launcher activity for app launch (§10), the singleton provisions a real transport via `build_default_transport()` (§7), and the bridge transport enforces loopback/HTTPS in code (§13). `core/android/simulate.py` implements the real `AndroidTransport` interface on a deterministic "device" so the full observe → reason → act → verify → continue loop is exercised through the real `ToolGateway`; `core/android/observe.py` (semantic observation — reason over labels/buttons/fields, not raw coords); `core/android/verify.py` (before/action/after). `tests/test_android_agentic_loop.py` proves the loop; `tests/test_android_subsystem.py` (20) covers the fixed ADB/UI/launch/factory/bridge paths. ⚠️ **Physical device/emulator + live-model E2E remain NOT VERIFIED** (no SDK/device/keys here); exact steps in `FINAL_REPORT.md` §52.
7. ~~**Restart/resume (Spec §13)**~~ ✅ **done** — `MissionEngine.load_mission()`; restart tests (kill worker → fresh engine loads → resumes → completes; duplicate-execution prevented; cancel state survives). **Host-level computer E2E remains UNTESTED** (guarded test skips without pyautogui + a real display); computer *capability* is reported honestly (`computer_control_unavailable` when real control is unavailable). Provider-E2E on a real API key remains UNTESTED (no keys in this environment).

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
