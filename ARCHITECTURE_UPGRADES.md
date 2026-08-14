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
