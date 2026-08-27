# Architecture Upgrades (foundation)

This branch adds the core architectural subsystems as **working foundations**:
independent modules + tests + minimal CLI wiring. They are designed to be
wired deeper into the agent loop / gateway over time, but each one already
runs and is testable offline.

Everything is dependency-free (stdlib + existing deps). Tests:
`python tests/test_architecture.py` (or `pytest`).

## What was added

| Subsystem | Module | CLI |
|---|---|---|
| Workspace / project isolation | `core/workspace.py` | `hermus workspace …` |
| Memory 2.0 (typed + scored) | `core/memory2.py` | `hermus mem2 …` |
| Model Router 2.0 (per-step) | `core/router2.py` | `hermus router choose …` |
| Autonomous verify/repair loop | `core/autonomous.py` | `hermus run …` |
| Persistent background agents | `core/agent_manager.py` | `hermus agent …` |
| Permission / risk manager | `core/permissions.py` | `hermus perms …` |
| Web research pipeline | `core/research.py` | `hermus research …` |
| Screen recording + computer control | `core/computer/` | `hermus screen …` |
| Self-healing watchdog | `core/watchdog.py` | `hermus watchdog …` |
| Profiles / personas | `core/profiles.py` | `hermus profile …` |

## Design notes

### Memory 2.0
Five typed stores (`working`, `episodic`, `semantic`, `procedural`, `project`)
in one SQLite DB. Recall scores each memory across six signals — importance,
recency (exp. decay), frequency, task relevance, user preference, and
success/failure — and returns a ranked list. Near-duplicates merge (frequency
signal) instead of duplicating.

### Model Router 2.0
`classify_task` (chat/code/reasoning/vision/research/summary/tooling/longcontext)
+ `select(text)` returns the best `provider/model` for *that step*, preferring
Ollama → free cloud, penalizing unhealthy/slow workers and context overflows,
and falling back to the configured default when nothing is discovered.

### Autonomous loop
A real `understand → plan → execute → observe → verify → diagnose → repair →
finish` state machine. A task is only `done` once a verifier confirms it;
failed verification triggers a bounded diagnose→repair→re-execute cycle. The
runner is LLM-agnostic: inject an `executor` (goal→result) and a `verifier`.
Default `Verifier` flags error/failure markers; default `Diagnoser` surfaces
the failing marker as a hint (passed as a 2nd arg to executors that accept it).

### Persistent background agents
Agents are **detached subprocesses** (`start_new_session=True`), so they keep
running after the CLI exits. State (`state.json` + `agent.json` + a `jobs/`
queue) lives under `~/.hermus/agents/<name>/`. `watchdog_tick` revives stale
agents. Register role logic via `register_handler(role, fn)`.

### Permission manager
Classifies each tool into READ/WRITE/EXECUTE/NETWORK/GUI/ADMIN with an
ALLOW/ASK/DENY default, escalates risk from args (e.g. `sudo` in a shell
command), supports per-agent + per-tool overrides, and appends to an audit
log. **Wire into `core/tool_registry.execute` to enforce** (see below).

### Hermus Computer Agent v1

The computer subsystem now implements **Record → Detect → Understand → Verify**:

- `ScreenRecorder` keeps only a bounded rolling window of JPEG-compressed
  frames in RAM (duration *and* byte guards), while optionally fanning the full
  session to disk.
- `VideoWriter` streams those JPEG frames through free FFmpeg to real MP4/WebM;
  Hermus resolves system FFmpeg or the `imageio-ffmpeg` fallback.
- `FrameSampler` + `EventDetector` find changes, debounce UI animation bursts,
  and select important evidence rather than sending every frame to vision.
- `VideoAnalyzer` compares BEFORE/AFTER composites with an injected model or
  local Ollama/LLaVA and generates an agent-readable `Timeline`.
- `ScreenVerifier` returns action, visual result, confidence, and recording
  evidence suitable for procedural memory.
- `ScreenWatcher` evaluates only new/changed frames until a visual condition is
  true or a timeout expires.
- `TaskArtifacts` writes `recording.mp4`, `timeline.json`, `events.json`,
  `actions.json`, `result.json`, and `manifest.json` together.
- `RecordingPolicy` constrains agent-created recordings to a private
  `data/recordings/` root; tool-level capture/watch consent remains controlled
  by `core.permissions`.

`hermus screen record start` uses a detached local service, so recording really
continues after the starting CLI process exits. Headless/test sources remain
available through `NullSource` and `CallableSource`.

### Hermus Computer Agent v2 — autonomous desktop control

v2 closes the loop from *seeing* to *operating* the computer:

- **Action engine** (`controller.py`, `mouse.py`, `keyboard.py`,
  `window_manager.py`) — `move_mouse/click/double_click/right_click`,
  `type_text/press_key/hotkey`, `scroll`, `open_application/close_application/
  focus_window`. Backends use `pyautogui`/`pygetwindow` when present and
  degrade to an auditable dry-run otherwise, so the loop is testable offline.
- **Vision-driven targeting** (`target_detector.py`) — `find_on_screen(target)`
  asks the vision model to locate a *described* UI element inside a captured
  frame and rescales its answer back to screen coordinates, so
  `click_target("Install button")` works across resolutions instead of hard
  coding `click(742, 381)`. A pure-PIL template matcher is included as a
  fallback.
- **Autonomous loop** (`computer_agent.py`, `state_machine.py`) — a visual
  state machine drives `Plan → Act → Record → Verify → Repair`. Each action is
  bracketed by exact BEFORE/AFTER captures and semantically verified; a failed
  verification is diagnosed and retried (re-located, re-acted) instead of
  blindly repeated. `wait_until(condition, timeout)` is exposed as a
  first-class agent tool on top of `ScreenWatcher`.
- **Evidence bundle** — every run writes `recording.mp4` + `timeline.json` +
  `actions.json` + `verification.json` + `result.json` + `summary.md` under
  `data/recordings/<task-id>/`, so the recording is agent evidence, not just
  debugging output.
- **Skill learning** (`skills.py`) — a successful procedure (actions with their
  verification results and recording evidence) is promoted to a reusable
  skill; the next similar task recalls it and adapts it to the current screen.
- **Safety layer** (`permissions.py`, wired through `core/permissions.py`) —
  every desktop action is classified LOW/MEDIUM/HIGH; high-risk actions
  (`sudo`, admin, system config) require explicit approval, and a global,
  file-backed `EmergencyStop` (`hermus computer stop`) halts all further
  mouse/keyboard/autonomous control across processes.
- **Control center** (`control_center.py`) — `hermus computer status` renders a
  live panel (status, action count, last action + verification, active
  backends).

CLI: `hermus computer task|target|click|wait|skills|status|stop`.

### Research pipeline
`search → dedupe → rank → extract claims → cross-check → contradictions →
synthesize → citations`, with confidence + uncertain-claims output. Default
synthesis is deterministic; pass an LLM `synthesizer` for real answers.

### Watchdog
Classify → known fix (apply) : diagnose (generate patch) → test → commit/rollback.
Default fixes: JSON-parse, missing-import, timeout-retry. Register custom fixes
via `register_fix(pattern, fn)`; attach `tester` / `rollbacker` callables.

## Full wiring (done)

All subsystems are now wired into the live agent loop, tool registry, and
gateway:

1. **Permissions** — `core/tool_registry.execute` gates *every* tool call
   before execution. Always audited to `~/.hermus/logs/permissions.jsonl`;
   DENY blocks (even for unregistered policy-listed tools like
   `credential_access`), ASK resolves via `HERMUS_ASK_POLICY` (default
   `allow` = audited-but-allowed for backward compatibility; `deny` = strict).
2. **Memory 2.0** — `core/agent._build_system_prompt` injects a scored recall
   block; after each turn the agent auto-persists episodic (+ semantic facts,
   + procedural tool-sequences) memories scoped to the active project.
3. **Router** — `core.agent.chat` re-selects the model per turn via
   `router2.select` (skipped when the model is pinned explicitly or the
   provider is `mock`). Response includes a `router` field.
4. **Autonomous loop** — `HermusAgent.autonomous(task)` wraps the ReAct loop
   as the executor of `AutonomousRunner` (verify/repair). Exposed via
   `hermus run` and the gateway `{"autonomous": true}` flag. An optional
   non-blocking verify gate (`HERMUS_AUTONOMOUS_ENABLED=1`) adds a
   `verification` field to every chat result.
5. **Watchdog** — `core.integrations.maybe_self_heal` attaches a
   `self_healing` block to failed results (gateway `/command` path).
6. **Persistent agents** — the gateway runs a background watchdog tick
   (`/agents` endpoints + `watchdog_tick` on a 30s loop).
7. **Research / computer / router / memory2 / workspace** exposed as tools
   (`research_deep`, `screen_*`, `router_choose`, `memory2_recall/remember`,
   `workspace_list_projects`) and REST endpoints.
8. **Profiles** — `--profile` / `HERMUS_PROFILE` injects a persona + gives the
   agent an independent Memory 2.0 store.

### Config flags (env)
| Flag | Default | Effect |
|---|---|---|
| `HERMUS_PERMISSIONS_ENFORCE` | `1` | gate tools via ALLOW/ASK/DENY |
| `HERMUS_ASK_POLICY` | `allow` | how ASK resolves (`allow`/`deny`) |
| `HERMUS_MEMORY2_ENABLED` | `1` | typed memory recall + auto-persist |
| `HERMUS_ROUTER2_ENABLED` | `1` | per-turn model routing |
| `HERMUS_AUTONOMOUS_ENABLED` | `0` | add verify gate to every chat turn |
| `HERMUS_WATCHDOG_ENABLED` | `1` | self-heal on failures |
| `HERMUS_BG_AGENTS_ENABLED` | `1` | gateway keeps background agents alive |
| `HERMUS_PROFILE` | `""` | active persona profile |

### New gateway endpoints
`/agents`, `/agents/{create,start,stop}`, `/workspace`, `/workspace/{create,use}`,
`/memory2/{remember,recall}`, `/permissions/{check,set,log}`, `/research`,
`/router/select`, `/screen/{status,start,stop,save,analyze,watch}`,
`/screen/action/{before,after}`, `/watchdog/handle`, `/profiles`,
`/profiles/create`.

### Tests
- `tests/test_architecture.py` — 10/10 (foundation units)
- `tests/test_integration.py` — 6/6 (live wiring: registry gate, agent loop,
  gateway endpoints)
- `tests/test_computer_agent_v1.py` — compressed RAM, MP4 round-trip,
  event timelines, before/after memory, watcher, artifacts and privacy policy
- full suite — 87 passing

---

# Part II — Retrieval, Gateway, Sandboxing & Delegation

The second architecture round (the "seven upgrades") is implemented and wired into the
agent loop, the gateway and the tool registry. Everything below is dependency-free,
works offline, and degrades to a single-process laptop install instead of requiring
services. Nothing here is optional-but-expected: each subsystem is on by default and
each one has a documented off-switch.

```
core/hybrid_search.py   BM25/FTS5 + dense vectors fused with Reciprocal Rank Fusion
core/decay.py           exponential decay, value bands, keep/decay/archive/purge plans,
                        prompt-budget packing
core/memory2.py         MemoryStore + Memory2 facade (recall, hybrid_recall, sweep,
                        compact, pin, forget, recall_prompt_block)
core/skill_forge.py     trajectory → evaluation → distillation → SKILL.md + skill.py
core/run_events.py      RunBus: per-run event log, ring buffer, replay, fan-out to SSE/WS
core/run_hooks.py       make_emitter / CancelToken / CancelledRun (agent-side plumbing)
core/openai_compat.py   stream_chat_completions (upstream token streaming, include_usage)
core/llm.py             stream_chat / chat_stream
core/agent.py           on_event + stream + should_cancel, memory block budgeting,
                        verification-gated skill harvest
core/sandbox.py         SandboxPolicy, capability probe, docker/podman/bwrap/local/off
core/delegation.py      JSON-RPC sub-agent trees: fanout, decompose, aggregate, cancel
tools/shell.py          shell_execute(...) routed through the sandbox
gateway/queue.py        durable asyncio job queue: lanes, priorities, retries, dedupe
gateway/redis_backend.py  optional Redis Streams transport (accelerator, never required)
gateway/handlers.py     job kinds: agent.chat, agent.autonomous, research.deep,
                        subagent.delegate, memory.sweep, channel.reply
gateway/realtime.py     SSE + WebSocket + queue/sandbox/memory/forge/delegation routes
gateway/gateway.py      lifespan wiring, /command async intake, webhook 202, maintenance
```

## A1 — Hybrid memory + semantic retrieval

Keyword matching alone was the recall bottleneck: `"postgres"` missed `"PostgreSQL
connection pooling"`. Recall is now three signals fused at query time.

* **Lexical**: `memories_fts`, an FTS5 *external-content* table over `memories.content`
  with INSERT/UPDATE/DELETE triggers, ranked with `bm25()`. User queries are pushed
  through `sanitize_fts_query()` (quotes, `NEAR`, trailing `*` prefixes) so a stray `"`,
  `(` or `-` can never raise `sqlite3.OperationalError`.
* **Dense**: per-memory float32 embeddings in `memories_vec`. `sqlite-vec` if installed,
  otherwise a brute-force cosine scan in SQL — same interface, smaller corpora only.
  Embeddings come from `EmbeddingClient` (Ollama → OpenAI-compatible → `hash`, which is
  deterministic and dependency-free, so tests and offline boxes index too).
* **Fusion**: Reciprocal Rank Fusion, `score = Σ weight_i / (rrf_k + rank_i)` with
  `rrf_k=60`, plus `memory_prior_weight=0.35` on the stored importance prior. Ranks —
  not raw scores — are combined, so BM25's unbounded scale and cosine's [0,1] can't
  drown each other out.

Each hit carries its provenance, which is what makes recall debuggable from the
dashboard: `retrieval: {mode: "hybrid" | "lexical-only", bm25_rank, vector_rank,
backend}`. `hermus mem2 hybrid "<query>" --explain` prints the per-signal breakdown.

**Fallback ladder** (each step is logged once, never fatal): hybrid → FTS5-only →
`LIKE` substring search. `HERMUS_MEMORY_HYBRID=0` jumps straight to lexical; a DB without
the vec0 module reindexes into brute-force cosine on next `hermus mem2 reindex`.

## A2 — Decay, budgets and eviction

Stale context used to compete with fresh context forever. `MemoryDecay` now scores every
row at recall time:

```
recency   = 0.5 ** (age_days / half_life)        half_life = 30d * (1 + ln(1+access)/ln(8))
frequency = 1 - 0.5 ** (access / 8)              saturating, so hoarding access is useless
combined  = 0.7 * recency + 0.3 * frequency       floored at max(0, memory_prior * 0.35)
```

Bands: `hot ≥ 0.75 > warm ≥ 0.45 > cold ≥ 0.18 > archive ≥ 0.08 > purge`. `plan()` maps a
band to `keep | decay | archive | purge` (`promote` for cold-but-repeated signals), and
`status()` returns the SQL to apply it. `pinned=1` rows are always 1.0 (never decayed,
never archived); `expires_at` rows expire outright.

Before the memory block reaches the prompt, `fit_to_budget()` packs it greedily by
*value density* (score × tokens ÷ tokens) with a per-kind cap, so one kind cannot evict
the rest, and returns `{kept, evicted, tokens, text, utilization, budget_tokens}` — the
gateway dashboard shows exactly what was dropped and why. Default budget
`HERMUS_MEMORY_BUDGET_TOKENS=600`.

The gateway runs `_memory_maintenance_loop()` every `HERMUS_MEMORY_SWEEP_MINUTES=60`:
decay evaluation → archive/purge plan → `compact_working_memory()` (TTL
`HERMUS_MEMORY_WORKING_TTL_HOURS=48`) → index consistency check. `hermus mem2 sweep
--apply` does the same on demand, `hermus mem2 context` shows the packed block and what
was evicted, and `hermus mem2 index` reports FTS5/vector coverage.
Access is recorded on recall (`memory_access` table) — that is the signal decay adapts to.

## A3 — Skill forge (trajectories → SKILL.md)

After a verified turn the agent distils *itself*:

1. **Gate** (`evaluate_trajectory`) — needs ≥3 tool calls
   (`HERMUS_SKILL_FORGE_MIN_TOOLS`), a non-empty final answer, failure ratio ≤0.5, no
   single tool repeated to death, and — critically — `verified is not False`: an
   unverified run is never harvested. Score must reach 3.0 to harvest.
2. **Distil** — steps become `DistilledStep(text, tool, args, outcome)`; with
   `HERMUS_SKILL_FORGE_LLM=1` the LLM rewrites them into imperative, reusable language,
   otherwise a deterministic template runs (no key needed).
3. **Emit** — `skills/<slug>/SKILL.md` (frontmatter: name, summary, tools, steps,
   triggers, `source: forge`, version) plus a `skill.py` that the existing skill loader
   imports. The generated `skill.py` is JSON-literal safe: argument examples are
   serialised as Python `repr`, never as JSON.
4. **Vet** — `validate()` runs import + smoke test of the emitted module. Anything that
   fails is written to `skills/.quarantine/<name>_<hhmmss>/VALIDATION_ERROR.txt` and is
   *not* registered.
5. **Dedupe** — cosine similarity against existing skill summaries at
   `HERMUS_SKILL_FORGE_DEDUPE=0.72` merges into the neighbour (`merged_into`) instead of
   duplicating; `HERMUS_SKILL_FORGE_MAX=200` retires the least valuable first.
6. **Feed back** — `record_outcome()` appends to `skills/forge_log.jsonl`; `stats()`
   reports `recent_outcome_rate`, and a skill that keeps failing stops being re-harvested.

Endpoints: `POST /skills/forge/harvest` (dry-run by default), `POST /skills/forge/run`,
`POST /skills/forge/validate`, `GET /skills/forge/stats`. CLI: `hermus forge list|log|run`.
Tools for the model: `skill_harvest`, `skill_forge_stats`.

## B1 — Queue first: intake decoupled from execution

Webhooks used to block a FastAPI worker for the whole agent run. `POST /command` (and
every channel webhook) now submits and returns `202` with handles:

```json
{"async": true, "job_id": "job_3f2b…", "run_id": "run_3f2b…",
 "status_url": "/jobs/job_3f2b…", "result_url": "/jobs/job_3f2b…/result",
 "events_url": "/jobs/job_3f2b…/events", "stream_url": "/stream/run/run_3f2b…"}
```

`gateway/queue.py` is a real asyncio worker pool with **lanes**: jobs sharing a
`session_key` (normally `platform:user`) run one at a time — so a user's turns never
interleave — while different lanes run in parallel up to `HERMUS_QUEUE_WORKERS=4`.

* Priority `0…9` (default 5) reorders within a lane.
* Failures retry with `min(30, HERMUS_QUEUE_RETRY_BACKOFF ** attempts)` delay
  (`max_attempts` per job); `asyncio.CancelledError`, `CancelledRun` and
  `CancelledError_` never retry.
* Per-job `timeout` (default `HERMUS_QUEUE_TIMEOUT=300`) sets a cooperative cancel
  *first* and only abandons the worker after `HERMUS_QUEUE_CANCEL_GRACE=15`.
* `dedupe_key` returns the *live* job instead of double-running the same webhook redelivery.
* Durability: every transition appends to `HERMUS_QUEUE_LOG=data/jobs/jobs.jsonl`,
  results go to `data/jobs/results/<job_id>.json`. On restart the log is rehydrated
  (`hermus jobs list` still works in a *different* process), and jobs that were mid-flight
  when the process died are reported `interrupted`, never silently "running".
* `submit()` stays synchronous by design (`Job` object returned, no `await` needed from
  sync tool code); everything after it is async.
* `HERMUS_QUEUE_ENABLED=0` ⇒ `start()` refuses to spawn workers and `/command` falls back
  to inline execution, so a laptop can run the gateway with no queue at all.

Optional **Redis Streams** transport (`HERMUS_QUEUE_BACKEND=redis` + `REDIS_URL`, stream
`hermus:jobs`, group `hermus-workers`) hands jobs between processes with at-least-once
delivery. `redis_available()` probes first, so a missing package or server prints one
line and the queue stays in-process.

Handler API (`gateway/handlers.py`) accepts four shapes, all supported: `handler(ctx)`,
`handler(payload)`, `handler(payload, emit)`, and `async def handler(ctx)`. Registered
kinds: `agent.chat`, `agent.autonomous`, `research.deep`, `subagent.delegate`,
`memory.sweep`, `channel.reply` (Telegram/Slack outbound, delivery verdict reported, not
swallowed).

## B2 — Bi-directional streaming

Every run gets a `Run` in `core/run_events.py`: a ring buffer (2000 events) with
**monotonic 1-based ids**, plus subscriber queues. `run_started` consumes id 1, so
`after=N` (or the SSE `Last-Event-ID` header) is an exact resume cursor; a client that
reconnects replays and then tails live.

* **SSE** — `GET /stream/run/{run_id}` (also `/jobs/{id}/events` as an SSE/JSON
  negotiable pair) with `?tokens=1` for per-token deltas and `: ping` keepalives.
* **WebSocket** — `GET /ws/agent`, protocol `hermus.agent.v1`. The `hello` frame
  advertises available job `kinds`, the sandbox backend and whether a memory index
  exists. Actions: `chat`, `autonomous`, `delegate`, `subscribe`, `cancel`, `tool`,
  `memory`, `ping`; everything streams the same event objects back with `run_id` +
  `event_id`. Unknown actions return `{"type":"error"}` instead of closing the socket.
* **Tokens** — `core/llm.stream_chat` → `stream_chat_completions()` for OpenAI-compatible
  providers (`stream:true`, `stream_options.include_usage`), so `llm_delta` events are real
  upstream tokens; local/non-streaming providers fall back to one delta per turn.
* **Cancellation** — `POST /jobs/{id}/cancel`, `POST /stream/command {action:"cancel"}`, or
  WS `{"action":"cancel"}`. It flips the run's cancel flag; `CancelToken` raises
  `CancelledRun` at the next step boundary, the job is recorded `cancelled`, and the
  children of a delegation tree are told to stop (`children stop at their next step boundary`).

Event vocabulary: `run_started, lane_assigned, job_started, turn_started, memory_recalled,
step_started, llm_delta, step_observed, tool_call, tool_result, verification,
skill_harvest_started, skill_created | skill_skipped, sandbox_output, channel_delivery,
speech_ready, run_cancelled, turn_finished, run_finished, job_finished`.

## C1 — Sandboxed execution with graceful degradation

`tools/shell.py` no longer runs model-authored commands in the parent process. All local
execution goes through `SandboxManager.run()`, which picks the strongest isolation the
machine can provide and *reports* what it got:

```
docker (if daemon) → podman → bwrap → local (rlimits + setsid + confine) → off
```

Policy (`SandboxPolicy.from_config`, all overridable per call):
`cpus=1.0, memory_mb=1024, pids=128, disk_mb=256, timeout=60, network=False,
read_only_rootfs=True, workspace_rw, tmpfs /tmp, drop=all caps +cap_net_raw only with
network, no_new_privs, run_as_nobody, env_allowlist, deny_patterns, max_output_chars=6000`.

Enforcement details that matter:
* **Env**: only the allowlist reaches the child (`PATH, HOME, LANG, LC_ALL, TERM, TZ,
  PYTHON*` by default) and anything matching `SECRET|TOKEN|KEY|PASSWORD|CREDENTIAL` is
  dropped even if allowlisted. With `network=False`, `HTTP(S)_PROXY/ALL_PROXY` are pointed
  at `127.0.0.1:9` (blackhole) — belt and braces for `local`.
* **Deny patterns** (`DANGEROUS_PATTERNS`, e.g. `rm -rf /`, fork bombs, `curl | sh`,
  `dd of=/dev/sd*`) block *before* execution with `returncode=126` and the message
  `blocked by sandbox policy … allow_dangerous=true (audited)`. `scan_command` alone is
  not the boundary — it is a pre-filter, and `sudo` needs no pattern.
* **Files**: `files={relpath: text}` stages inputs into `data/sandboxes/<id>/` and the run
  is `cd`-ed there; paths are confined (`startswith(workdir + os.sep)`) so `../` escapes
  are rejected. Without staged files the jail cwd is the repo root, per `workspace_rw`.
* **Output**: stdout/stderr capped, ANSI stripped, secrets scrubbed from the record.
* **Audit**: every run appends a structured JSON line to `data/sandboxes/sandbox_audit.jsonl`
  (`backend, limits, returncode, duration_ms, blocked, purpose, session_key`) — `local`
  mode's weaker isolation is exactly why it is audited.
* `run_python()` pipes code safely (no `-c` quoting bugs), `run_wasm()` uses `wasmtime`
  when present and reports `{"success": false, "error": "wasmtime not installed"}` when not.
* `status()` → `{configured, backend, reason, capabilities, policy, active, active_runs,
  root, audit_log, note}`; capabilities are flat booleans (`docker_binary`,
  `bwrap_usable`, `gvisor_runsc`, `wasmtime`, `unshare_net`, `resource_module`, `root`,
  `platform`) so `hermus sandbox status` and `/sandbox/status` never disagree.
* `HERMUS_SANDBOX=off` (or `backend="off"`) is explicit and honest:
  `limits={"enforced": false, "reason": "sandboxing disabled by policy"}`.
* `hermus sandbox run "<cmd>"`, `hermus sandbox python "<code>"` (code is positional),
  `hermus sandbox status`. Model-facing tool: `sandbox_run`; the permission manager gates
  `allow_dangerous` and network escalation.

## C2 — Hierarchical delegation with structured results

`core/delegation.py` turns the old fire-and-forget sub-agent into a *tree* with a wire
protocol, so a child's answer is structured data instead of scraped stdout.

* **Protocol**: JSON-RPC 2.0 over the child's stdio (newline-delimited), `PROTOCOL_VERSION
  = "1.0"` in the handshake. Methods: `ping`, `agent.run`, `tool.call`, `memory.recall`,
  `cancel`. Codes: `-32700` parse, `-32600` invalid request, `-32601` method not found,
  `-32603` internal, `-32001` timeout, `-32800` cancelled. A malformed frame answers
  `-32600`; it never kills the child.
* **Result contract** (`normalize_result`) — every path, including failures and
  non-RPC in-process children, yields
  `{answer, confidence, evidence, artifacts, tool_calls, usage, steps, error}`.
* **Fan-out**: `Delegation.fanout(tasks, goal=…)` runs children concurrently
  (`HERMUS_DELEGATION_WORKERS=4`), `decompose_and_run(goal)` plans workstreams with
  `plan_workstreams()` first. `HERMUS_DELEGATION_MAX_DEPTH=2` budgets recursion — an
  over-budget call returns `{ok: false, status: "refused"}` instead of forking forever.
* **Aggregation** (`aggregate_results`): `concat` (sections + citations), `vote`
  (plurality over normalised answers), `best` (winner's confidence, then discounted to
  `0.85` when `disagreement > 0.4`), `synthesize`. All-children-failed is an explicit
  `{answer: "", used: 0, errors: [...]}`, never an empty success.
* **Backends**: `subprocess-jsonrpc` (real isolation + pid), `inprocess` (no RPC needed),
  `inprocess-fallback` (RPC failed — reported, not hidden).
* **Observability**: per-node `event_count`, `pid`, `backend`, `duration_ms`, `tool_calls`
  and the answer; `tree(id)` returns children, `cancel_tree(id)` cascades the cooperative
  stop. `python -m core.delegation --depth N [--self-test]` is a real smoke test.
* **Surfaces**: `POST /delegate` (async via the queue, or `sync: true`),
  `GET /delegation/status`, `GET /delegation/{tree_id}`, `POST /delegation/{tree_id}/cancel`,
  WS `{"action":"delegate"}`, tools `delegate_tasks` + upgraded `subagent_spawn`, CLI
  `hermus delegate "<goal>" --task … [--aggregate best|vote|concat|synthesize]`.
  `subagents/subagent.py` keeps the old function names as a facade.

## Testing

```
tests/test_hybrid_memory.py     23   FTS5/RRF/vector fallbacks, decay maths, budget, sweep
tests/test_skill_forge.py       24   gating, distillation, validate/quarantine, dedupe, outcomes
tests/test_sandbox.py           21   policy, limits actually enforced, env scrub, audit, CLI shape
tests/test_delegation.py        17   rpc frames, contract, aggregation, depth budget, cancel
tests/test_gateway_realtime.py  23   queue semantics, SSE replay, WS duplex, all HTTP routes, restart
```

All five run standalone (`python tests/test_gateway_realtime.py`) and under pytest; the
whole suite is 263 passed / 1 skipped. Tests set `HERMUS_HOME` to a temp dir, use private
stores (`Memory2(db_path=…)`, `JobQueue(persist=…)`, `SkillForge(skills_dir=…)`), and run
with `mock/mock`, so no API keys, Docker or Redis are required — the degraded paths are
*asserted*, not assumed.

## Known limits (deliberate)

* `local` fallback is rlimits + confinement, not a security boundary; on machines without
  Docker/Podman/bubblewrap the audit log is the accountability mechanism.
* `hash` embeddings are deterministic but not semantic — install Ollama (or point
  `HERMUS_*` at any OpenAI-compatible embedder) for real dense retrieval.
* Redis is an accelerator: single-node in-process lanes are the supported default, and
  queue durability comes from the JSONL log, not Redis.
* Streaming tokens depend on the provider supporting `stream: true`; the fallback is
  coarser, never wrong.
* Delegation children inherit the parent's tool permissions; `max_depth=2` and
  `max_workers=4` are the blast-radius knobs, and there is no cross-machine child (yet).

---

# Part III — Mission Engine, SWE Mode, Domain Verifiers, Artifacts, DAG, and Rollback

Consolidated architecture upgrades transforming Hermus into an objective-driven, reliable autonomous coding platform based on the 12 Upgrade Recommendations.

## 1. True Mission Engine (`core/mission.py`)
- **Objective Lifecycle**: Goal → Requirements → Subgoals (DAG) → Execute → Observe → Verify → Repair → Continue → Final Proof.
- **Dynamic Step Budget**: Adapts step limits dynamically based on verified progress rather than rigid turn counts.
- **Checkpoint & Resume**: Checkpoints persisted to `~/.hermus/missions/<id>.json` with full state resumption via `hermus mission resume <id>`.
- **Explicit Blocked State**: Distinguishes between task failures and environmental/permission blockers (`BLOCKED`) with actionable instructions.

## 2. Domain-Specific Verification Subsystem (`core/verifier_registry.py`)
- **Modular Domain Verifiers**:
  - `PythonVerifier`: AST syntax validation, automated test execution (`pytest`/`unittest`), runtime exception & traceback detection.
  - `AndroidVerifier`: Android project structure (`AndroidManifest.xml`, Gradle build configurations), APK/AAB container integrity & DEX inspection.
  - `WebVerifier`: HTML5 entrypoint, CSS/JS/TS asset checks, node configuration, live port HTTP endpoint verification.
  - `GitVerifier`: Working tree clean status, branch consistency, and commit history.
  - `LinuxVerifier`: Executable file permissions (`+x`), daemon process status, TCP port listening checks.
  - `ResearchVerifier`: Synthesis depth, word-count substance, source citation & URL validation.
  - `FileVerifier`: Schema & format integrity (JSON parsing, non-empty guarantees).
- **Auto-Detection & Composite Verification**: Auto-detects domain from task requirements or combines multi-domain verification pipelines.

## 3. Dedicated Software Engineer Mode (`core/swe_mode.py`)
- **8-Phase Engineering Lifecycle**: `INSPECT` → `PLAN` → `EDIT` → `BUILD` → `TEST` → `DEBUG/REPAIR` → `REVIEW_DIFF` → `PACKAGE_REPORT`.
- **Toolchain Auto-Detection**: Python, Node/TypeScript, React/Next.js, Rust (Cargo), Go, Kotlin/Android.
- **Automated Repair Loop**: Feeds compiler outputs and pytest tracebacks directly back into the repair prompt with automated checkpoint rollback on unrecoverable failures.
- **Unified Diff & Change Reports**: Generates full unified diffs and human-readable engineering change summaries.

## 4. Artifact-Centric Workspace (`core/artifact_manager.py`)
- **Deliverables as First-Class Entities**: Tracks APKs, ZIP bundles, build binaries, diffs, and test reports.
- **Metadata & Fingerprinting**: Records SHA256 hashes, byte sizes, preview capabilities, and mission linkage.
- **ZIP Bundle Exporter**: Package mission deliverables with an embedded `artifacts_manifest.json` in one command.

## 5. Dependency-Aware Agent DAG (`core/agent_dag.py`)
- **Directed Acyclic Graph Orchestration**: Defines stage dependencies across specialized agents (Researcher, Architect, Coder, Tester, Reviewer, Verifier).
- **Parallel Stage Execution**: Dispatches independent nodes simultaneously while enforcing dependency barriers.
- **Cycle Prevention**: Topological sort with Kahn's algorithm and cycle detection.

## 6. Task-Aware Model Routing (`core/router2.py`)
- **Capability-Based Routing**: Dynamically maps coding tasks to code-specialized models, complex architecture to deep reasoning models, visual tasks to vision models, and review to independent critic models.
- **Reliability & Cooldown Tracking**: Penalizes providers with consecutive errors/rate-limits and automatically routes to resilient alternatives.

## 7. Independent Critic & Verifier Panel (`core/critic.py`)
- **Tripartite Evaluation**:
  - `CodeReviewer`: Identifies syntax errors, unimplemented stubs, and maintainability concerns.
  - `SecurityAuditor`: Screens for hardcoded credentials, unsanitized `eval`/`exec`, shell injection risks, and root operations.
  - `OutcomeVerifier`: Proves that user objectives and requirements are demonstrably satisfied by artifacts and test outputs.

## 8 & 9. Self-Improvement, Reliability Scoring, and Skill Hardening (`core/skill_manager.py`)
- **Automated Regression Testing**: Automatically runs `test_skill.py` across all installed skills to detect regressions.
- **Skill Reliability Scoring**: Calculates historical success rate and verification bonuses.
- **Capability Declarations**: Validates declared permissions (`read`, `network`, `write_workspace`, `execute_sandbox`) before execution.

## 10. Unified Permission Enforcement (`core/permissions.py`)
- **Capability Architecture**: `READ`, `WRITE_WORKSPACE`, `WRITE_SYSTEM`, `EXECUTE_SANDBOX`, `EXECUTE_HOST`, `NETWORK`, `CREDENTIALS`, `GUI`, `ADMIN`.
- **PolicyGate Interceptor**: Guarantees all tool calls, subagents, and sandbox executions pass through centralized policy checks with append-only JSONL audit logging.

## 11 & 12. Transactional Rollback & Checkpoints (`core/rollback.py`)
- **Workspace Snapshots**: Checkpoint entire directories before risky tasks, compute diffs, and restore cleanly.
- **Git Branch Transactions**: Automatically isolate development in temporary `hermus/tx-<id>` branches with commit on verified success or clean rollback on failure.

## 13. Mission-Centric Control Room Dashboard
- Visualizes active missions, dynamic progress bars %, sub-goals with live state icons (✓, →, ■, ✕, ⚠), evidence metrics, generated artifacts, and recovery checkpoints.
