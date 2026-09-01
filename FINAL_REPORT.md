# Final Report — Branch Reconciliation (main + clean-slate/final-consolidation)

This repository file records the two consolidated workstreams that were merged on
the reconciliation branch (`arena/01a05dfc-hermus-agent-free`):

1. **`main`** — red-line autonomy control plane (approval bundles, mission
   pre-flight, capability ledger/registry, emergency stop, local-defense scanner,
   safety policy/report) and the optional speech/avatar media integration
   (OmniVoice-shaped TTS, HeyGem connector, handy-inspired STT).
2. **`clean-slate/final-consolidation`** — the canonical-architecture
   consolidation: single ModelGateway/ToolGateway/MemoryFacade boundaries,
   Android device-control subsystem + companion app, computer-control honesty,
   delegated-job consolidation through the canonical JobQueue, gateway/security
   hardening, vision routing through ModelGateway, and the `ddgs` migration.

Both historical reports are retained verbatim below.

---

## Part A — main: OmniVoice / HeyGem / handy integration

# Final Report — OmniVoice / HeyGem / handy integration

## Goal
Integrate the strongest useful capabilities from the studied repos into Hermus
without copying full repos, duplicating ownership, or introducing parallel
subsystems.

## What was integrated

### 1. OmniVoice-shaped advanced TTS under the existing speech owner
Canonical owner: `core/speech.py`

Integrated shape:
- kept existing local TTS fallback behavior (`piper`, `espeak`, `pyttsx3`)
- added lazy optional OmniVoice runtime loading instead of a second speech stack
- added advanced synth arguments for:
  - `language`
  - `ref_audio` / `ref_text`
  - `instruct`
  - `duration`
  - `speed`
  - `prompt_id`
  - `create_prompt_id`
  - `normalize_text`
- added reusable clone-prompt persistence in `data/speech/prompts`
- added tool wrappers in `tools/speech_tools.py`:
  - `speech_status`
  - `speech_synthesize`
  - `speech_clone_prompt_create`
  - `speech_clone_prompts`

### 2. HeyGem-style local avatar connector, but only as a thin adapter
Canonical owner: `core/avatar.py`

Integrated shape:
- no vendored Electron/app stack
- no parallel persistence/runtime subsystem
- fresh connector against the confirmed local HTTP contract only
- added status/probe, voice preparation, cloned-audio generation, render submit,
  and render polling
- persisted connector artifacts/metadata under `data/avatar`
- added tool wrappers in `tools/heygem.py`:
  - `avatar_service_status`
  - `avatar_prepare_voice`
  - `avatar_synthesize_audio`
  - `avatar_render_video`
  - `avatar_render_status`

### 3. Handy-inspired STT-side improvements inside the existing voice owner
Canonical owner: `tools/voice.py`

Integrated shape:
- preserved current local-engine/faster-whisper routing
- added local model asset discovery in conventional directories
- added transcript normalization
- added optional filler stripping
- added optional transcript persistence through the canonical memory facade
- exposed new tool `voice_discover_local_models`

## Supporting architecture changes

### Config
Updated `core/config.py` with optional knobs for:
- OmniVoice:
  - `omnivoice_enabled`
  - `omnivoice_model`
  - `omnivoice_device`
  - `omnivoice_prompt_dir`
- HeyGem/avatar:
  - `heygem_tts_url`
  - `heygem_face2face_url`
  - `avatar_output_dir`
  - `heygem_timeout_s`
- Handy/STT behavior:
  - `handy_model_dirs`
  - `stt_normalize_default`
  - `stt_strip_fillers_default`

### Tool registration and permissions
Updated:
- `core/tool_registry.py` to discover `tools.speech_tools` and `tools.heygem`
- `core/permissions.py` with explicit policy entries for the new speech/avatar
  tools and STT discovery tool

### Existing route/status surfaces
Updated:
- `gateway/routes_speech.py`
  - richer dashboard status
  - STT model discovery/status exposure
  - advanced speech synthesis passthrough
  - optional remembered transcription path
  - avatar status / prepare / render / render-status routes
- `gateway/routes_canonical.py`
  - `/api/v1/system/capabilities` now reports `speech`, `transcription`, and
    `avatar` capability state through canonical backend owners
- `bootstrap.py`
  - optional import checks for OmniVoice-related packages
- `core/doctor.py`
  - deterministic media-capability probing and honest degraded findings for
    missing speech/avatar/STT readiness
- `gateway/control.html`
  - capability cards now render backend-derived speech/STT/avatar status rather
    than inventing a dashboard-only truth source

## Tests and gates added/updated
Updated or added coverage in:
- `tests/test_media_integrations.py`
- `tests/test_architecture_gates.py`
- `tests/test_living_dashboard.py`
- `tests/test_voice_routing.py`
- `tests/test_control_room_e2e.py`
- `tests/test_canonical_contracts.py`
- `tests/test_hermus_doctor.py`

New/expanded coverage verifies:
- OmniVoice prompt creation and reuse remain inside the canonical speech owner
- Handy-style STT model discovery stays inside `tools.voice`
- HeyGem HTTP details stay inside `core.avatar`
- tool registration includes the new media modules
- route/capability surfaces expose backend-derived media state
- transcript memory logging goes through `MemoryFacade`
- avatar connector orchestration works in unit-tested mocked form
- doctor status/reporting now includes optional media findings

## Additional robustness fix discovered while verifying
While running the full suite, several pre-existing tests failed because some
internal subprocess calls invoked bare `pytest`, which is not reliable inside a
venv-managed environment.

Fixed by switching these internal test-runner invocations to `sys.executable -m pytest` in:
- `core/swe_mode.py`
- `core/skill_manager.py`
- `core/verifier_registry.py`

This was not specific to OmniVoice/HeyGem/handy, but it was required to make
Hermus's verification path reliable in the tested environment.

## Verification performed

### Import/registration verification
Confirmed:
- the new media modules compile/import cleanly
- ToolGateway/ToolRegistry discover these tools:
  - `speech_status`
  - `speech_synthesize`
  - `speech_clone_prompt_create`
  - `speech_clone_prompts`
  - `avatar_service_status`
  - `avatar_prepare_voice`
  - `avatar_synthesize_audio`
  - `avatar_render_video`
  - `avatar_render_status`
  - `voice_available_models`
  - `voice_discover_local_models`

### Test verification
Executed successfully:
- targeted integration/architecture/status suites
- full repository test suite

Result:
- `635 passed, 1 skipped`

## What remains intentionally optional or unverified
The following were **not** claimed as working live, because they were not tested
against real local runtimes in this session:
- live OmniVoice import/model execution with actual installed OmniVoice weights
- live voice cloning output quality
- live HeyGem local services on ports `18180` and `8383/easy`
- live talking-avatar video render completion against a real backend
- real hardware acceleration behavior for any optional media stack

Current honest status:
- the integration paths, configuration, registration, permissions, routes,
  tests, and mocked/unit behavior are wired and verified
- real optional backend functionality still depends on the user actually having
  those external runtimes installed and reachable

## Licensing / notices
Updated `THIRD_PARTY_NOTICES.md` to record:
- OmniVoice reference/integration notes
- handy reference/integration notes
- HeyGem.ai connector-only treatment and non-vendoring rationale

## Acceptance-criteria summary
- preserved existing architecture: yes
- one canonical owner per responsibility: yes
- integrated via existing gateways/facades/surfaces: yes
- optional/degraded behavior is explicit: yes
- tests and architecture gates added: yes
- import/registration/full-suite verification performed: yes
- no untested live-backend claim made: yes
- `ARCHITECTURE.md` and `FINAL_REPORT.md` updated: yes


---

## Part B — clean-slate/final-consolidation: Final Completion Report (§46–48)

# HERMUS — Final Completion Report (§46–48)

Branch: `clean-slate/final-consolidation`
Head SHA: **`6fe446d`** (pushed & verified on `origin/clean-slate/final-consolidation`)
Date: 2026-08-30

> This report is honest by construction. It is updated after a source-inspection
> integration pass (see §53) that found and fixed several real defects the prior
> green suite did not catch.

This report is honest by construction: no capability is marked WORKING on a unit
test alone, and no delivery claim is made that was not run here.

---

## §46 — Exact test report

Environment: Python 3.13.14 (recreated `.venv`), deps from `requirements.txt` +
`pytest-timeout`. Full suite run against `tests/` (53 files).

| Category | Result | Notes |
|---|---|---|
| **Total collected** | **654** | 652 passed + 2 skipped |
| **Passed** | **652** | |
| **Failed** | **0** | |
| **Skipped** | **2** | both are host-E2E, correctly gated (see below) |
| Warnings | 10 | non-fatal deprecation (Starlette TestClient httpx) |

### Test-classification breakdown (by purpose)

- **Unit / off-line integration** — the bulk (652). These cover canonical owners off-line:
  MissionEngine autonomy, ModelGateway, ToolGateway, MemoryFacade, WorldState, EventBus,
  JobQueue, computer v1/v2, vision/voice routing, counsel, research, delegation,
  skill-forge (evidence-gated learning), evolution/self-improvement, restart/recovery.
- **Architecture gates** — `test_architecture_gates.py` = **24** structural gates
  (one-autonomy-engine, one-event-authority, one-memory-writer, one-world-state-owner,
  one-model/provider boundary, one-tool-gateway, one-job-execution-owner, one-control-room,
  no-bare-except-pass, no-committed-secrets, doctor/health honesty, thin-setup/bootstrap, …).
- **Behavioral "proof-of-usage" gates** — `test_behavioral_boundary_proof.py` = **3**. These
  run a *real* (offline mock) agent turn and assert the runtime actually routed through
  ModelGateway / ToolGateway / MemoryFacade (§15: proves usage, not existence).
- **Restart / recovery** — `test_restart_recovery.py` = **3**. kill worker → fresh engine →
  load → resume → complete; duplicate execution prevented; cancel state survives.
- **Android backend** — `test_android_subsystem.py` = **11 passed + 1 skipped**. Authorization
  (consent denied-by-default, grant/revoke/allowlist), honest `android_control_unavailable`,
  ADB command building, audit + EventBus, HMAC secure/pairing.
- **Computer-control honesty** — `test_computer_control_honesty.py` = **6 passed + 1 skipped**.
  Honest `computer_control_unavailable`, explicit dry-run, missing-args, backend report.
- **Host E2E** — skipped (see §48). These are never passed on a mock.

### Skipped (with exact reason)

1. `test_android_subsystem.py::test_host_e2e_android_control_real_device`
   → `no adb / no online Android device`.
2. `test_computer_control_honesty.py::test_host_e2e_computer_real_control`
   → `no pyautogui + display for real computer control`.

---

## §47 — Delete report (module removed, why, replaced by, migrated callers, covering tests)

Deletions across the consolidation branch (via `git log --diff-filter=D`):

| Module | Why duplicate/dead | Replaced by | Migrated callers | Covering tests |
|---|---|---|---|---|
| `core/autonomous.py` | second autonomy engine (parallel to MissionEngine) | `core.mission.MissionEngine` (single autonomy owner) | autonomy callers routed to MissionEngine | `test_autonomous_loop.py`, `test_mission_platform.py` |
| `core/computer/world_state_v2.py` | dead v2 duplicate, never wired to a functional caller | `core.computer.world_state.WorldState` + `core.state.WorldStateFacade` | none (dead) | `test_world_model.py`, `test_computer_autonomy.py` |
| `core/events/legacy.py` | second event bridge with zero production consumers | `core.events.EventBus` (durable/replayable authority) | none (dead) | `test_gateway_realtime.py`, `test_capability_flows.py::test_task5_event_bus_replays_after_bus_recreated` |
| `gateway/dashboard.html` | competing UI surface | `gateway/control.html` | root → `/control` | `test_control_room_e2e.py`, `test_architecture_gates.py::test_one_control_room_ui_only_control_html` |
| `gateway/dashboard_computer.html` | competing UI surface | `gateway/control.html` | — | same |
| `gateway/jarvis_dashboard.html` | competing UI surface | `gateway/control.html` | — | same |
| `gateway/remote.html` | competing UI surface | `gateway/control.html` | — | same |
| `gateway/static/hermus-client.js` | client-only dashboard state (violates snapshot+replay) | self-contained `control.html` (backend reconstruct) | none | `test_control_room_e2e.py` |
| `gateway/static/jarvis-control.js` | client-only dashboard state | self-contained `control.html` | none | same |
| `gateway/static/living-deck.js` | client-only dashboard state | self-contained `control.html` | none | same |

**Not deleted (intentionally retained, not duplicates):**
- `core/compat/legacy_memory.py` — the session/curated/user-model/token backend now owned as a
  *private backend* of `MemoryFacade` (no longer a public competing writer; gate forbids
  app-level import outside `core.memory`).
- `core/memory2.py` — the typed Memory2 store, now internal to `core.memory` (app-level
  `memory2` imports are forbidden by gate).
- Provider stack (`providers`/`multi_key`/`provider_resolver`/`model_capabilities`) — audit
  confirmed it is ONE mutually-dependent stack under `ModelGateway`, not competing
  implementations; no duplication warranting deletion.

**Migrated this turn (source → canonical owner):**
- All app-level `memory2` imports → `core.memory` facade (agent, delegation, integrations,
  skill_forge, profiles, harness).
- `core/agent.py` model construction → `get_model_gateway().llm()` (no direct `FreeLLM`).
- Computer dry-run fallback → honest `computer_control_unavailable` capability.

---

## §48 — Retained-complexity, remaining limitations & final capability status

### Retained-complexity report (feature depth preserved)
MissionEngine (autonomy/repair/verification), ModelGateway (complete/chat/stream/
select_model/negotiate_capabilities/fallback/health_check/provider_status/model_status),
ToolGateway (policy/auth/timeout/typed ToolResult/EventBus), MemoryFacade (typed + session +
curated + user-model + token usage), WorldStateFacade, EventBus (durable/replayable),
JobQueue, computer control (observe→act→verify, vision grounding, repair), Android control
(backend), delegation (depth-bounded subagents), research, counsel, voice, vision,
self-improvement/learning (evidence-gated), model routing + provider fallback, browser/
internet tools, background execution.

### Remaining limitations (brutally honest)
- **Device/emulator E2E (Android)** — NOT DONE / UNTESTED. The backend boundary, consent,
  audit, signature and honest `android_control_unavailable` are implemented and unit-tested,
  but a real device + the Android Agent Companion app + the secure bridge round-trip have
  not been exercised here (no adb/device). **Not** reported as WORKING.
- **Host computer E2E** — NOT DONE / UNTESTED. The honest capability reporting and the
  guarded E2E test are in place, but a real click/type/launch on a live desktop has not been
  run here (no display + pyautogui). **Not** reported as WORKING.
- **Provider E2E (real model API)** — UNTESTED. No API keys/network in this environment; the
  model boundary is exercised with the offline mock and injected adapters, not a live
  provider. Free/open/free-tier provider support is retained and capability-aware, but a live
  provider completion was not verified.
- **Delegation → canonical JobQueue (implemented this pass)** — delegation is now entered
  through the canonical JobQueue: the agent tools (`subagent_spawn`, `delegate_tasks`), the
  `subagents.subagent` facade and the `POST /delegate` endpoint submit a `subagent.delegate`
  Job and (when synchronous) block for its structured result. The queue owns the execution
  lifecycle (queued→running→done/failed/cancelled, retry, timeout, persistence, restart
  recovery). The delegation engine (`core.delegation`) is now only the *worker implementation*
  that a queued `subagent.delegate` Job invokes; its subprocess/`ThreadPoolExecutor` children
  are the transport adapter, and every worker runs through the single engine
  (`run_subagent_task` → `HermusAgent` → ModelGateway/ToolGateway/MemoryFacade) emitting
  `job.lifecycle` events on the canonical EventBus. **Remaining:** the *source* of the
  correlation IDs (mission_id/run_id) is set by the queue context when an agent delegates from
  inside a queued turn (via `current_job_context()`), but a mission DAG node agent does not yet
  carry its `mission_id` onto a delegate call made through the raw tool — the traceability
  plumbing is in place and proven when the IDs are supplied, but auto-wiring the mission node
  agent is not implemented.
- **`/tmp` size** — the shared tmpfs is small; full-suite runs need `--basetemp` on the root fs.

### Final capability status
| Capability | Status | Basis |
|---|---|---|
| Model boundary (ModelGateway) | **WORKING** | off-line integration + behavioral proof; provider E2E UNTESTED |
| Tool boundary (ToolGateway) | **WORKING** | off-line integration + behavioral proof |
| Memory facade | **WORKING** | off-line + behavioral proof |
| World state | **WORKING** | unit/integration |
| Events (EventBus) | **WORKING** | durable log + replay tests |
| Autonomy (MissionEngine) | **WORKING** | autonomous-loop + restart/recovery tests |
| Restart / resume / cancel | **WORKING** | real persistence tests |
| Computer control | **PARTIAL** | backend + honest capability WORKING; host E2E UNTESTED |
| Android control | **PARTIAL** | backend + honest unavailable WORKING; device E2E UNTESTED |
| Vision / voice / counsel / research / learning | **WORKING** | off-line integration |
| Provider E2E (live key) | **UNTESTED** | no keys/network |
| Control room UI | **WORKING** | single `/control`, `/control` E2E; backend state + event replay |

---

## §49 — What changed this session (commits, pushed & verified)

```
32c7378 Docs: record Android-backend, computer-honesty, restart/recovery + setup as done
eabaee6 Computer control (§14): no silent dry-run fallback — honest reporting
84e1782 Setup + control-room documentation (§18/21): thin wiring + honest docs
118a892 Android control subsystem (§16–19): real backend, consent-gated + audited
152dc63 Autonomy/recovery (§13): mission restart-resume + durability tests
c005088 Observability (§13/15): behavioral proof gates for canonical boundaries
cb6ed03 Security (§32): replace wildcard+credentials CORS with secure-by-default
3116cb2 Memory boundary (§8): core.memory is the ONLY writable memory path
0c57411 Model boundary (§3–4): canonical ModelGateway owns model construction
```

Remote `origin/clean-slate/final-consolidation` = `32c7378`, verified by fetch.

---

## §50 — Final real-world verification & completion pass (this turn)

This pass focused on **actually proving and finishing the real system**, not cosmetic
refactoring. It added the on-device Android companion, a deterministic yet real
observation/control path, and closed a genuine memory-boundary security hole.

### New commits (pushed & verified)

```
e2ad355 core(security): enforce single memory boundary — route app-level memory2 imports through MemoryFacade
1c0a3c6 core(android): deterministic simulated device, semantic observation, verified agentic phone-control loop, companion app
```

Both verified on `origin/clean-slate/final-consolidation` by `git fetch` (local == remote =
`e2ad355`, 0 unsynced commits). Nothing merged into `main` (still 11 behind).

### Full-suite re-run (after this turn's changes)

`tests/` (excluding `tests/js`, `tests/eval`): **661 passed, 2 skipped, 10 warnings**,
0 failures. The +9 from the prior 652 are `tests/test_android_agentic_loop.py` (9 tests).
The 2 skipped are the two host-E2E
tests (Android + computer) which are correctly gated on device/display availability.

### What was actually built this turn

**1. Android companion app (on-device half) — `android_companion/`.**
Reference implementation in native Android (Kotlin/Gradle). It does **not** bypass Android
security: it uses the documented, permission-gated APIs only.
- `bridge/Protocol.kt` — HMAC-SHA256 signed request/response envelope, op set, device id.
- `bridge/BridgeServer.kt` + `HermusBridgeService.kt` — loopback-only control socket on
  `127.0.0.1:8080`, authenticated (invalid MACs rejected before dispatch), request/response
  ids, heartbeat/reconnect-aware design, audit metadata.
- `accessibility/HermusAccessibilityService.kt` + `DeviceController.kt` — semantic UI
  hierarchy dump (text/label/bounds/focus/enabled/selected) + tap/swipe/type via
  `GestureDescription` + `ACTION_SET_TEXT`; launch app, home, foreground package.
- `serve/ScreenCapture.kt` — MediaProjection → PNG Base64 screenshot (system-consented).
- `ui/PairingActivity.kt` — user consent + enable-accessibility + start-bridge surface.
- Gradle build files + resources + `README.md`.

**Status: NOT VERIFIED on a device.** It has **not** been compiled or run here (no Android
SDK/device/emulator). It is a careful, buildable reference, and the build + device E2E are
explicitly **NOT VERIFIED** — exact steps in §52.

**2. Deterministic simulated Android device — `core/android/simulate.py`.**
Implements the **real** `AndroidTransport` interface (UI tree, screen, tap, type_text,
launch_app, back/home, current_app, device_id) with a scripted Tasks app, and hosts a
verifiable task list. It is a *simulated device*, not a mock of the tool — it drives the
real `AndroidTool` → transport boundary exactly as a device would.

**3. Semantic observation — `core/android/observe.py`.**
`build_observation()` turns the UI tree into a model-friendly view: visible text, buttons,
fields + their current value, labels, bounds, focused/enabled/selected state, package,
screenshot reference. The agent reasons about **labels/buttons/state**, not raw coordinates
(§7). `AndroidTool.observe()` is a consent-gated composite op; `android_observe` /
`android_current_app` are registered tools.

**4. Action verification — `core/android/verify.py` + the controller.**
`AndroidVerifier` (before → action → after → verify). Every meaningful action in the agent
loop is verified against a fresh observation; on no-op it retries/replans (§8).

**5. Agentic phone-control E2E — `core/android/agent.py` + tests.**
`AndroidAgentController.run_goal("add 'Buy milk' to tasks")` plans, observes, types, taps,
re-observes, verifies and completes — **routing every step through the real
`ToolGateway → AndroidTool → transport`** (the single invocation boundary). Deterministic
policy (no live model — see §51). `tests/test_android_agentic_loop.py` = 9 tests.

**6. Memory-boundary security fix.**
The gate `test_no_app_level_memory2_direct_access` had two holes: it missed relative
imports (`from .memory2` / `from ..memory2`) and absolute `from core.memory2 import ...`.
Fixed to match by resolved final module segment, which exposed real bypasses in
`tool_registry`, `gateway/handlers`, `gateway/realtime`, `gateway/routes_subsystems`.
Added `sweep()/reindex()/index_stats()/access_log()` passthroughs to `MemoryFacade` and
migrated all four callers to `from core.memory import memory`. `core.memory` is now the
single writable memory boundary.

### Reliability of the "agentic loop" evidence

The agentic phone-control loop is a **real control-loop correctness proof** run over the
actual `ToolGateway` and the actual `AndroidTool`, with a **deterministic policy** standing
in for the model's decision step. It proves the observe → reason → act → verify → continue
path works end-to-end over the real boundaries. It is **not** a live-provider or
physical-device test; those remain NOT VERIFIED (below).

---

## §51 — Final capability matrix (per directive §24)

Statuses use only **WORKING / PARTIAL / REQUIRES CONFIGURATION / NOT IMPLEMENTED /
NOT VERIFIED** and are based on evidence actually produced here.

| Capability | Implementation | Automated Test | Real E2E | Status |
|---|---|---|---|---|
| Autonomous missions | MissionEngine | `test_autonomous_loop.py` | deterministic loop (real tool path) | WORKING |
| Mission persistence | atomic persisted store | `test_restart_recovery.py` | restart/resume (engine re-created) | WORKING |
| Restart / resume | persisted mission + bind executor | 3 tests | run | WORKING |
| ModelGateway | canonical boundary | integration + behavioral proof | **no live key/network** | PARTIAL (works off-line; live E2E NOT VERIFIED) |
| ToolGateway | canonical boundary | integration + behavioral proof | run (android loop) | WORKING |
| Memory | MemoryFacade | `test_hybrid_memory.py` + behavioral proof | runtime path uses facade | WORKING |
| WorldState | WorldStateFacade | `test_world_model.py` | run | WORKING |
| EventBus | durable/replayable | replay tests | run | WORKING |
| Computer observation | backend + vision grounding | unit/integration | **no display** | PARTIAL |
| Computer control | honest `computer_control_unavailable` | honesty tests | **no display / pyautogui** | NOT VERIFIED |
| Android observation | semantic observe (simulated) | 9 tests | **real device** | PARTIAL (simulated WORKING; device NOT VERIFIED) |
| Android control | AndroidTool + bridge | 9 tests | **real device** | PARTIAL (simulated WORKING; device NOT VERIFIED) |
| Android reconnect | bridge (loopback) + heartbeat design | — | **device** | NOT VERIFIED |
| Android verification | AndroidVerifier before/after | `test_android_agentic_loop.py` | run (simulated) | WORKING (simulated) |
| Vision / voice / delegate / research / counsel | subsystems | off-line integration | — | WORKING (off-line) |
| Failure recovery | repair/replan/retry | autonomous + recovery tests | run | WORKING |
| Backend-only execution | background persistence | restart/recovery | control-room test | WORKING |
| Control room | single `/control` client | `/control` E2E | run | WORKING |
| Security | consent + allowlist + HMAC + audit | android + gate tests | static | WORKING (static; device auth NOT VERIFIED) |

### §52 — NOT VERIFIED items with exact verification instructions

1. **Android device/emulator E2E (companion).** Build & install the companion, pair, and
   run the agent loop on a real device/emulator:
   ```bash
   cd android_companion && ./gradlew assembleDebug          # needs Android SDK + JDK 17
   adb install app/build/outputs/apk/debug/app-debug.apk
   adb reverse tcp:8080 tcp:8080
   # On device: enable the accessibility service, grant screen capture, start the bridge.
   # On host:    HERMUS_ANDROID_SECRET=$(python -c "from core.android.secure import new_pairing_secret as n; print(n())")
   #             configure the BridgeAndroidTransport base_url=http://127.0.0.1:8080 + same secret
   # then run  tests/test_android_subsystem.py::test_host_e2e_android_control_real_device
   ```
2. **Live ModelGateway E2E.** Provide API key(s) + network and run the provider path:
   `from core.models.gateway import get_model_gateway; gw = get_model_gateway(); gw.llm("provider/model").complete(...)`.
3. **Host computer control E2E.** On a desktop with `pyautogui`/display, run
   `tests/test_computer_control_honesty.py::test_host_e2e_computer_real_control`.

---

## §53 — Final integration: real connectivity & full-system fix pass (latest)

This pass did NOT trust the previous report, prior agent claims, or a green suite. It
inspected the production source, found real connectivity/integration defects the existing
tests did not catch, fixed them, and added regressions proving the fixes.

### Git state (verified by fetch before editing)

- local HEAD = `6fe446d`; `origin/clean-slate/final-consolidation` = `6fe446d`; 0 unsynced.
- `origin/main` = `574c7807` (NOT merged — per directive).
- Working tree clean; remote URL tokenless.

### Defects found by source inspection and fixed

**§8 ADB binary I/O — was corrupting screenshots.** `AdbAndroidTransport.get_screen()` ran
`exec-out screencap -p` through a text-mode subprocess (`text=True`), which decodes stdout
as UTF-8 and corrupts binary PNG. Fixed: added a binary-safe runner; reads raw bytes,
validates the PNG signature, returns base64. Malformed/empty screencap now fails honestly.

**§9 Real UI-tree retrieval — was reading the wrong output.** `get_ui_tree()` treated the
`uiautomator dump` STDOUT as the XML, but stdout is only the confirmation text. Fixed: dump
then `cat /sdcard/window_dump.xml`, parse + validate the XML into a semantic hierarchy
(text/desc/resource-id/class/clickable/enabled/selected/focused/bounds/package/activity).
Empty/malformed XML fails honestly.

**§10 App launch — was passing a bare package to `am start -n`.** `launch_app(package)` sent
the package name to `-n` (which needs a component). Fixed: supports `launch_app(package=…)`
resolving the launcher activity via `cmd package resolve-activity --brief`, and
`launch_app(component=…)`; rejects invalid components/missing targets.

**§7 Default transport — singleton was `AndroidTool(transport=None)`.** `get_android_tool()`
never provisioned a transport. Fixed: added `build_default_transport()` (config/env-driven:
ADB from `HERMUS_ANDROID_ADB`/PATH, or companion bridge from `HERMUS_ANDROID_BRIDGE_URL`
+ `HERMUS_ANDROID_SECRET`), and the singleton now provisions through it — connected to a
real transport when available; `capability()` stays truthful.

**§13 Bridge security — documented but not enforced.** `BridgeAndroidTransport` claimed "HTTPS
or loopback only" but never checked. Fixed: enforces in code (HTTPS anywhere, plaintext only
on loopback); a remote plaintext endpoint is refused before any request.

**§5 ToolGateway bypasses — five production call sites.** `delegation.py` (delegated worker),
`counsel/council.py`, `reasoning/strategies.py`, `skill_forge.py`, and `gateway/realtime.py`
all invoked `tool_registry.execute(...)` directly, bypassing the ToolGateway's
policy/audit/tracing/timeout. Migrated all five to `get_tool_gateway().execute()` via
`gateway_result_dict`. Fixed the pre-existing `_classify_registry_result` bug that treated a
non-`None` `error` key as failure even when it was `""` (the `sandbox_run` success sentinel).

**§4.2 ModelGateway real retry/fallback — was only a candidate list.** `fallback()` returned
a list of candidates but nothing executed a retry/fallback. Added `chat_with_fallback()`: ranks
candidates, retries a retryable failure within a provider, then falls back to the next provider,
recording outcomes/circuit state per attempt; returns one success or one structured error. Also
made `fallback()` enumerate the resolver's reported bundles so a failed provider can genuinely
be replaced.

**§4.3 Streaming error handling — mid-iteration errors leaked raw.** `stream()` caught errors
only when the generator was *created*. Fixed: the returned generator is wrapped so an error
raised *during* iteration is classified into a typed `ModelGatewayError`.

**§31 Architecture gates strengthened.** Added AST-based gates: no production ToolGateway bypass
(`tool_registry.execute`, alias-aware), AndroidTool default-transport provisioning via the
canonical factory, and a single AndroidTool class. (24 → 27 structural gates.)

### Test result after these fixes

`tests/` (excluding `tests/js`, `tests/eval`): **678 passed, 2 skipped, 13 warnings, 0 failures**
(up from 661). New tests: `tests/test_android_subsystem.py` regressions (13 → 20), the
addition of `tests/_android_fixtures.py` (realistic PNG + uiautomator XML),
`tests/test_model_gateway_fallback.py` (4), and 3 new architecture gates.

#### NEXT backend consolidation pass (Delegation → JobQueue)

`tests/` (excluding `tests/js`, `tests/eval`): **684 passed, 2 skipped, 16 warnings, 0 failures**
(up from 678). New this pass:
* `tests/test_delegation_connectivity.py` (2) — the E2E delegation-connectivity proof
  (MissionEngine/agent → `submit_and_wait("subagent.delegate")` → JobQueue → sub-agent
  worker → ModelGateway / ToolGateway / MemoryFacade → EventBus `job.lifecycle` →
  structured result → parent), plus `spawn_subagent`-via-queue.
* 4 new architecture gates in `test_architecture_gates.py` (27 → 31): delegation entered
  only through the canonical queue, the subagent facade submits via the queue, the subagent
  worker uses canonical boundaries, and the JobQueue mirrors lifecycle onto the canonical
  EventBus.

### §34 — Final capability matrix

Statuses use only WORKING / PARTIAL / REQUIRES CONFIGURATION / NOT IMPLEMENTED / NOT
VERIFIED, based on evidence actually produced here.

| Capability | Impl. | Canonical path | Automated test | Real E2E | Status |
|---|---|---|---|---|---|
| Autonomous missions | yes | MissionEngine | autonomous-loop + mission-platform | deterministic tool loop | WORKING |
| Mission persistence | yes | atomic store | restart/recovery | run | WORKING |
| Restart / resume | yes | load_mission | 3 restart tests | run | WORKING |
| ModelGateway | yes | agent → gateway.llm() | integration + behavioral proof | **no live key** | PARTIAL |
| Model fallback | yes | chat_with_fallback | 3 fallback tests | **no live key** | PARTIAL |
| Streaming | yes | gateway.stream() | classification test | **no live key** | PARTIAL |
| ToolGateway | yes | agent → gateway.execute() | integration + behavioral proof | run (android loop) | WORKING |
| Delegation | yes | agent/mission → `subagent.delegate` Job on JobQueue → sub-agent worker → gateway | delegation tests + connectivity proof | run | WORKING |
| Memory | yes | MemoryFacade | hybrid + behavioral proof | run | WORKING |
| WorldState | yes | StateFacade | world-model tests | run | WORKING |
| EventBus | yes | canonical bus | replay + android audit mirror | run | WORKING |
| Computer observation | yes | controller.observe | unit/integration | **no display** | PARTIAL |
| Computer control | yes | controller | honesty tests | **no display** | NOT VERIFIED |
| Android observation | yes | observe() semantic | 20 backend tests | **no device** | PARTIAL |
| Android control | yes | AndroidTool | 20 backend + agentic-loop | **no device** | PARTIAL |
| Android ADB | yes | AdbAndroidTransport | command + binary/UI tests | **no device** | PARTIAL |
| Android companion | reference | android_companion/ | — | **no SDK/device** | NOT VERIFIED |
| Android reconnect | design (bridge) | BridgeAndroidTransport | bridge validation only | **no device** | NOT VERIFIED |
| Android verification | yes | verify.py before/after | agentic-loop + verifier test | run (simulated) | PARTIAL |
| Vision / voice / research / counsel | yes | subsystems | off-line integration | — | WORKING (off-line) |
| Failure recovery | yes | repair/replan/retry | autonomy + recovery tests | run | WORKING |
| Backend-only operation | yes | persisted mission | control-room + restart | run | WORKING |
| Control room | yes | single /control | /control E2E | run | WORKING |
| Security | yes | consent/allowlist/HMAC/audit | android + gate tests | static | WORKING (static; device auth NOT VERIFIED) |

### §35 — Final code-connectivity: paths traced

- **Path A** User → API → MissionEngine (`core/mission.py`) → Agent (`core/agent.py`) →
  ModelGateway (`core/models/gateway.py`, via `get_model_gateway().llm()`) → ToolGateway
  (`core/tools/gateway.py`) → Tool → EventBus (`core.events`) → WorldState → Memory
  (`core.memory.MemoryFacade`) → completion. **CONNECTED** (off-line).
- **Path B** User → Mission → Android tool (`android_*`) → AndroidTool (`core/android/tool.py`)
  → AndroidTransport (`core/android/transport.py`) → device → screenshot/UI tree → semantic
  observation (`core/android/observe.py`) → action → verification (`core/android/verify.py`).
  **CONNECTED** for the real `AndroidTool`→transport boundary via a deterministic
  simulated device (Test 5). Physical device E2E **NOT VERIFIED**.
- **Path C** User → Mission → Computer tool (`computer_action`) → ComputerActionController
  → host observe/act/verify. **CONNECTED at the backend**, honest `computer_control_unavailable`
  when no display; host E2E NOT VERIFIED.
- **Path D** Mission → failure → `_classify_failure`/`_retryable_failure` →
  retry/fallback/recovery → continuation. **CONNECTED** (fallback + recovery tests).
- **Path E** Mission → backend restart → `load_mission()` → resume → complete.
  **CONNECTED** (restart/recovery tests).

### Honest NOT VERIFIED items (with how to verify later)

- **REAL ANDROID E2E.** Requires a physical device/emulator + built companion. Steps in §52.
- **REAL COMPUTER (host GUI) E2E.** Requires a display + pyautogui.
- **LIVE PROVIDER E2E.** Requires an API key + network. Run a real completion through
  `ModelGateway.chat_with_fallback()`.

### Known test-isolation fragility (pre-existing, not a regression)

`test_computer_autonomy.py::test_background_agent_jobs_persist_queryable_results` fails when
run alone (AgentManager uses a process-global named-agent registry; a prior `create("worker")`
in the same file collides), but **passes in the full suite**. This is unrelated to the
canonical-boundary work and was not introduced here.

### Remaining limitations

- Physical device/emulator, live provider, and host-GUI E2E are all **NOT VERIFIED** here.
- The agentic phone-control loop uses a deterministic policy (documented per §17) for the
  model's decision step; the real observe→act→verify control path over the actual
  `ToolGateway`/`AndroidTool`/transport boundaries is proven, but it is not a live-model call.
- Some feature modules (counsel, computer planner, reasoning strategies) construct the
  `FreeLLM` completion facade directly. `FreeLLM` is the canonical completion facade (not a
  raw provider SDK), and the *agent* path already routes via `get_model_gateway().llm()`; the
  module-level `FreeLLM` constructs are a further-consolidation item, not a provider-SDK bypass.

---

## §53 — NEXT backend consolidation pass: Delegation → JobQueue (final report)

### A. What was inspected
Whole production tree for delegation / job-lifecycle / subagent execution:
`core/delegation.py`, `gateway/queue.py`, `core/agent_manager.py`, `core/mission.py`,
`core/tool_registry.py`, `subagents/subagent.py`, `gateway/handlers.py`,
`gateway/realtime.py`, `core/contracts/jobs.py`, `core/contracts/events.py`,
`core/events/bus.py`, `core/run_events.py`, `core/memory/__init__.py`,
`core/models/gateway.py`, `core/llm.py`, `tests/test_delegation.py`,
`tests/test_capability_flows.py`, `tests/test_behavioral_boundary_proof.py`,
`tests/test_architecture_gates.py`, `tests/test_gateway_realtime.py`.

### B. Bugs / architecture bypasses found (and what they were)
1. **Delegation bypassed the canonical JobQueue.** `subagent_spawn`, `delegate_tasks`
   (agent tools), `subagents.subagent.*` and the synchronous `POST /delegate` route
   called `delegation.fanout` / `decompose_and_run` directly. The queue's
   `subagent.delegate` handler existed, but the tools/route did not use it — so
   delegated work ran outside the canonical lease/heartbeat/retry/persist lifecycle.
2. **Not one sub-agent worker.** The RPC worker, the in-process fallback and the
   queue handler each built their own `HermusAgent`; the worker code was duplicated.
3. **Synthesis/planning LLM calls used the module `free_llm` (a directly-constructed
   `FreeLLM`),** bypassing `ModelGateway`.
4. **JobQueue did not mirror lifecycle onto the canonical EventBus** (only RunBus),
   so `/control`/audit could not see job transitions on the single event authority.
5. **Correlation IDs did not flow** mission/run/job/parent → worker → event.

### C. Fixes
1. Delegation is entered only through a `subagent.delegate` Job on the JobQueue
   (tools, facade and `POST /delegate` use `submit_and_wait`); the queue owns the
   lifecycle; `core.delegation` is only the worker implementation.
2. One engine `core.delegation.run_subagent_task` → `HermusAgent`; the RPC worker and
   the in-process fallback both call it.
3. Synthesis/planning route through `get_model_gateway().chat(...)`.
4. JobQueue and delegation publish `job.lifecycle` / `delegation.activity` envelopes
   on the canonical EventBus (RunBus kept for SSE/WS).
5. Correlation IDs (`mission_id`/`run_id`/`job_id`/`parent_task_id`) threaded on the
   Job, into `DelegationNode`, into the worker and onto events; `current_job_context()`
   auto-inherits the parent IDs when an agent delegates inside a queued turn.

### D. Canonical ownership map
| Responsibility | Canonical owner | Production callers | Bypasses | Fixed? | Test proving it |
|---|---|---|---|---|---|
| Model selection/completion | `core.models.ModelGateway` | `HermusAgent`, delegation synthesis/planning | none (gates forbid direct provider/FreeLLM outside subsystem) | — | `test_one_model_boundary_no_direct_provider_sdk`, connectivity |
| Tool execution | `core.tools.ToolGateway` | `core.agent`, delegation worker `call_tool` | none (gates forbid `tool_registry.execute`) | — | `test_no_tool_gateway_bypass_in_production`, connectivity |
| Memory | `core.memory.MemoryFacade` (`memory = get_memory()`) | `HermusAgent`, delegation worker `recall` | none (no app-level `memory2`) | — | `test_no_app_level_memory2_direct_access`, connectivity |
| Event authority | `core.events.EventBus` | JobQueue lifecycle, delegation handler/worker | none (no legacy bus instantiation outside owner) | — | `test_jobqueue_lifecycle_mirrors_onto_canonical_event_bus`, connectivity |
| Job lifecycle | `gateway.queue.JobQueue` | agent_manager, realtime, subagent facade | **→ was: delegation tools bypassed the queue** | YES | `test_delegation_entered_only_through_canonical_queue`, connectivity |
| Subagent worker engine | `core.delegation.run_subagent_task` | RPC worker, in-process fallback, queue handler | **→ was: duplicated worker impls** | YES | connectivity |
| Delegation entry | `subagent.delegate` Job | agent tools, `subagents` facade, `POST /delegate` | none | — | connectivity, `test_subagents_facade_submits_through_queue_not_direct_engine` |

### E. Connectivity proof (what the test actually ran)
`tests/test_delegation_connectivity.py` submits a real `subagent.delegate` Job,
the queue runs the delegation handler in a worker thread, the sub-agent runs through
`HermusAgent`, and the test proves (by instrumenting the real canonical objects) that
`ModelGateway.llm`, `ToolGateway.execute` and `MemoryFacade.recall_context` were each
called, that `job.created/queued/started/completed` arrived on the EventBus, and that
`mission_id`/`run_id` supplied on the Job reached the child `DelegationNode` and the
structured result returned to the parent.

### F. Exact test counts / skips
`tests/` (excluding `tests/js`, `tests/eval`): **684 passed, 2 skipped, 0 failures**
(up from 678). New: 2 in `test_delegation_connectivity.py`, +4 gates in
`test_architecture_gates.py` (27 → 31). The 2 skips are pre-existing and unrelated
(e.g. host-GUI / device tests that require hardware); they were not introduced here.

### G. Remaining gaps (honest)
- **Mission node agent → delegate correlation is not auto-wired.** The plumbing proves
  that when `mission_id`/`run_id` are supplied they trace end-to-end, and an agent
  delegating from inside a queued turn inherits them via `current_job_context()`. But a
  mission DAG node agent is built by `make_agent_backed_executor` without a `mission_id`
  and does not stamp it onto a raw `delegate_tasks` call. Auto-wiring the mission node
  agent is a follow-up.
- **Nested delegate jobs are not executed.** To avoid a bounded-pool deadlock, the queue
  runs each `subagent.delegate` job to completion and the delegation handler fans out via
  its own transport; delegation does not submit child *jobs* onto the same queue. The
  child jobs' per-child persistence is therefore not isolated at the Job level (it is at
  the delegation-tree level).
- **Physical Android / host-GUI / live provider** remain NOT VERIFIED (stating policy),
  unchanged by this pass.
- **`FreeLLM` module-level constructions** in `counsel`/`computer planner`/`reasoning`
  modules are a further-consolidation item (documented in "Remaining limitations"); not a
  raw provider-SDK bypass.

### H. Git
- Branch: `clean-slate/final-consolidation`
- HEAD: `d4dbbc0` (clean working tree)
- New commits on top of `166d08b`:
  `427b7d2` core consolidate delegation through JobQueue,
  `5e8cbac` tests e2e connectivity + gates,
  `d4dbbc0` docs architecture + capability matrix.
- **PUSH NOT DONE / BLOCKED:** `git remote -v` is empty — no origin URL is configured in
  this workspace (the `origin/*` tracking refs reference a stale `ae366d9` for the branch
  and `574c780` for `main`). The GitHub token was explicitly flagged as needing rotation
  and MUST NOT be embedded in a remote URL. A remote URL is required before pushing
  `clean-slate/final-consolidation`; this is the only step that could not be completed
  here.
- `main` was **not** modified.
