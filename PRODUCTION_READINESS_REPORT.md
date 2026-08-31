# Hermus — Production-Readiness & Integration-Completion Report

Branch: `clean-slate/final-consolidation`
HEAD: `d79efe0c11c7bc6f6a35114c26715729463aa607`   (4 commits ahead of upstream `0055043`)
Upstream tip verified unchanged: `005504398c3188566e5acbc391239e2aefc215cf`

---

## 1. What was actually wrong (found by source inspection, not docs)

1. **`_check_gateway_auth` was defined + imported but never wired.** It was not attached to a single route, so the optional gateway token was documentation-only. Worse, it **returned a `JSONResponse`** — FastAPI does **not** short-circuit a request when a dependency returns a `Response`; it must `raise HTTPException`. Even if it had been wired, it would have silently passed every request.
2. **Channel control-plane actions were unauthenticated.** `routes_channels.py` mixed the inbound Telegram webhook (which *must* stay open — Telegram cannot attach an auth header) with genuine control actions (`GET /channels/status`, `POST /channels/start`, `POST /telegram/send`). Under a configured `HERMUS_GATEWAY_TOKEN`, an unauthenticated caller could start the channels, read their status, and make the bot message arbitrary chats.
3. **`/webhook/telegram` was unauthenticated.** It trusted any POST body as a real Telegram update and ran the agent on it — an attacker who could reach the gateway could forge updates and drive the agent (no `X-Telegram-Bot-Api-Secret-Token` header check).
4. **15 production modules bypassed the model boundary**, constructing `FreeLLM(`/`free_llm.` directly and picking provider/model themselves, instead of going through `ModelGateway`. (This was fixed in commit `44a9369` during the earlier phase of this pass; see §2.)

## 2. What was fixed

| SHA | Change |
|-----|--------|
| `44a9369` | **core(model): route every production model invocation through ModelGateway.** 15 modules (`core/counsel/members.py`, `core/counsel/meta.py`, `core/doctor.py`, `core/multi_ai.py`, `core/reasoning/scaffold.py`, `core/reasoning/strategies.py`, `core/computer/delegation.py`, `core/computer/planner.py`, `core/computer/repair.py`, `core/self_improvement.py`, `core/skill_forge.py`, `core/skill_manager.py`, `scheduler/cron.py`, `tui/tui.py`, `core/compat/legacy_memory.py`) now obtain the client via `get_model_gateway().llm(...)` / `.chat(...)` / `.stream(...)`. Added architecture gate `test_one_model_boundary_no_direct_free_llm_construction`. |
| `6341348` | **security(gateway): actually enforce the optional gateway token on control-plane HTTP routes.** `_check_gateway_auth` now raises `HTTPException(401)` when `HERMUS_GATEWAY_TOKEN`/`config.gateway_api_token` is set (opens by default when unset) and is wired via `Depends` onto every control-plane router (realtime HTTP/SSE, canonical, engine, subsystems, management, registry, android, jarvis, computer, speech). WebSocket endpoints were moved to separate ungated `ws_router`s (they self-auth with a 1008 close) because a WS route can’t inject `request: Request` into a dependency. |
| `29bdd09` | **security(gateway): gate channel control actions and harden the Telegram webhook.** Split `routes_channels` into `router` (inbound `/webhook/telegram`, ungated) and `control_router` (`/channels/status`, `/channels/start`, `/telegram/send`, gated by the same optional token). Added optional `HERMUS_TELEGRAM_WEBHOOK_SECRET` validation on `/webhook/telegram` (constant-time compare of `X-Telegram-Bot-Api-Secret-Token`; a no-op when unset). |
| `d79efe0` | `docs: document HERMUS_TELEGRAM_WEBHOOK_SECRET in .env.example`. |
| `958d1be` | **feat(gateway): route vision through ModelGateway.** `tools/vision.py` no longer issues its own Ollama request; vision flows through `ModelGateway.vision_complete()`/`FreeLLM.generate_image()`. The model-boundary gate now also forbids direct model-endpoint literals. Added `tests/test_vision_gateway.py`. |
| `3443bb0` | **chore(web_search): use ddgs.** Swapped the deprecated `duckduckgo_search` for `ddgs` (drop-in, identical result keys) with a legacy fallback; `requirements.txt` pins `ddgs`. Removes the rename `RuntimeWarning`. |
| `992a210` | **test(isolation): hermetic computer-control + workspace tests.** Added `TaskControlManager.reset()` and an autouse conftest fixture clearing the process-wide singleton (root-cause fix for the order-dependent `test_computer_live_events` fragility). Fixed the real `workspace.dirs` no-op mutation bug by redirecting `workspace.base_dir`/profile dir to a temp root with `try/finally` restore in the three affected tests. Suite is now repeat-run-stable. |

## 3. What was deleted / never existed

- **Nothing was deleted or weakened.** The `WorldStateV2` duplicate class had already been removed in the prior pass. No tests were removed or replaced with mocks; the only new tests are additive (below).
- The known "duplicate route" scare (`GET /runs/{run_id}` in `realtime.py` and `routes_canonical.py`) was **investigated and found NOT to be a real duplicate** — the canonical router is mounted with `prefix="/api/v1"`, so the effective paths are `/runs/{run_id}` vs `/api/v1/runs/{run_id}`. No change.

## 4. What was preserved (feature depth kept)

- Mission engine: on a mission crash the runtime returns a structured `mission_failed`; chat downgrade happens **only** with `HERMUS_MISSION_FALLBACK_TO_CHAT=1` and is labeled `run_kind: chat_fallback`. No silent fallback.
- ModelGateway `chat_with_fallback` never fabricates a response on failure — it retries real candidates, records typed outcomes, then raises `ModelGatewayError`.
- `tools/vision.py` (direct Ollama LLaVA call) was deliberately **left as-is** rather than routed through `ModelGateway`. See §6/PARTIAL.
- Computer `mouse.py`/`keyboard.py`/`recorder.py`/`window_manager.py`, `android/transport.py`, `verifier_registry.py` `raise NotImplementedError` only on **abstract base classes** with real concrete implementations — correct by design, not broken.

## 5. What was connected / canonical-owner verification (audited, no change needed)

- **Single `HermusAgent`** in `core/agent.py`; channels (Telegram/Discord) converge on it via `get_agent_for_user` → `_agent_factory`. All agent construction in `core/agent.py`, `agent_manager.py`, `delegation.py`, `harness/swarm.py`, `mission.py`, `reasoning/eval.py` routes through `get_model_gateway().llm(...)`.
- **Single `ModelGateway`** (`core/models/gateway.py`) — one class, one `get_model_gateway()`.
- **Single `Config`** (`core/config.py`) — one class, one singleton `config`.
- **Single durable `EventBus`** (`core.events.get_bus()`); `RunBus` is the live SSE/`/runs` stream. Both persist/mirror run activity; this is a deliberate live-stream vs durable-log split, not a duplicate owner.
- **WorldState unified**: `core/state/world.py` facade over one `WorldState` impl (`WorldStateV2` gone).
- **No duplicate (method, normalized-path) HTTP routes** across all gateway routers (prefix-aware scan).
- **All 138 production modules import cleanly** (no missing-import landmines in un-executed paths).
- **Direct model HTTP/SDK calls are confined to the model subsystem** (`core/llm.py`, `core/openai_compat.py`, `core/providers.py`, `core/free_keys.py`, `core/multi_key.py`, `core/model_fleet.py`, `core/provider_router.py`, `core/router2.py`, `core/model_capabilities.py`, `core/custom_api.py`, `core/computer/llm/`). The only outlier is `tools/vision.py`.
- **Permission systems are domain-scoped, not duplicates**: `core/permissions.py` (agent tool permissions: Decision/Risk/Capability), `core/computer/permissions.py` (computer action policy/emergency stop), `core/android/permissions.py` (Android runtime). Each governs a distinct domain.

## 6. PARTIAL / NOT-VERIFIED (honest, not faked)

- **FIXED — `tools/vision.py` now routes through ModelGateway.** Added `FreeLLM.generate_image()` (owns the Ollama `/api/generate` call, 404 → `model_unavailable`, connection → `network`) and `ModelGateway.vision_complete()`/`vision_models()`; `tools/vision.py` no longer imports `requests` or hits a backend directly. `video_analyzer` keeps working (same `{success, description}` contract). The architecture gate now also forbids direct model-endpoint literals outside the model subsystem.
- **FIXED — web_search `ddgs` migration.** `tools/web_search.py` prefers `ddgs` (same `DDGS` class / result keys) with a legacy fallback; `requirements.txt` pins `ddgs`. The deprecation `RuntimeWarning` is gone (warnings dropped from 15 → 2).
- **FIXED — test isolation.** Added `TaskControlManager.reset()` + an autouse conftest fixture that clears the process-wide singleton between tests (root cause of the order-dependent `test_computer_live_events` fragility). Also fixed the real `workspace.dirs` no-op mutation bug (it's a property returning a fresh dict): `test_agent_manager_lifecycle`, `test_background_agent_jobs_persist_queryable_results` and `test_gateway_endpoints` now redirect `workspace.base_dir` (and the profile dir) to a temp root with `try/finally` restore, so they no longer write to the real `~/.hermus` and the suite is repeat-run stable.
- **NOT-VERIFIED — live provider APIs, Android, desktop/host-GUI**: you will test these manually. No faked E2E.
- **AUTH/PUSH BLOCKED (environment):** `git push origin clean-slate/final-consolidation` **fails** in this sandbox because no git credential/token is present (`fatal: could not read Username for 'https://github.com'`). The commits are committed locally and are a clean fast-forward over `0055043` — they need a single push from an environment with credentials.

## 7. Exact SHA & test results

- **HEAD:** `992a210cb189e37523b3fecb8d1e20194b565ecb`
- Commits ahead of upstream `0055043`: `44a9369` (model boundary), `6341348` (gateway token), `29bdd09` (channel gating + webhook secret), `d79efe0` (env doc), `958d1be` (vision gateway), `3443bb0` (ddgs), `992a210` (test isolation).
- **Full suite (all fixes applied, verified repeat-run-stable):** `692 passed, 2 skipped, 0 failures` (~150s; run with `TMPDIR=/home/user/.tmptest --basetemp=/home/user/.tmptest/bt` to avoid the 993MB `/tmp` tmpfs). The slowest test is the pre-existing 53s `test_delegation.py::test_decomposition_path_plans_then_runs`.
- **Architecture gates:** `32 passed` (the extended model-boundary gate `test_one_model_boundary_no_direct_free_llm_construction` passes).
- **Gateway realtime + channels:** `26 passed`; **vision:** `5 passed`.

## 8. Remaining work

1. Push the 7 local commits to `origin` from a credential-capable environment (clean fast-forward over `0055043`; no force needed). Blocked only by the sandbox having no git credentials.
2. Manual verification by the user: live providers, Android, desktop/host GUI.
3. Existing test only, not touched: `test_delegation.py::test_decomposition_path_plans_then_runs` is slow (~53s) — pre-existing, not related to these changes.
