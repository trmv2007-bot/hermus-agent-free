# Sentinel — the always-alert core (FAIRY half of JARVIS × FAIRY)

HERMUS already had a **passive** always-on layer: the ambient loop polls every
15–30s, observes the world, and emits `attention.raised` events
(`core/ambient_loop.py`). The **sentinel** (`core/sentinel.py`) is the missing
*instant* layer: it subscribes to the canonical EventBus and reacts **inline in
the publisher's thread** — `EventBus.publish` fans out synchronously — so a
critical event is decided in milliseconds, not at the next tick.

```
event ──► EventBus.publish ──► Sentinel.handle_event (inline, same thread)
                                 │ salience score 0..1
                                 ▼
              ignore ── note ── enqueue ── alert
                          (attention)  (JobQueue)  (JobQueue + notify)
                                 └─ optional LLM triage (any configured model)
                                 └─ publishes sentinel.decision back on the bus
```

## Safety contract (non-negotiable, same as the roadmap invariants)

The sentinel **never executes tools itself**. It can only:

- publish `sentinel.decision` events on the bus,
- enqueue jobs through the canonical JobQueue (the same path proactive rules
  use — approval / red-line / emergency-stop stay authoritative upstream),
- send notifications through configured channels (Telegram etc.),
- write periodic event→memory digests (`consolidate`) — the "learning about
  my things" loop.

Duplicates de-escalate: the same event re-alerting is downgraded to `note`, so
you never get alert spam. LLM triage failures fall back to `note`, never to an
automatic action.

## Salience

`score = base_weight(event.type) + severity bonus + failed-status bonus
(+0.2 if the event text overlaps your current focus/goals from personal
context)`, capped at 1.0. Thresholds (env-tunable via `HERMUS_SENTINEL_*`):

| score | decision | side effect |
|---|---|---|
| ≥ 0.75 (`alert`) | `alert` | enqueue job **+** notify channel |
| ≥ 0.50 (`enqueue`) | `enqueue` | enqueue job via JobQueue |
| ≥ 0.45 (`triage`) | LLM triage (if enabled + model) | act/ignore per model, else `note` |
| ≥ 0.25 (`note`) | `note` | recorded + attention |
| < 0.25 | `ignore` | nothing |

## Run it

```bash
hermus sentinel run                      # daemon: inline on the event bus, forever
hermus sentinel run --model groq/...     # LLM triage with any configured model (auto | role | provider/model | mock/mock)
hermus sentinel run --once               # replay the last 100 events once and exit
hermus sentinel run --dry-run            # decide + record, never enqueue/notify
hermus sentinel test '{"type":"incident","command":"circuit_open","status":"failed","args":{"severity":"critical"}}'
hermus sentinel status                   # thresholds, bus cursor, recent decisions
hermus sentinel install                  # autostart: systemd user unit / Termux boot / cron @reboot / Windows schtasks
hermus sentinel alerts                   # tail data/sentinel/alerts.jsonl (local push-alert log)
hermus sentinel consolidate --deep       # one-shot learning pass (deep = recurring failures -> LessonsStore)
hermus sentinel nightshift               # cron: every day at 3:00 am -> hermus sentinel consolidate --deep
hermus sentinel rules                    # rule proposals + existing proactive rules
hermus sentinel approve <proposal_id>    # approve a proposal -> durable enabled proactive rule
```

Decisions are appended to `data/sentinel/decisions.jsonl`.

Gateway extras: `GET /api/healthz` (bus/sentinel/cron/watchdog/queue rollup)
and, with `HERMUS_PRESENCE_PROACTIVE_CHECKINS=1`, every sentinel alert queues
one cooldown-limited read-only check-in job (`runtime.turn`, `read_only: true`).
`make selfcheck` runs the sentinel/watchdog/ambient suites offline with
`--strict`.

## Measure it (the harness can test the sentinel directly)

Harness tasks have a `driver` field. `driver: "sentinel"` tasks feed `event` /
`events` through an isolated sentinel (in-memory bus, recorded enqueues/alerts)
and write `sentinel_decisions.jsonl` / `enqueued.jsonl` / `alerts.jsonl` into the
task workspace — so checks verify the always-on behavior itself:

```bash
hermus harness run sentinel --model mock/mock     # 6/6 offline
hermus harness tasks sentinel
```

`harness/suites/sentinel.json` covers: critical incident → alert+enqueue,
mid-severity → enqueue without alert, heartbeat → ignore, duplicate →
de-escalate, LLM triage consulted, multi-event stream ordering.

## Learning & the night shift

After `HERMUS_SENTINEL_RULE_PROPOSE_AFTER` (default 3) acted-on repeats of the
same `type|command` pattern, the sentinel writes a proposal to
`data/sentinel/proposed_rules.json` and emits `sentinel.rule_proposed` —
nothing auto-enables until you run `hermus sentinel approve <id>`, which
creates a durable, enabled rule in `ProactiveAutomation`.
`hermus sentinel consolidate --deep` (registered as the nightly cron by
`hermus sentinel nightshift`) distills recent events into memory and turns
recurring failures into `LessonsStore` lessons. Full picture: `docs/ALWAYS_ON.md`.

## Python API

```python
from core.sentinel import Sentinel, build_llm, envelope_from_dict, default_recorder

llm, res = build_llm("auto")  # any configured model
s = Sentinel(llm=llm, model=res["ref"], recorder=default_recorder("data/sentinel/decisions.jsonl"))
s.serve()  # subscribe + consolidate loop (Ctrl+C stops)
# or drive one event manually:
s.handle_event(envelope_from_dict({"type": "incident", "args": {"severity": "critical"}}))
```
