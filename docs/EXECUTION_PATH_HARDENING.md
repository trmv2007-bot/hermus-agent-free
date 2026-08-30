# Execution-path hardening

This pass closes the gaps a review of `b753d56` ("universal runtime") found in
the **execution path** — not by adding another architecture layer, but by
making the existing one honest about failure, evidence, budgets and isolation.

Rule of the pass: **stop broad architectural rewrites; make the path that
actually runs behave correctly and prove it with tests.**

Every item below has a regression test in
`tests/test_execution_path_hardening.py` (46 tests) and the whole suite is
**399 passed / 1 skipped**.

---

## 1. A mission failure is a mission result — never a chat answer

**Was:** `core/runtime.execute()` caught any exception from `MissionEngine`,
logged it, and then ran `agent.chat(...)` — so "build this app and keep going
until it works" could crash internally, fall back to chat, and come back as
*advice about how to build the app*. Execution silently became explanation.

**Now:**

```
MISSION ERROR
     ↓
MISSION FAILED            (run_kind="mission_failed", status="failed")
     ↓
diagnostics: stage · reason · error_type · recoverable · resumable
     ↓
repair / resume handle:   hermus mission resume <id> --restart-failed
```

* `MissionEngine.start_mission()` catches lifecycle crashes itself and
  **persists a `failed` report** with `{type, message, stage, traceback,
  recoverable}` — the mission stays on disk and restartable instead of
  vanishing into an exception.
* `core/runtime.mission_failure_result()` builds the structured contract; the
  answer text starts with `MISSION FAILED` and the event stream emits
  `mission_error` + `mission_finished(failure=…)`.
* The old downgrade still exists but is **opt-in**
  (`HERMUS_MISSION_FALLBACK_TO_CHAT=1`) and then labelled
  `run_kind="chat_fallback"`, `degraded_from="mission"`, so no caller can
  mistake it for mission output.

## 2. Intent is classified before anything becomes a mission

`classify_request()` used to look for *an action verb anywhere* + *a
deliverable word anywhere*, so "Can you explain how to fix my app?" and "What
is the best way to build an API?" were promoted to missions.

`detect_intent()` now decides first:

| intent | example | promoted? |
|---|---|---|
| `explanation` | "explain how to fix my app", "best way to build an API" | no |
| `question` | "what is the capital of France?", "when does the backup run?" | no |
| `analysis` | "review this diff and summarize findings" | no |
| `action` | "build me a REST API for payments", "fix the failing tests" | yes |
| `conversation` | "hi there!" | no |

Explicit markers (`mission:`, `autonomously`, `keep going until …`) always win;
only `action` intents are auto-promoted (`HERMUS_MISSION_AUTO_CLASSIFY=0`
still disables auto-promotion). `classify_request(text, with_intent=True)`
returns `(kind, intent)` for callers that want the reason.

## 3. Evidence gate: goal-completion evidence ≠ supporting action

Tools are now split into **goal evidence** (`file_write`, `file_edit`,
`sandbox_run`, `shell_execute`, `swe_develop`, `browser_*`, …) and
**supporting actions** (`memory_add`, `embeddings_add`, `slack_notify`,
`jira_create_issue`, `github_integration_pr_comment`, `skill_harvest`, …).

Supporting actions are recorded (`evidence[].supporting_actions`) but never
satisfy the gate, and the repair instruction names them:

> "This stage must produce 'change' evidence … Only supporting actions were
> recorded (memory_add, slack_notify); those do not prove the goal was
> accomplished."

## 4. Stages are judged by expected output type, not by role name

`expected_output_type(node)` answers *what this stage owes us*:

| expected | satisfied by |
|---|---|
| `change` | files created/modified, or a mutating tool ran |
| `execution` | commands/tests actually executed |
| `analysis` | a substantive written finding (≥120 chars, or a concrete verdict) |

So a **verifier that reports "tests failed because X" succeeds** — it is an
`analysis` stage and the finding *is* the deliverable. Under the old
role-based rule (`ACTION_ROLES` included `verifier`/`tester`) that honest
verdict was judged `no_evidence_of_work` and triggered a pointless repair
loop. Observation roles only become `change` stages when the goal explicitly
asks for a fix/implementation.

## 5. Budget hierarchy

A mission owns the whole lifecycle, so it gets a hierarchy instead of one
number that was *smaller* than a single agent turn (25 vs 32):

```
mission budget (default 48, HERMUS_MISSION_BUDGET_STEPS)
  ├─ planning        requirements + DAG build          (charged once)
  ├─ execution       DAG rounds                        (per round)
  ├─ verification    observe + verify + critic panel
  ├─ repair          diagnose + replan rounds
  └─ emergency       reserve a phase borrows from when it runs dry
```

`MissionBudget.consume/remaining/borrow/grant_extension/
grant_emergency_extension` keep per-phase accounting; the hard stop stays the
global bound, and a phase that legitimately over-runs borrows from the
emergency reserve (overdraw is recorded, not fatal). Extensions are real
steps (`bonus_steps`) split ~60/40 between execution and the emergency
reserve — and the docstring no longer claims the removed
`initial_steps + 10 * extensions_used` behaviour.

## 6. Mission-isolated file evidence

The old `_scan_changed_files()` walked `workspace.root` + `Path.cwd()` and
counted anything whose `mtime` was newer than the mission start — so a
concurrent mission's (or the user's editor's) file could become *this*
mission's proof of work.

`core/mission_files.py` replaces that with:

* a **per-mission workspace** — `~/.hermus/missions/<id>/workspace/` — which is
  advertised in every node prompt ("write every deliverable inside …");
* a **baseline + snapshot diff** over mission-scoped roots only (other
  missions' directories are excluded, skip-lists and caps apply);
* content fingerprints `(mtime_ns, size, blake2b-64KB)` so an in-place rewrite
  inside one clock tick is still detected.

## 7. Crash-safe mission state

`_save_mission()` writes through `core/atomic_io.py`
(`tmp → flush → fsync → os.replace → fsync(dir)`) inside an advisory
`flock`, so a second worker or an interrupted process can never observe a
half-written `msn_*.json`.

## 8. Explicit resume semantics

| state | `resume_mission()` |
|---|---|
| `blocked`, interrupted, `pending`… | resumes (adds steps if the budget is spent) |
| `failed` | **refuses** unless `restart_failed=True` (clears the error, resets failed nodes, grants steps, bumps `restarts_used`) |
| `completed`, `cancelled` | raises `ValueError` (terminal — start a new mission) |

`extend_budget(id, steps, emergency=False)` works on running/blocked/failed
missions, consumes a normal extension slot (or the separate emergency
reserve), and marks a failed mission recoverable again. API:
`POST /missions/{id}/resume?restart_failed=true`,
`POST /missions/{id}/extend?steps=10&emergency=true`; CLI:
`hermus mission resume <id> --restart-failed`, `hermus mission extend <id> --emergency`.

## 9. One frontend runtime client

`gateway/static/hermus-client.js` is now the single API client for every
dashboard (served at `/dashboard-assets/hermus-client.js`):

* `sendCommand({text, files, …})` — **queue-first** (`async: true`, multipart
  when attachments are present), SSE subscription before submit, job polling,
  mission-failure surfacing via `formatFailure()`;
* `openStream()`, `steer()`, `missions.resume/extend`, `capabilities()`.

`/dashboard` and `/jarvis` both load it and both call
`HermusClient.sendCommand`. **`/jarvis` used to be a dead UI** — it drew a
beautiful HUD and issued zero requests; it now executes real turns and renders
the answer (plus mission state, tool calls and failure diagnostics) in a
runtime panel.

### Bug found while smoke-testing the queue path

`GET /jobs/{id}` answered **404 for every finished job**: `Job.brief()` always
contains an `error` key (empty on success), and the route tested
`if "error" in st`. The dashboard's poll fallback therefore never saw success
and, without SSE, a queued turn hung until timeout. Status payloads now carry
an explicit `found` flag, the routes use it, and the client gives up after 5
unknown-job polls instead of spinning.

## 10. Self-improvement is gated by verified success + repeatability

Learning from one bad trajectory used to be possible:

```
bad execution → bad trajectory → skill harvest → system learns it
```

`core/skill_forge.py` now requires, before a skill is distilled:

1. **verified success** — a verifier verdict, *or* ≥2 distinct tools with
   clean results and a substantive answer; and no `no_evidence_of_work` /
   `no_model_backend` / `mission failed` marker in the trajectory
   (`stage="unverified"` otherwise);
2. **repeatability** — the procedure signature (goal shape + tool set) must be
   observed succeeding in **independent sessions** (`HERMUS_SKILL_FORGE_MIN_REPEATS`,
   default 2). The first success returns `stage="awaiting_repeat"` and is
   recorded in `success_ledger.json`; the same session repeating itself is not
   independent evidence.

## 11. Model capability negotiation

`core/model_capabilities.py` answers, *before* a run starts:

```
model supports tools?      YES / NO / UNKNOWN
vision?                    YES / NO / UNKNOWN
long context?              YES / NO / UNKNOWN
structured outputs?        YES / NO / UNKNOWN
streaming?                 YES / NO / UNKNOWN
computer control?          YES / NO / UNKNOWN
```

Sources: provider presets → curated model-family matrix → live probe
(`GET /api/tags` for Ollama: reachable? model pulled?). `negotiate()`,
`select_compatible_model(required)` and `mission_capability_gate()` feed
`GET /models/capabilities`, the runtime pre-flight event
(`model_capability_warning`) and, with `HERMUS_AUTO_SELECT_MODEL=1`, an
automatic recommendation. The free default stays `ollama/llama3.1:8b` — but
Hermus now *says* when it cannot do the requested work instead of discovering
it mid-mission.

## 12. CI is local, not hosted

GitHub Actions is intentionally **not** part of this repository. The project's
barrier is that the agent works, not that a hosted runner is green — the exact
same gates run locally in seconds:

```bash
HERMUS_MODEL=mock/mock python -m pytest tests/ -q     # 399 passed, 1 skipped
python -m compileall -q hermus.py core gateway tools backends scheduler subagents skills tests
ruff check . --select E9,F63,F7,F82,B,PLE --ignore E501   # All checks passed!
```

Run these before merging anything; they are the whole gate. If hosted CI is
ever wanted again, re-adding a workflow file is a five-minute change and does
not touch any Hermus code.
