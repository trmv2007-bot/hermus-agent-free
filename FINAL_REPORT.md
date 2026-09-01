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
