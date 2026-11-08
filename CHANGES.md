# HERMUS Agent Free — Full Repair & Redesign

**Before:** 21 of 1565 tests failing, a half-finished "harness-remodel" merge, an unreadable 7–9px-font dashboard with duplicated markup and five JS modules that were never even loaded.
**After:** 1563 passed / 2 skipped / **0 failed**, a completely redesigned Control Room, honest execution paths everywhere.

Every change below keeps the repo's own test contracts (ARIA tablist, wording pins, harness governance gates) intact — nothing was deleted or disabled, features were only repaired and re-organized.

---

## 1 · Crash-level engine bugs (the "works like shit" part)

| Fix | File | What was wrong |
|-----|------|----------------|
| Re-exported `governed_call` / `governed_turn` / `MOCK_REF` / `list_models` / `ping_model` / `resolve_model_spec` | `core/harness/__init__.py` | The harness-remodel merge dropped the re-exports. **Every** import of `governed_call` across 10 modules raised `ImportError` at call time — this alone broke multi-model fan-out, MAP goals, council deliberation, self-improvement, skill forging and the sentinel. |
| Governed the last three ungoverned model turns | `core/skill_forge.py`, `core/sentinel.py`, `core/counsel/meta.py` | Raw `.chat()` calls bypassed the token budget / session record; the repo's own static gate flagged them. |
| Rebuilt honest pooled-agent execution | `core/agents/agent.py` | `_handle_task_message` / `_handle_collaboration` were **fake work** (`asyncio.sleep(0.5)` + canned "Task completed: …" strings). `run_task` now submits a canonical `runtime.turn` job via `submit_and_wait`, records a harness session, and reports real answers or `Error: …` — never invented success. Agent DMs/broadcasts are now durable on the harness bus (one broadcast record, not N DMs). |
| `ToolRunner.execute_tool` now goes through the canonical ToolGateway | `core/agents/specialization.py` | It used to *prompt the model to pretend to run the tool*. Now it executes the real tool with permission gating and typed results. |
| Restored the eval-harness HTTP API | `gateway/routes_harness.py`, `gateway/gateway.py` | `/api/harness/suites|runs|latest|stats|compare` had no backing routes (the test monkeypatched `_runner`, which didn't exist). Rebuilt read-only projections over `HarnessRunner`. |
| Added the agents bus endpoints | `gateway/routes_agents.py` | `POST /api/v1/agents/subscribe` (+ `/unsubscribe`, `GET /inbox/{session_id}`) were missing entirely, so the Control Room could not read swarm mail. |

## 2 · Test-suite repairs (21 failures → 0)

- `tests/test_config.py` — stale field-count pin (194 vs the 202 the merge actually landed); updated with an explanation comment so the next drift is deliberate.
- `tests/test_js_client.py` — Node ≥ 23 switched the default test reporter to `spec`, so the `"# fail 0"` TAP assertion never matched; the runner now passes `--test-reporter=tap` to pin the format across Node versions.
- `.github/workflows/ci.yml` → `test.yml` + CI badge in `README.md` — the test guards a real badge/workflow pair; the repo had `ci.yml` but the badge and guard expect `test.yml`.
- Everything else (bugfixes, universal keys, deepthink, counsel, computer agent, harness consolidation ×8, harness product, console manifest, control-room UX, living dashboard) was collateral of the fixes in §1.

## 3 · Control Room — full redesign (the "looks like shit" part)

**Rebuilt from scratch:** `gateway/control.html`, `gateway/static/nexus.css`, `gateway/static/control.css`.

What the redesign fixes:

- **Readable typography.** The old UI rendered almost everything at 7–9px. The new design system uses a 13.5px base with an 11px floor for meta labels.
- **One design system.** Three stacked, `!important`-fighting CSS layers (nexus.css + ~10 KB inline styles + JARVIS overrides) are consolidated into a single token-based system: deep-slate surfaces, one cyan accent, semantic status colors, consistent cards, tags, KPIs, tables and forms.
- **A real, visible ARIA tablist.** The old tabs were an sr-only *text stub* — 11 real tabs now (Overview, Jobs, Missions, Approvals, Safety, Capabilities, Memory, Presence, Computer, Agents, Systems) with roving tabindex and arrow-key navigation. The missing **Jobs** tab and the **Systems** (console) tab id contract are restored; Doctor lives inside the Systems console as the manifest intends.
- **De-duplicated markup.** The workshop and command-palette blocks each appeared **twice** in the old file; `nexus-mock.js` was loaded twice. All singletons now.
- **All nine JS modules actually load.** `control-room.js` (the 1155-line panels/envelope engine), `control-core.js`, `control-agents.js` and `control-client.js` were never referenced by the page — half the app was dead code in the browser. Correct load order is wired, and `refreshTab` now also refreshes the Approvals/Capabilities/Jobs-remote surfaces.
- **Everything is kept, nothing limited:** hero core orb (canvas), command shell with the pinned "Tell HERMUS what you need" placeholder, voice button, hands-free voice panel with push-to-talk, command palette (Ctrl+P / Ctrl+K), full-screen Workshop, Personal Space surface, model-fleet routing UI, agents fleet + crew mail + GPU meter, telemetry timeline, replay, modals, toasts, Focus/Learning/Routines/Devices dock surfaces.
- **Accessibility pinned in `control.css`:** visible `:focus-visible` rings and two `prefers-reduced-motion` blocks.
- **Verified live:** booted the gateway, screenshotted every tab (desktop + mobile), VLM-reviewed, and iterated until there were **zero browser JS errors** and no visual glitches (missing `#missions` / `#approvalBundles` / `#pendingApprovals` / `#approvalGrants` tables and the full voice panel were added during this pass).

**Dead assets removed:** `hermus-command-theme.css` (unreferenced override layer), `jarvis-ui.js` + `control-shell.js` (orphaned drawer experiment referenced by no route, test or doc).

## 4 · Notes on the "deep restructure" decision

`core/memory2.py` and `core/router2.py` look like duplicates but are **versioned successors** ("Memory 2.0", "Model Router 2.0") imported by 17 files including the architecture tests — merging them would be a large, risky rename with zero behavioral gain, so they were deliberately left in place. The chaos that *was* safely removable (dead CSS layers, duplicated HTML blocks, unloaded modules, fake-work stubs) is gone.

## 5 · Verification

```
$ python -m pytest tests/ -q
1563 passed, 2 skipped in 247s          # was: 21 failed
$ node --test --test-reporter=tap tests/js/*.test.js
# fail 0
```

Browser smoke (Chromium, gateway live): 11 tabs, palette, modal, workshop — 0 page errors; every panel populated from real endpoints (no stuck loading states).
