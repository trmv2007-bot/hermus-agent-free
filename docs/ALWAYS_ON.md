# ALWAYS-ON: how HERMUS stays alert around the clock

The repo is not just a CLI you run — it is a small always-on organism. Five
pieces snap together so it notices things, tells you instantly, acts inside
its red lines, learns from what it sees, and survives reboots:

```
                 ┌──────────────────────────────────────────────┐
   host events ─▶│  EventBus (canonical, data/events.jsonl)     │
   (jobs, cron,  │      │                                       │
   watchdog, UI) │      ▼                                       │
                 │  ┌─────────┐   salience score   ┌─────────┐  │
                 │  │ Sentinel│───────────────────▶│ alert / │  │──▶ alerts.jsonl + Telegram + notify-send
                 │  │(core/  │  thresholds        │ enqueue │  │──▶ JobQueue (human-approved task shape)
                 │  │sentinel)│◀───────────────────│ / note  │  │──▶ decisions.jsonl + sentinel.decision events
                 │  └────┬────┘                    └─────────┘  │
                 │       │ proposes rules after N repeats         │
                 │       ▼                                        │
                 │  proposed_rules.json ──approve──▶ ProactiveAutomation (durable rules)
                 │       │                                        │
                 │  consolidate --deep (cron, 3 am) ──▶ memory + LessonsStore
                 │                                            │
                 │  AmbientLoop (passive observer) ◀───────────┘ attention.raised/cleared
                 │                                            │
                 └──────────────────────────────────────────────┘
        bootstrap.py ──best-effort──▶ `hermus sentinel install` (autostart: systemd user unit / cron @reboot / Termux boot / Windows schtasks)
        gateway up  ──▶ sentinel thread inline (HERMUS_SENTINEL_WITH_GATEWAY=1)
        control room ──▶ GET /api/sentinel/{status,decisions,alerts,proposals} + live bus stream
```

## 1. The sentinel — event-driven alert/act core (`core/sentinel.py`)

Every event on the bus gets a **salience score** (type weight + severity +
status + focus boost, duplicates de-escalate). Thresholds decide:

| salience | action | side effects |
|---|---|---|
| ≥ 0.75 | `alert` | enqueue **and** notify (alerts.jsonl → Telegram → notify-send) |
| ≥ 0.50 | `enqueue` | enqueue onto the canonical JobQueue |
| ≥ 0.25 | `note` | record only |
| below | `ignore` | record only |

The sentinel **never executes tools directly** — it can only enqueue and
notify; approvals and red lines stay authoritative. LLM triage (optional,
`HERMUS_SENTINEL_LLM_TRIAGE=1`) refines the mid band.

```bash
hermus sentinel run            # daemon: inline on the EventBus
hermus sentinel test           # fire a synthetic incident, watch it alert
hermus sentinel status         # thresholds + counters
hermus sentinel alerts         # tail data/sentinel/alerts.jsonl
hermus sentinel install        # autostart entry (systemd / cron / Termux / schtasks)
```

Measure it: `hermus harness run sentinel --model mock/mock` (6 scripted
decision scenarios, offline, no credentials).

## 2. Proactive rules — the sentinel learns patterns (`proposed_rules.json`)

After `HERMUS_SENTINEL_RULE_PROPOSE_AFTER` (default 3) acted-on repeats of the
same `type|command` pattern, the sentinel writes a **proposal** and emits
`sentinel.rule_proposed`. Nothing auto-enables: you approve explicitly.

```bash
hermus sentinel rules                    # proposals + existing proactive rules
hermus sentinel approve prop_88c8c69141 # -> ProactiveAutomation rule, enabled
```

## 3. Night shift — consolidation cron (plus memory decay)

```bash
hermus sentinel consolidate --deep   # one-shot: digest events -> memory; recurring failures -> LessonsStore; memory sweep (decay/archive/purge)
hermus sentinel nightshift           # register cron: every day at 3:00 am -> consolidate --deep
hermus sentinel nightshift --at "2:30 am" --remove
```

The lessons loop is closed and measured: lessons land in the agent's system
prompt via `LessonsStore.to_prompt_block` (`tests/test_learning_loop.py`),
and the `watchdog` harness suite proves fix-failed alerts and that repeated
failures become approvable rule proposals.

## 4. Ambient loop — the passive observer (`core/ambient_loop.py`)

Runs alongside everything else, diffing attention snapshots each tick and
emitting `attention.raised` / `attention.cleared` / `state.changed` on the
bus. Tested by the `ambient` harness suite: `hermus harness run ambient`.

## 5. Harness — product-level AI eval, isolated and parallel

The harness is a product surface, not just a dev tool: persistent run registry
with A/B compare and flaky-task stats, token + cost accounting per run, token
budgets (`--max-tokens`, `--strict`), a nightly product-eval cron
(`hermus harness schedule`), a read-only API (`/api/harness/*`), and
**regression events on the bus** — when a suite gets worse, the sentinel alerts
(`harness.regression`, weight 0.85). `make selfcheck` gates all of it offline.

```bash
hermus harness run fairy --model mock/mock            # builtin capability suite
hermus harness run sentinel --model mock/mock         # sentinel decision suite
hermus harness run ambient                            # ambient change-detection suite
hermus harness run mysuite.json --isolated --workers 4 # each agent task in its own subprocess
```

`--isolated` runs every agent task in a dedicated subprocess (own cwd — no
shared-chdir races), `--workers N` runs them in parallel, and every run
reports `total_tokens_est` / per-task `tokens_est` (estimated via
`core/token_counter.py`). Sentinel/ambient driver tasks always stay inline.

## 6. Wiring it everywhere

- **Fresh machine:** `bootstrap.py` runs `hermus sentinel install`
  best-effort (skip with `HERMUS_SKIP_AUTOSTART=1`) — systemd user unit where
  available, `cron @reboot` on Termux, a Task Scheduler command on Windows.
- **Gateway:** with `HERMUS_SENTINEL_WITH_GATEWAY=1` (default) the gateway
  starts a sentinel thread inline at boot and drains it on shutdown; model via
  `HERMUS_SENTINEL_MODEL`.
- **Control room:** `GET /api/sentinel/status|decisions|alerts|proposals`
  (gateway-auth gated) plus the live `sentinel.decision` events on the bus
  stream — the UI polls/ subscribes, it never edits pinned JS. One-glance
  liveness: `GET /api/healthz` rolls up bus cursor/subscribers, sentinel
  proposals, the night-shift cron's next run, last watchdog record, and queue
  depth.
- **FAIRY handoff:** with `HERMUS_PRESENCE_PROACTIVE_CHECKINS=1`, every
  sentinel **alert** also queues one read-only `runtime.turn` check-in
  (cooldown `HERMUS_SENTINEL_CHECKIN_COOLDOWN_SECONDS`, default 900s) —
  "the sentinel noticed; here's a safe status" — always through the canonical
  queue, never a direct model call from the sentinel.
- **Self-check:** `make selfcheck` runs the `sentinel` + `watchdog` + `ambient`
  harness suites at 100% on `mock/mock` with `--strict` (exit 1 on any
  failure or token-budget overrun via `--max-tokens`), fully offline.

## Architecture gates

`tests/test_architecture_gates.py` enforces the read-only rule: the
read-model/observer modules (`presence_kernel`, `ambient_loop`, `world_model`,
`focus_os`, `dashboard_state`, `control_room_state`) may not subprocess,
`os.system`, or touch the JobQueue for writes. Only `world_awareness`
(read-only host observation) and `sentinel` (JobQueue enqueue + notify only)
are exempt by design.

See `docs/SENTINEL.md` for the sentinel deep-dive and `docs/HARNESS.md` for
the harness.
