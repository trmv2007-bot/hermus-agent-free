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

### Computer control (hybrid recording)
A rolling screen buffer (`ScreenRecorder`, last N seconds) instead of streaming
everything to a model. `FrameSampler` promotes only *changed* frames (motion /
UI events); `ScreenVerifier` confirms expected states from before/after frames
(optionally via a vision-model callback). Headless-safe (`NullSource` /
`CallableSource` for tests).

### Research pipeline
`search → dedupe → rank → extract claims → cross-check → contradictions →
synthesize → citations`, with confidence + uncertain-claims output. Default
synthesis is deterministic; pass an LLM `synthesizer` for real answers.

### Watchdog
Classify → known fix (apply) : diagnose (generate patch) → test → commit/rollback.
Default fixes: JSON-parse, missing-import, timeout-retry. Register custom fixes
via `register_fix(pattern, fn)`; attach `tester` / `rollbacker` callables.

## Deeper wiring (next steps)

These foundations are intentionally *not* yet forced into the default agent
path. To make them authoritative:

1. **Permissions** — in `core/tool_registry.execute`, call
   `permission_manager.check(name, agent=…)` and block/confirm non-ALLOW
   decisions before running the tool.
2. **Memory 2.0** — in `core/agent._build_system_prompt`, add
   `memory2.recall_prompt_block(user_message)` alongside the existing
   `curated_memory` block.
3. **Router** — in `core/agent.chat`, route each step's model via
   `router2.select(…)` instead of a fixed `self.model_name`.
4. **Autonomous loop** — wrap `HermusAgent.chat` as the `executor` of
   `AutonomousRunner` so multi-step goals get verify/repair gating.
5. **Watchdog** — run `watchdog.handle` from the gateway on task failures.
