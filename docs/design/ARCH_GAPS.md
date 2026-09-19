# Architecture Gaps — Design Review Register (A1–A15)

> Status: ACTIVE REGISTER. Companion to `SPEC_PERSISTENT_FLEET.md` (v2).
> Each gap is the delta between **what exists today** (§1 audit) and **what
> the spec mandates**. Every entry quotes its fix location in the spec and
> names the file(s) that will change. Severity: **P0** = blocks the
> persistent-fleet premise; **P1** = correctness/durability under failure;
> **P2** = polish. Consolidation rule applies throughout: **one canonical
> owner per concern, no parallel v2 subsystems.**

Gap groups: Event-sourcing core (A1–A4) · Lifecycle (A5–A9) ·
Persistence (A10–A12) · Single-owner rule (A13–A15).

---

## Group 1 — Event-sourcing core (seq / cursors / snapshots / idempotency)

### A1 — No single writer, no monotonic `seq` — **P0**

**Why it matters.** The current buses (`core/harness/bus.py`,
`core/agents/messaging.py`) have no total order. Without a monotonic `seq`,
CLAIM races are undecidable, replay is impossible, and "the bus is the
truth" is marketing, not mechanics. Today's bus also "caps at 500 msgs" and
does "full-rewrite saves" (§1, line 26) — both data-loss shapes.

**Spec fix.** §6 "Core mechanics", lines 273–275: *"Single writer +
monotonic `seq`. All appends go through one writer (async lock); `seq` is
the total order. No two events ever share a seq — this is what makes CLAIM
races decidable (first claim wins by seq)."* Envelope shape at §6 lines
253–264 (`seq`, `id`, `ts`, `sender`, `target`, `kind`, `priority`,
`content`, `reply_to`, `mission_id`, `trace_id`, `est_tokens`).

**Implementation pointer.** New canonical module `core/fleet/bus.py`
(single-writer append path, async lock, fsync per append). Merge/absorb
`core/agents/messaging.py` and `core/harness/bus.py` into it (§6, line 248:
*"One bus, merging `core/agents/messaging.py` + `core/harness/bus.py`"*).

### A2 — Mailboxes are queues, not cursors — **P0**

**Why it matters.** `Agent` mailboxes today are `asyncio.Queue` +
`_pending_responses` dicts — in-memory only. Restart or sleep loses every
pending message; PAUSED/SLEEPING agents accumulate unbounded in-memory
state. This is the delivery half of fatal gap #1 (§1, lines 38–39).

**Spec fix.** §6, lines 276–279: *"Cursors, not queues. An agent's
'mailbox' is just its `cursor` into the log: pending = events where
`seq > cursor` and addressed to me. SLEEPING/PAUSED agents accumulate
nothing in memory; restart/sleep lose nothing. (Replaces `asyncio.Queue`
mailboxes + `_pending_responses`.)"* `cursor` is a persisted field on
LiveAgent (§3, line 111).

**Implementation pointer.** `core/fleet/bus.py` (cursor read API,
`tail(after_seq=, target=)`), `core/fleet/registry.py` (persist `cursor`
per agent); delete queue machinery from `core/agents/agent.py`.

### A3 — No snapshot + replay boot path — **P0**

**Why it matters.** Without snapshots, boot replay time grows with log age
and the dashboard can never be a "pure projection" — it currently owns
duplicated state. Nothing today can reconstruct roster + mailboxes after a
crash.

**Spec fix.** §6, lines 284–287: *"Snapshot + replay. Periodic
`fleet.snapshot` (every ~10k events or 1h) writes full registry+agent state
with `last_seq`. Boot = load newest snapshot + replay tail after it. The
dashboard consumes (snapshot, tail) too — 'pure projection' is now actually
true."* Dashboard side: §9 line 447 (`GET /api/fleet/bus?tail=N&after_seq=`)
and line 469 (scrollback from snapshot+tail).

**Implementation pointer.** `core/fleet/bus.py` (snapshot writer, tail
replay), `core/fleet/registry.py` (boot = snapshot + replay; expose
snapshot to `gateway/routes_fleet.py` + `gateway/realtime.py`).

### A4 — No idempotency: replayed events re-execute side effects — **P0**

**Why it matters.** At-least-once delivery after a crash means a replayed
`screen_click` or shell command fires twice. The current system has no
dedup keys anywhere; §8 already assumes the protection exists (lines
409–410: *"All clicks go through the idempotency layer (§6) — a replayed
event never re-clicks."*).

**Spec fix.** §6, lines 280–283: *"At-least-once delivery + idempotency.
Replay after a crash can redeliver; consumers are idempotent via
`executed_task_ids` (task events) and event `id` dedup. Side-effecting
tools (shell, screen_click) are only invoked behind this dedup."* Fields:
event `id` in envelope (§6 line 256), `executed_task_ids` on LiveAgent
(§3 line 112). Task assignment is idempotency-keyed (§5 line 222; §9
line 434).

**Implementation pointer.** `core/fleet/bus.py` (event `id` dedup on
consume), `core/fleet/registry.py` (`executed_task_ids` set, persisted in
checkpoint), `core/tools/ToolGateway` call path (dedup check before
side-effecting tools), `core/computer/*` click path.


---

## Group 2 — Lifecycle (states, transitions, sleep, shutdown)

### A5 — Agents don't persist — **P0**

**Why it matters.** Fatal gap #1 (§1, lines 38–39):
*"`core/agents/Agent._registry` is an in-memory dict; everything dies on
restart, and nothing writes roster state to disk."* No persistent roster
means no persistent fleet — the entire spec premise.

**Spec fix.** §3 "Persistence guarantees", lines 133–137: *"Spawn is
durable. Creating an agent is ONE bus append (`agent.spawned`, fsync) — the
roster file `data/fleet/agents/<agent_id>.json` is a **derived cache**,
never authoritative."* Registry operation table §5 lines 218–226 (`spawn` →
bus append → derive roster cache). LiveAgent persisted fields §3 lines
100–113.

**Implementation pointer.** New `core/fleet/registry.py` (canonical owner,
absorbing `core/agent_manager.py`, `core/agents/pool.py`,
`core/harness/sessions.py` per §5 lines 213–214). Retire in-memory
`_registry` in `core/agents/agent.py`.

### A6 — Agents don't think, and aren't wired to keys — **P0**

**Why it matters.** Fatal gaps #2 and #3 (§1, lines 40–44):
`_handle_task_message` is a stub (`asyncio.sleep(0.5)` → "Task completed")
— never invokes an LLM, never uses a key, never calls a tool;
`MultiKeyManager` pools are "islands — no agent ever draws from them."

**Spec fix.** §10 step 1, lines 516–520: *"delete the
`asyncio.sleep(0.5)` stub — `assign()` runs a real `FreeLLM` turn via the
Vault."* Binding resolution §4 lines 187–197: per-call
`Vault.resolve(provider, model, key_name?)` → cooldown-rebind → terminal
`auth_failed` rebind; *"Agents never hold keys persistently."* Design rule
§2 lines 89–91: *"Keys are never owned by agents."*

**Implementation pointer.** `core/fleet/registry.py` (`assign()` task
runner), `core/multi_key.py` (extended into the Vault: `resolve()`, account
budgets, terminal failure states — §4), `core/llm.py` (`FreeLLM` turn),
`core/provider_resolver.py` (env key auto-import, §4 lines 184–186).

### A7 — State machine incomplete: no PAUSED / BLOCKED, illegal transitions unguarded — **P0**

**Why it matters.** Today's `Agent` has only IDLE/WORKING/THINKING (§1 line
25, verdict: *"Keep the model, **rewrite the runtime**"*). Without BLOCKED
there is no confirm-tier approval flow (an agent needing approval stalls
invisibly); without PAUSED there is no intervention ladder short of kill;
without a guarded transition function, corrupt states are undetectable.

**Spec fix.** §3 state machine, lines 116–129 — full transition list
including `WORKING ⇄ BLOCKED(awaiting approval/confirm-tier tool)`,
`PAUSED → IDLE | DESTROYED` *"(mailbox cursor frozen while paused)"*,
`ERROR → IDLE (explicit recovery path only — never silent auto-clear)`;
line 116: *"transitions are bus events; illegal transitions rejected."* §5
lines 241–242: *"Every state transition emits `agent.state_changed` so the
dashboard and CLI see the same truth."* BLOCKED UI treatment §8 lines
389–392, §9 line 456.

**Implementation pointer.** `core/fleet/registry.py` (guarded
`transition(agent_id, to)` — the ONLY state writer; emits
`agent.state_changed`), `core/approval.py` / `core/safety_policy.py`
integration (confirm-tier → BLOCKED), `gateway/static/control-room.js`
(state pills incl. `blocked-awaiting-approval`).

### A8 — Sleep is not real teardown — **P1**

**Why it matters.** Roster-cap economics (§13 lines 594–596: *"Roster cap
~50 live (non-SLEEPING) agents; beyond that, LRU idle agents are
auto-slept… SLEEPING = ~0 RAM"*) only hold if sleep actually frees the
asyncio task, queue, and memory. A flag-flip "sleep" leaks one task per
agent forever.

**Spec fix.** §3, lines 140–143: *"Sleep is real teardown. SLEEPING cancels
the listener task, drops the queue, flushes memory to checkpoint — zero
in-memory footprint. Wake restores from checkpoint and resumes from
`cursor`."* Transitions: `IDLE → SLEEPING (idle timeout: full teardown, not
just a flag)` (§3 line 125); `SLEEPING → IDLE (wake-on-message: restore
checkpoint, resume cursor)` (line 126). Memory compaction = LLM
summarization, new code (§3 lines 144–146).

**Implementation pointer.** `core/fleet/registry.py` (sleep/wake paths,
idle-timeout + LRU reaper), checkpoint writer (memory flush with
`schema_version` + `last_event_seq`), new LLM-summarizing compaction
alongside `core/harness/compaction.py` (which only truncates today, §1
line 29).

### A9 — No ordered shutdown: in-flight work silently lost — **P1**

**Why it matters.** Gateway restart mid-task currently drops in-flight LLM
calls and uncheckpointed memory. With durable cursors this is recoverable —
but only if shutdown actually checkpoints first and records interrupted
calls.

**Spec fix.** §3, lines 147–149: *"Shutdown ordering: checkpoint_all → wait
in-flight LLM calls (bounded grace ~10s) → final snapshot → exit. In-flight
calls past grace are recorded as interrupted and re-deliverable on boot via
cursor replay."* Chaos-test coverage §13 lines 609–611 (*"restart gateway
mid-task (cursor replay, no duplicate side-effects)"*).

**Implementation pointer.** `core/fleet/registry.py` (`checkpoint_all()`
per §5 line 226, shutdown sequencer), gateway lifespan hooks (app
startup/shutdown), `core/fleet/bus.py` (final snapshot).

---

## Group 3 — Persistence (schema_version, WAL-first, retention)

### A10 — WAL-first discipline: roster files must be derived caches — **P0**

**Why it matters.** The two-write split-brain bug class has already bitten
this repo once: §3 line 137 — *"This eliminates the two-write split-brain
class of bug that already wiped `api_keys.json` once."* Any write path that
updates a JSON file before (or instead of) the durable log recreates it.

**Spec fix.** §3 lines 133–137 (quoted in A5): *"WAL-first: event → fsync →
cache update; boot = rebuild roster from snapshot + log tail."* Same
discipline for persisted budgets: §4 lines 166–170 (daily RPD *"persisted —
not in-memory — so restarts don't reset it"*).

**Implementation pointer.** `core/fleet/bus.py` (append+fsync is the only
durable write), `core/fleet/registry.py` (roster JSON = cache, rebuilt on
boot), `core/multi_key.py` (budget windows persisted with the same
write-order rule; keep the existing `.corrupt-<ts>` backup pattern, §13
lines 621–622).

### A11 — No `schema_version` / no migration path for persisted state — **P1**

**Why it matters.** Fleet state files (checkpoints, snapshots, roster
cache, vault budgets) will evolve. Without versioned formats and explicit
N→N+1 migration, the first schema change either strands existing users'
agents or silently corrupts them.

**Spec fix.** §13 lines 619–620: *"Every persisted file carries
`schema_version`; `core/fleet/migrate.py` does explicit N→N+1 steps with
`--dry-run`."* Field on LiveAgent §3 line 113 (`schema_version: int` —
*"checkpoint format version"*); checkpoint stores `schema_version` +
`last_event_seq` (§3 line 146). Import rules §13 lines 623–627 (old
registries import as SLEEPING; old bus logs archived to `data/archive/`,
not migrated; rollback via importable-but-facaded old modules for one
release, line 628).

**Implementation pointer.** New `core/fleet/migrate.py`; version stamps
written by `core/fleet/bus.py` (snapshots), `core/fleet/registry.py`
(checkpoints/roster), `core/multi_key.py` (`data/api_keys.json` — reuse
`_normalize_entry`'s backfill, §13 line 623).

### A12 — Unbounded log growth: no rotation or retention — **P1**

**Why it matters.** A durable append-only JSONL log without rotation grows
forever, and boot replay time degrades to a function of total history
instead of snapshot cadence.

**Spec fix.** §6, lines 288–290: *"Rotation & retention: daily JSONL files
(`data/fleet/bus-YYYYMMDD.jsonl`); logs older than the newest snapshot +
grace period are archivable/deletable (§13). Boot replay time is bounded by
snapshot cadence, not log age."* §13 lines 601–602: *"Bus log growth is
bounded by §6 rotation + retention (archive/delete logs older than newest
snapshot + grace)."*

**Implementation pointer.** `core/fleet/bus.py` (daily file rotation,
retention sweeper keyed off newest snapshot's `last_seq`), `data/archive/`
handling per §13 line 626.


---

## Group 4 — Single-owner rule (JobQueue coexistence, consolidation gate)

### A13 — Split-brain agent systems: five parallel implementations — **P0**

**Why it matters.** §1 line 34 (verdict ❌ "Consolidate to ONE"):
`core/agents/*` vs `core/agent_manager.py` vs `core/multi_ai.py` vs
`core/harness/swarm.py` vs `subagents/`. Every parallel roster is a place
state can diverge, and §11 line 544 warns a framework import would *"add a
sixth agent system; the fix is connection, not more machinery."*

**Spec fix.** §2 design rule, lines 79–81: *"**One Fleet Registry.**
`core/agents`, `core/agent_manager.py`, `core/multi_ai.py`,
`core/harness/swarm.py`, `subagents/` all become thin facades or are
deleted. There is exactly one place an agent can 'live.'"* §5 lines
213–214: `core/fleet/registry.py` is the *"new canonical module, absorbing
`agent_manager`, `agents/pool.py`, `harness/sessions.py`."* Disposal
routes: `multi_ai.py` → Orchestrator protocols, PERSONA_PRESETS kept (§1
line 28); `harness/swarm.py` + bus → Fleet Bus (§1 line 26); `subagents/`
deleted (§10 step 7, lines 532–533).

**Implementation pointer.** New `core/fleet/registry.py` (sole owner);
facade-then-delete: `core/agent_manager.py`, `core/multi_ai.py`,
`core/harness/swarm.py`, `core/harness/sessions.py`, `subagents/`.

### A14 — JobQueue coexistence unresolved: two execution paths — **P1**

**Why it matters.** `gateway/queue.py` handlers (`agent.general`,
`agent.computer`) are a second agent execution path. If both the JobQueue
and fleet dispatch can run agent work, idempotency, budgeting, and the
state machine each need two implementations — and will diverge.

**Spec fix.** §5, lines 233–236 (decided): *"**JobQueue coexistence
(decided):** the canonical `gateway/queue.py` Job system stays for
*user-initiated background jobs*; `agent.general` / `agent.computer`
handlers are re-pointed at fleet dispatch so there is no second agent
execution path. Jobs ≠ agents; agents may *submit* jobs."*

**Implementation pointer.** `gateway/queue.py` (re-point agent handlers to
fleet dispatch), `core/fleet/registry.py` (accept job-originated tasks
through the same idempotency-keyed `assign()`).

### A15 — No consolidation gate: regressions silently re-introducible — **P1**

**Why it matters.** A13's consolidation is one refactor away from being
undone by the next PR that adds a convenience registry. Without a CI gate,
"one canonical owner" is a convention, not an invariant.

**Spec fix.** §5, lines 237–239: *"**Consolidation gate:** a test in
`tests/` asserts no second agent registry is importable (mirrors existing
consolidation gates) — CI fails if someone reintroduces a parallel
roster."* Reinforced at §13 lines 614–615: *"**Consolidation gates:** no
second agent registry importable; no direct provider SDK import outside the
model subsystem (existing pattern)."*

**Implementation pointer.** New test module under `tests/` (import-graph
assertion: only `core/fleet/registry.py` defines a roster; facades
re-export but define nothing). Runs in CI alongside existing consolidation
gates.


---

## Checklist — gap → roadmap step mapping (§10)

| Gap | Severity | Roadmap step(s) | Done when |
|---|---|---|---|
| A1 seq/single-writer | P0 | **0b**, 3 | One append path; monotonic seq; claim races decidable by seq |
| A2 cursors | P0 | **0b**, 3 | Mailboxes derived from log; restart loses nothing |
| A3 snapshot+replay | P0 | **0b**, 3, 4 | Boot = snapshot + tail; dashboard is pure projection |
| A4 idempotency | P0 | **0b**, 3, 4 | Replayed event never re-clicks / re-executes |
| A5 durable agents | P0 | 1 | Spawn survives restart; roster file is a cache |
| A6 thinking + key wiring | P0 | **0a**, 1, 2 | `assign()` runs a real `FreeLLM` turn via `Vault.resolve()` |
| A7 state machine | P0 | 1, 4 | PAUSED/BLOCKED exist; illegal transitions rejected; `agent.state_changed` emitted |
| A8 real sleep | P1 | 6 | SLEEPING = ~0 RAM; wake resumes from cursor |
| A9 shutdown ordering | P1 | 1, 6 | In-flight calls bounded-grace; interrupted work re-delivers on boot |
| A10 WAL-first | P0 | **0b**, 1 | No write path touches JSON before append+fsync |
| A11 schema_version/migration | P1 | 7 | `core/fleet/migrate.py` with `--dry-run`; old registries import as SLEEPING |
| A12 rotation/retention | P1 | **0b**, 3 | Daily JSONL rotation; replay time bounded by snapshot cadence |
| A13 split-brain consolidation | P0 | 1, 5, 7 | Exactly one roster owner; `subagents/` deleted; `multi_ai`/`swarm` absorbed or facaded |
| A14 JobQueue coexistence | P1 | 4 | `agent.general`/`agent.computer` handlers dispatch via fleet |
| A15 consolidation gate | P1 | 7 | CI test fails if a second registry becomes importable |

> Ordering note (§10 lines 501–512): **0a** (Vault economics,
> `core/multi_key.py` only), **0b** (bus contract), **0c** (mission object)
> are foundation pre-steps — *"Retrofitting later = rewriting registry +
> orchestrator + dashboard."* A1–A4, A10, A12 are therefore prerequisites
> for everything in groups 2 and 4.

*End of register. 15 gaps, 4 groups. Update this file when a gap closes —
mark it ✅ with the closing commit, don't delete it.*

