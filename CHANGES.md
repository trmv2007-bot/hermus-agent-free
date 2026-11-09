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

## 6 · Round 2 — "Control Center" restyle + CI repairs (post-push feedback)

The first redesign kept a dark, cyan sci-fi look. This round replaces the visual language entirely and makes CI green.

### 6.1 Visual language: dark sci-fi → light Control Center

- `gateway/static/nexus.css` fully rewritten again (~1,050 lines): soft neutral canvas (`#f4f5f7`), white cards with hairline borders, one indigo accent family (`#4f46e5`), semantic green/amber/red, 13.5px base type with an 11px uppercase meta floor. Dark sci-fi chrome, neon glows and the holographic stage are gone.
- The tab rail is now a proper sidebar (224px, sticky); on ≤1080px it collapses to an icon rail, on ≤760px the grids stack and the topbar declutters.
- The living core canvas (`control-core.js`) re-tinted to the light palette (indigo idle, amber acting, violet verifying, red error); the old hardcoded cyan (`#5ee8ff`) is gone.
- `control.css` focus ring fallback updated to the new accent. The pinned strings the tests require ("visual interaction system", `model-surface`, `:focus-visible`, ≥2 `prefers-reduced-motion` blocks) are all preserved.
- The JARVIS second space was **restored** (it had been dropped from the page): triple-click the HERMUS mark to open it. No neon mockups — the rail only links real surfaces, the roster reads `/api/v1/agents/list` live, and every button is a real `data-open` / `data-command` action.
- `#modelRole` got its role enum back (default/reasoning/vision/coding/background/doctor/voice — mirrors `MODEL_ROLES` in `nexus.js`), so role-scoped model routing is selectable again.

### 6.2 Bugs found by re-running the CI browser QA locally

- **Missing static routes (pre-existing):** the gateway served `control-room.js`, `console.js`, etc., but had **no routes for `control-core.js`, `control-agents.js` and the brand SVGs** — the living core never rendered and the agents engine never loaded. Added explicit allow-listed routes in `gateway/gateway.py`.
- **Tab switching regression risk:** the new stylesheet's `.tab[hidden]{display:none!important}` fought the browser UA rule pattern `selectTab` relies on (it toggles `.on` but leaves the `hidden` attribute in place). Removed the over-eager rule so `.tab.on{display:block}` wins, exactly like the previous design.
- **ui-qa console-noise filter:** the QA boots the page against a static mock server, where the `/dashboard/events` WebSocket upgrade legitimately fails (the mock serves files, not streams). The QA now ignores that network-layer message while still failing on any real `pageerror`.

### 6.3 CI repairs (why the previous push showed "failed")

- The push itself succeeded — what failed was the **"HERMUS CI" workflow**, and it had been failing on the old commit `623d9bb` too.
- `ruff format --check` exited 1 on six files (four of them harness-remodel leftovers: `core/harness/memory_graph.py`, `core/harness/sessions.py`, `core/harness/stats.py`, `core/multi_key.py` + two harness tests). Formatted; `ruff check` and the `mypy` target verified locally.
- Local replication of every CI step now passes: compileall, pytest, ruff check, ruff format, mypy, `node --check`, and the full Playwright ui-qa script from `test.yml` (baseline ≥30 labelled+bound buttons, palette, JARVIS workspace, models, Personal Space, workshop, command line, unique action coverage — exit 0, zero page errors).

### 6.4 Verification (round 2)

```
$ python -m pytest tests/ -q --ignore=tests/eval
1563 passed, 2 skipped in 256s
$ python -m ruff check . && python -m ruff format --check .
All checks passed! / 570 files already formatted
$ node /tmp/hermus-ui/qa.mjs   # the exact CI ui-qa script, mocked backend
exit 0, 0 page errors
```

Screenshots of the new UI (desktop tabs, palette, JARVIS workspace, workshop, mobile) were captured against a live gateway and reviewed.

---

## 7 · Dark Cockpit remodel (commit 7738071)

The Control Center language moved from light-indigo to a **Dark Cockpit**: deep-space canvas (`#0a0f16`), three graphite surface layers, one cyan instrument-glow accent (`#22d3ee`) with `--glow` tokens, glassy topbar, gradient primary buttons with dark text, cyan presence orb, active-tab inset rail. `color-scheme: dark` and the `theme-color` meta keep native controls dark. All 1563 tests stayed green through the remodel; five screenshots (Overview / Jobs / Safety / Missions / palette) verified against a live gateway.

## 8 · Product-polish round 2 (this commit)

Six small UX debts from the self-review, each verified against a live gateway:

1. **Activity feed de-duplication** (`nexus.js` + CSS) — the 30s model-catalog poll used to repaint the same "Model catalog · 0 deployment(s) discovered" line every cycle. Consecutive identical events now collapse into one row with a cyan `×N` counter and the latest timestamp; any different message starts a new row.
2. **Status badge that explains itself** (`nexus.js`, `control.html`) — HEALTHY / DEGRADED / OFFLINE now carry a dynamic tooltip built from the live health snapshot (which subsystems were probed, which are not ready, "open Systems → Doctor to diagnose"). The badge is focusable (`tabindex="0"`) so keyboard users get the same explanation.
3. **Missions filter + bulk resume** (`control-room.js`, `control.html`, CSS) — a state filter (all / blocked / failed / active / terminal — the twelve lifecycle states bucketed the way a human scans), a live "showing N of M" count, and a "resume blocked" bulk action that resumes every blocked/failed mission sequentially and reports **per-mission** results. Success is counted honestly: a mission that re-blocks on the same missing model counts as *not* resumed even when the HTTP call returned 200 (verified live: "0/18 resumed", each row showing its true post-resume state).
4. **Console KPI + table-label humanization** (`console.js`) — the Systems console renders server-shaped dotted keys as words: `engine.doctor_model` → "engine · doctor model", `stuck.threshold_minutes` → "stuck · threshold minutes", table headers `est_size_gb` → "est size gb". The exact raw key stays in the tooltip for correlation with the raw owner return.
5. **Relative ages** (`control-room.js`) — Jobs and Computer tables show "2m ago" instead of raw ISO stamps or epoch floats (the job queue stores `created_at` as epoch seconds — the parser handles ISO strings, epoch seconds and epoch ms, and passes unparseable values through). The absolute timestamp is one hover away.
6. **Dark-theme integrity** (`nexus.css`) — two leftover hardcoded light colors from the light era (skeleton shimmer `#fafbfc`, workshop editor `#fcfcfd`) now use theme tokens; the file header still described "white cards on a soft neutral canvas" and now documents the Dark Cockpit language. Remote-status KPIs print `on`/`off` instead of `true`/`false`.

**Tests:** 4 new tests (relative ages incl. epoch + clock-skew clamp, mission buckets, mission-tools wiring, tablist tabindex pin updated to 2 with a comment). Full suite: **1566 passed, 2 skipped, 0 failed**. `node --check` clean on all touched JS; zero browser console errors; live-gateway verification of every behavior above.

---

## 9 · Round 3 — a user walked the whole product (this commit)

The method this time was not a code review but a role-play: **use the product like a first-time human** — type a command, read what comes back, click every tab, every row, every button — and fix whatever makes the moment uncomfortable. That walk surfaced one product-killer and a stack of friction points; all are fixed and pinned by tests.

### The killer: the command bar swallowed every answer

Typing "list the files in this repo" produced "Request accepted" and then **silence, forever**. Two stacked causes, both verified against the live gateway:

1. `sendCommand` POSTed natural language to `/api/v1/commands` — the *console-command ledger*, which answers `{ok:true, command_id:null}` and starts nothing. The real engine path (`POST /jobs`, kind `runtime.turn`) was only a dead-code fallback behind an HTTP error. The command bar now uses the real path; the ledger endpoint stays where it belongs (the Systems "Typed command" card).
2. The run bus *did* publish the final response as `agent_response` with the full text — and `handleRunEvent` threw it away, flipping a status chip instead. The answer now lands in a **conversation transcript** under the command bar: user lines right in accent, HERMUS lines left, persisted in `localStorage` (last 20 messages, survives reload), with a Clear control. A run that finishes without a response says so honestly; a run error lands in the transcript; voice requests appear as "🎙 voice request".

### From dead ends to doors

Every dead end the walk hit now offers the next step:

- **Model fleet** no longer says *"press SYNC"* (a button that does not exist). Empty/unavailable states explain what is missing and carry a working jump: "Show me how →" / "Open Systems →".
- **Answers that name a missing model backend** (the default state on a fresh install) get a "Connect a model →" button in the transcript itself.
- **Mission blockers** ("No model backend reachable… Configure a model before running missions") render a "connect a model →" jump next to the blocker text.
- **System health** has a Diagnose button; **attention items** carry Diagnose + Dismiss (dismissal is per-browser, keyed on title+detail, so a genuinely new problem always resurfaces). When the gateway is degraded but the kernel attention list is empty, the card synthesizes "No subsystem reports ready — run the Doctor" instead of claiming "All systems ready" under a DEGRADED badge.
- **Job rows are doors**: clicking (or Enter/Space on) a row expands the request text, run linkage, wait/run time, error and the canonical result from `/jobs/{id}/result`. Columns now read job / kind / state / tries / run / age, and the queue line uses real fields — "backend inprocess · 4 workers · 49 succeeded" instead of "backend=? · workers=?".

### The hero chip stopped arguing with itself

Two pollers owned the same pixels: the label could say **THINKING** while the line under it said *"Idle. The queue is clear."* Now there is a single-writer rule — while a user run is live, nexus owns the chip ("working on 'list the files…'"); the queue-derived copy yields (`control-core` checks `HermusNexus.state.busy/missionLive` before writing), a just-answered run shows *"Answered — the response is in the conversation…"* for a few seconds, and each writer always writes label + line as one consistent pair.

### Small comforts

- **Command palette is a keyboard instrument**: ArrowUp/Down move a highlighted active row, Enter runs it, the digit hints 1–9 are real shortcuts, and typing resets the selection. (Before: the hints were decoration and Enter did nothing.)
- **Telemetry timeline reads like words**: "2m ago · turn prepared" (raw type + ISO stamp kept in the tooltip); the search filter matches humanized or raw text.
- **Harness context numbers agree with the words**: `6.1k/6.0k` now flags amber with a tooltip and the line says *"over budget (trimmed next turn)"* instead of contradicting itself with "within budget"; KPI labels read "66 / 797 live sessions", "recall mode".

**Tests:** 18 new tests (`tests/test_command_answer_loop.py`) evaluate the real `nexus.js` / `control-room.js` / `control-core.js` in Node: agent_response → transcript, honest no-response and error paths, transcript persistence, palette keyboard nav, attention dismissal persistence, job row expansion + real queue fields, telemetry humanization + filter, mission blocker CTA, budget honesty, and the single-writer hero rule — plus a regression pin that the command bar POSTs `runtime.turn` to `/jobs` and never the ledger endpoint again. Full suite: **1584 passed, 2 skipped, 0 failed**. Every behavior above was additionally verified by hand against a live gateway (command → streamed answer, job expansion, palette Enter, CTA navigation, dismissal, reload persistence).
