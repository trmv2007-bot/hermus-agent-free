# HERMUS Persistent Fleet — Architecture Spec v2

> Goal: an "allrounder" assistant — JARVIS-level helpfulness, Ultron-level
> internet/system reach — built on **persistent, self-orchestrating agents**
> backed by **pools of API keys** across many providers.
>
> Status: SPEC v2 (implementation roadmap in §10). v2 applies the 46-gap
> review (4-agent audit, 2026-09-19). Consolidation rule still applies:
> **one canonical owner per concern, no parallel v2 subsystems.**
>
> v2 changes at a glance: account-aware Vault budgets (§4), durable
> event-sourced bus with seq/cursors/snapshots (§6), leased claims +
> stall-replan + HITL gates in orchestration (§7), intervention ladder +
> approval UX in the dashboard (§9), new Operations section (§13).

---

## 1. What exists today (audit)

| Capability | Existing code | Verdict |
|---|---|---|
| Multi-key storage, health, RPM/TPM budgets, round-robin, parallel dispatch | `core/multi_key.py` (`MultiKeyManager`) | ✅ Keep — become the Credential Vault (§4); needs account grouping + terminal auth-failure state |
| Provider presets (OpenAI-compatible: Groq, OpenRouter, Gemini, DeepSeek, Together, HF, Ollama, Cerebras, SambaNova, NVIDIA NIM...) with free-tier budgets | `core/providers.py`, `core/llm.py` (`FreeLLM`), `core/provider_resolver.py` | ✅ Keep — the Model Abstraction (§4) |
| Fan-out / map / race across many models+keys | `core/model_fleet.py` | ✅ Keep — becomes a *tool* the orchestrator calls (§6) |
| Agent state machine (IDLE/WORKING/THINKING), message types, priorities | `core/agents/agent.py`, `core/agents/messaging.py` | ⚠️ Keep the model, **rewrite the runtime** (§5) — add PAUSED/BLOCKED states |
| Agent-to-agent bus, sessions, swarm spawn | `core/harness/bus.py`, `core/harness/sessions.py`, `core/harness/swarm.py` | ⚠️ Merge into the one Fleet Bus (§6) — current bus caps at 500 msgs, full-rewrite saves, no seq |
| Named agent identity registry + canonical Job queue | `core/agent_manager.py`, `gateway/queue.py` | ⚠️ Merge into the Fleet Registry (§5) |
| Multi-AI debate/collaboration personas | `core/multi_ai.py` | ⚠️ Absorb into Orchestrator protocols (§7); PERSONA_PRESETS kept |
| Turn compaction | `core/harness/compaction.py` | ⚠️ Truncates only — LLM-summarizing compaction is new code (§3) |
| System/internet tools (shell, files, browser, web_search, vision, computer, android) | `tools/*`, `core/tools/ToolGateway` | ✅ Keep — the only legal action path (§8) |
| Safety tiers, red lines, approval flow | `core/safety_policy.py`, `core/approval.py`, `RED_LINES.md`, `AUTONOMY_BOUNDARIES.md` | ✅ Keep — wraps every agent action (§8) |
| Dashboard projection + WebSocket events | `gateway/routes_jarvis.py`, `gateway/realtime.py`, `gateway/static/control-room.js` (1,177 lines, 11 tabs: error envelope + retryable toasts, `/readyz` pill, Safety tab, Missions tab, Emergency stop) | ⚠️ Migrate (not greenfield) onto Fleet Registry (§9); VRAM monitor is currently simulated (`Math.random()`, control.html:1213) — replace with real probe |
| Mobile pause/resume/cancel page | `core/computer/remote.py` | ✅ Absorb as the mobile fleet remote (§9) |
| **Duplicate/split-brain agent systems** | `core/agents/*` vs `core/agent_manager.py` vs `core/multi_ai.py` vs `core/harness/swarm.py` vs `subagents/` | ❌ Consolidate to ONE |

### The three fatal gaps

1. **Agents don't persist.** `core/agents/Agent._registry` is an in-memory
   dict; everything dies on restart, and nothing writes roster state to disk.
2. **Agents don't actually think.** `Agent._handle_task_message` is a stub
   (`asyncio.sleep(0.5)` → "Task completed"). It never invokes an LLM, never
   uses a key, never calls a tool.
3. **Agents aren't wired to keys.** `MultiKeyManager` and `ModelFleet` are
   islands — no agent ever draws from them.

---

## 2. Target architecture (layered)

```
┌─────────────────────────────────────────────────────────────────┐
│  DASHBOARD / CLI / VOICE / TELEGRAM / DISCORD / API             │
│  (projections + controls only — never own truth)                │
├─────────────────────────────────────────────────────────────────┤
│  GATEWAY  (FastAPI: /api/fleet/* REST + /ws/fleet WebSocket)    │
├─────────────────────────────────────────────────────────────────┤
│  ORCHESTRATOR  (task intake → plan → delegate → synthesize)     │
│  FLEET BUS     (one event-sourced log: seq + cursors +          │
│                 snapshots + idempotency; JSONL, single writer)  │
├─────────────────────────────────────────────────────────────────┤
│  FLEET REGISTRY  (persistent roster of LiveAgents)              │
│  LiveAgent = persona + model + key-binding + memory + cursor    │
│  states: SPAWNING → IDLE ⇄ WORKING / THINKING / PAUSED /        │
│          BLOCKED(awaiting approval) → IDLE                      │
│          IDLE → SLEEPING (full teardown + checkpoint) → IDLE    │
│          any → ERROR → IDLE (explicit recovery only)            │
│          IDLE → DESTROYED (explicit, user-confirmed only)       │
├─────────────────────────────────────────────────────────────────┤
│  MODEL GATEWAY  (FreeLLM / provider presets)                    │
│  CREDENTIAL VAULT (account-aware key pools × providers)         │
├─────────────────────────────────────────────────────────────────┤
│  TOOL GATEWAY  (shell/files/web/browser/computer/android)       │
│  wrapped by SAFETY (tiers: safe / confirm / forbidden)          │
└─────────────────────────────────────────────────────────────────┘
```

Design rules (extending ARCHITECTURE.md):

- **One Fleet Registry.** `core/agents`, `core/agent_manager.py`,
  `core/multi_ai.py`, `core/harness/swarm.py`, `subagents/` all become thin
  facades or are deleted. There is exactly one place an agent can "live."
- **Agents are async, in-process.** They live in the gateway process on the
  asyncio loop. No subprocess-per-agent.
- **The bus is the truth — fully.** Every message, task, claim, result and
  state change is an event with a **monotonic `seq`**, appended to a durable
  JSONL log by a **single writer**. Nothing authoritative lives only in
  memory: agent state, mailboxes, and the dashboard are all derivable by
  (snapshot + replay). See §6.
- **Keys are never owned by agents.** Agents hold a *binding request*
  (`provider`, optional `key_name`); the Vault hands out the actual key per
  call, enabling rotation, cooldown and failover transparently.
- **Budget enforcement lives in the Bus/Registry layer**, not in any agent —
  a crashed coordinator must never mean an unbounded mission (§7).

---

## 3. The LiveAgent lifecycle (persistence model)

```python
class LiveAgent:
    agent_id: str            # stable UUID, persisted
    name: str                # user-chosen: "Friday", "Scout-1"
    persona: str             # system prompt
    provider: str            # "groq" | "openrouter" | "gemini" | ...
    model: str               # "llama-3.3-70b-versatile" etc.
    key_name: str | None     # None = Vault picks (round-robin over account pool)
    state: AgentState        # see state machine below
    memory: list[Message]    # rolling conversation + LLM-summarized compaction
    skills: list[str]        # tool allowlist for this agent
    stats: AgentStats        # tasks done/failed, tokens, cost, latency
    cursor: int              # last bus seq this agent has consumed (the "mailbox")
    executed_task_ids: set   # idempotency dedup (at-least-once delivery)
    schema_version: int      # checkpoint format version
```

**State machine** (transitions are bus events; illegal transitions rejected):

```
SPAWNING → IDLE
IDLE → WORKING | THINKING | PAUSED | SLEEPING | DESTROYED
WORKING ⇄ BLOCKED(awaiting approval/confirm-tier tool)
WORKING → IDLE | ERROR | PAUSED
PAUSED → IDLE | DESTROYED            (mailbox cursor frozen while paused)
BLOCKED → WORKING (approved) | IDLE (rejected) | PAUSED
IDLE → SLEEPING (idle timeout: full teardown, not just a flag)
SLEEPING → IDLE (wake-on-message: restore checkpoint, resume cursor)
ERROR → IDLE (explicit recovery path only — never silent auto-clear)
IDLE → DESTROYED (user-confirmed; shows what is lost: memory age, task count)
```

**Persistence guarantees:**

- **Spawn is durable.** Creating an agent is ONE bus append (`agent.spawned`,
  fsync) — the roster file `data/fleet/agents/<agent_id>.json` is a **derived
  cache**, never authoritative (WAL-first: event → fsync → cache update;
  boot = rebuild roster from snapshot + log tail). This eliminates the
  two-write split-brain class of bug that already wiped `api_keys.json` once.
- **Agents stay until dismissed.** Task completion always returns to `IDLE`.
  Only the user destroys agents, via a confirm dialog showing what is lost.
- **Sleep is real teardown.** SLEEPING cancels the listener task, drops the
  queue, flushes memory to checkpoint — zero in-memory footprint. Wake
  restores from checkpoint and resumes from `cursor`. Cheap sleep is what
  allows large rosters (cap ~50 live agents; LRU-sleep beyond that, §13).
- **Memory compaction = summarization.** Old turns are compacted into an
  LLM-written summary (new code — `harness/compaction.py` only truncates);
  checkpoint stores `schema_version` + `last_event_seq` for replay anchoring.
- **Shutdown ordering:** checkpoint_all → wait in-flight LLM calls (bounded
  grace ~10s) → final snapshot → exit. In-flight calls past grace are
  recorded as interrupted and re-deliverable on boot via cursor replay.

---

## 4. Key & model management (the Vault)

Built on the existing `MultiKeyManager`, extended with the account dimension:

- **Accounts are first-class.** Provider rate limits are usually **per
  account/org, not per key** (Groq, Gemini, OpenAI — stated in
  `providers.py`). Every key entry gets `account_id` (user-supplied at add
  time, `--account` CLI flag; same-base_url+prefix heuristic proposes
  groupings for confirmation on import). **Pooling multiplies limits only
  across distinct accounts/providers** — the honest free-tier strategy is
  M providers × 1–2 keys each, plus several *accounts* per provider if the
  operator genuinely has them. Dashboard shows **effective account-level
  RPM**, not raw key count.
- **Three budget windows, persisted:** per-key RPM/TPM, **per-account**
  RPM/TPM, and **daily RPD** (UTC-midnight-anchored, persisted — not
  in-memory — so restarts don't reset it; seed OpenRouter `:free` at 50/day,
  Groq per-model RPD where published). `Vault.resolve()` enforces
  `min(per-key-remaining, per-account-remaining, daily-remaining)`.
- **Header-driven dispatch:** prefer `x-ratelimit-remaining-*` response
  headers (already parsed in `core/openai_compat.py`) over blind local
  windows — skip keys whose *account* is near-exhausted even when the local
  window looks clean.
- **Terminal failure states** (fixes dead-key resurrection): `401/403 →
  auth_failed` = **permanent** removal from rotation until the user manually
  re-enables (no 5-minute auto-retry — that pattern invites account bans);
  `429 → cooldown` with exponential backoff; `5xx → transient` retry.
  **Account quarantine:** N auth-failures across keys of one account → whole
  account parked + bus alert. All classification changes emit bus events.
- **Health probes:** background async task, 5-min staggered + jittered,
  cheapest available model, classified per the table above. Probe spend is
  accounted against the key's daily budget.
- **Env bridging:** keys in `.env` (`GROQ_API_KEY`, `GEMINI_API_KEY`, ...)
  auto-import into the Vault at boot via `provider_resolver` (with
  `account_id` prompted or defaulted per provider).
- **Binding resolution (per LLM call):**
  ```
  agent requests completion
    → Vault.resolve(provider, model, key_name?)
        → healthy key whose KEY + ACCOUNT + DAILY budgets all allow it
        → on 429/5xx: cooldown key, rebind next healthy binding, retry (bounded)
        → on 401/403: terminal auth_failed, rebind, alert
  ```
  Agents never hold keys persistently — per-call bindings keep
  rotation/cooldown/failover transparent. (Operator decision, open phase:
  the Vault API returns complete key records; agents/dashboard may read them.)
- **Fleet-wide parallelism:** "call all available models" fans out over
  `Vault.all_healthy_bindings()` via the existing async dispatcher
  (`aexecute_parallel_with_keys`), bounded by account budgets.
- **Free-tier pooling (honest version):** free keys (Groq, OpenRouter
  `:free`, Gemini, Mistral, Codestral, NVIDIA NIM, Cerebras, SambaNova —
  presets with free-tier budgets already in `core/providers.py`) are
  first-class Vault citizens. At boot, the existing
  `core/free_keys.discover_and_provision_free_models()` warm-up registers
  the free/community/local catalog. (Models bundled *inside* third-party CLI
  tools are tied to those tools' auth — not usable as raw API keys.)

---

## 5. The Fleet Registry (single owner)

`core/fleet/registry.py` (new canonical module, absorbing `agent_manager`,
`agents/pool.py`, `harness/sessions.py`):

| Operation | Behavior |
|---|---|
| `spawn(spec) -> LiveAgent` | bus append `agent.spawned` → derive roster cache; enters IDLE |
| `dismiss(agent_id)` | the ONLY destroy path; cancel in-flight task first, then user-confirmed destroy |
| `update(agent_id, patch)` | edit name/persona/model/key_name/skills of a live agent (bus event `agent.updated`) |
| `list() / get(agent_id)` | live state from memory, durable state from snapshot+replay |
| `assign(agent_id, task)` | IDLE → WORKING → IDLE; idempotency-keyed; result posted to bus |
| `pause(agent_id)` / `resume(agent_id)` | freeze/thaw cursor consumption without losing state |
| `cancel_task(agent_id)` | cancel current task (cooperative) → agent back to IDLE, task event marked `cancelled` |
| `broadcast(content)` | message to every non-destroyed agent |
| `checkpoint_all()` | periodic + on-shutdown flush of memory/state + snapshot |

Rules:

- **Name uniqueness:** case-insensitive unique names; collision → auto-suffix
  (`Friday-2`) with a warning. Migration from old registries may collide —
  resolved at import time (§13).
- **JobQueue coexistence (decided):** the canonical `gateway/queue.py` Job
  system stays for *user-initiated background jobs*; `agent.general` /
  `agent.computer` handlers are re-pointed at fleet dispatch so there is no
  second agent execution path. Jobs ≠ agents; agents may *submit* jobs.
- **Consolidation gate:** a test in `tests/` asserts no second agent registry
  is importable (mirrors existing consolidation gates) — CI fails if someone
  reintroduces a parallel roster.

Every state transition emits `agent.state_changed` so the dashboard and CLI
see the same truth with zero polling hacks.

---

## 6. The Fleet Bus (communication) — event-sourced, for real

One bus, merging `core/agents/messaging.py` + `core/harness/bus.py`. This is
the section where "the bus is the truth" becomes mechanically true.

**Event envelope:**

```json
{
  "seq": 10432,                    // monotonic, assigned by the single writer
  "id": "uuid",                    // idempotency key
  "ts": "...", "sender": "...", "target": "agent_id|null",
  "kind": "...",                   // see kinds below
  "priority": 1-4,
  "content": "...", "reply_to": "event_id|null",
  "mission_id": "...", "trace_id": "...",
  "est_tokens": 123                // for budget accounting (§7)
}
```

**Kinds:** `user_task`, `dm`, `broadcast`, `propose`, `claim` (single-writer —
see below), `subtask`, `result`, `review`, `synthesis`, `state_changed`,
`gate_pending`, `gate_resolved`, `mission_opened`, `mission_terminated`,
`budget_warning`, `alert`, `system`.

**Core mechanics (fixes the P0 architecture gaps):**

- **Single writer + monotonic `seq`.** All appends go through one writer
  (async lock); `seq` is the total order. No two events ever share a seq —
  this is what makes CLAIM races decidable (first claim wins by seq).
- **Cursors, not queues.** An agent's "mailbox" is just its `cursor` into the
  log: *pending = events where `seq > cursor` and addressed to me*.
  SLEEPING/PAUSED agents accumulate nothing in memory; restart/sleep lose
  nothing. (Replaces `asyncio.Queue` mailboxes + `_pending_responses`.)
- **At-least-once delivery + idempotency.** Replay after a crash can
  redeliver; consumers are idempotent via `executed_task_ids` (task events)
  and event `id` dedup. Side-effecting tools (shell, screen_click) are only
  invoked behind this dedup — a replayed event never re-clicks.
- **Snapshot + replay.** Periodic `fleet.snapshot` (every ~10k events or 1h)
  writes full registry+agent state with `last_seq`. Boot = load newest
  snapshot + replay tail after it. The dashboard consumes (snapshot, tail)
  too — "pure projection" is now actually true.
- **Rotation & retention:** daily JSONL files (`data/fleet/bus-YYYYMMDD.jsonl`);
  logs older than the newest snapshot + grace period are archivable/deletable
  (§13). Boot replay time is bounded by snapshot cadence, not log age.

**Agent-facing tools** (through ToolGateway, visible/auditable):
`agent_send`, `agent_broadcast`, `agent_request_help`, `agent_share_result`.

---

## 7. Self-orchestration (agents talking to agents)

Replaces the session-scoped `multi_ai.py` debate with a persistent protocol.
Skeleton is close to AutoGen Magentic-One's outer/inner loop; v2 adds the
failure-safety machinery from Magentic-One, LangGraph, and MetaGPT.

**Missions are durable event-sourced objects** — `mission_opened`,
`subtask.claimed`, `subtask.result`, `mission_terminated` are bus events, so
any agent can resume coordination by replaying the mission's event prefix.
A dead coordinator never orphans a mission and never disables budgets.

**Mission state machine** (transitions are bus events; invalid ones
rejected — makes deadlock states statically detectable):

```
PROPOSED → CLAIMING → WORKING ⇄ REVIEWING → SYNTHESIZING → DONE | FAILED
   any → SUSPENDED(gate) → resume at gate
   CLAIMING with no live agents → detected, escalated (replan or fail)
```

**Protocol:**

```
USER: "Research X, build me a report, and set up monitoring for it"
  │
  ▼
ORCHESTRATOR (designated coordinator LiveAgent, or elected per task)
  1. decompose: planner breaks goal into subtasks (LLM; fallback heuristic)
     → HITL gate `after_decompose` (default off; ON for parallel-all)
  2. broadcast PROPOSE: every IDLE agent sees subtasks + required skills
  3. CLAIM round (TIMED): agents claim matching subtasks
     - claims are single-writer events; first claim by seq wins
     - claimed work carries a LEASE (5 min, heartbeat-renewed by worker)
     - lease expiry or claim-round timeout → subtask returns to pool
  4. WORK: claimed agents execute — LLM turns + ToolGateway calls —
     posting results to the mission BLACKBOARD (shared per-mission channel;
     agents subscribe-filter instead of DM-spam: relevant results visible
     to teammates, est_tokens charged per event)
  5. stall detection: Progress Ledger (what's done / pending / stuck) +
     stall counter; 3 stalls → REPLAN (re-decompose remaining work);
     replan count bounded (max 2) → then mission fails honestly
  6. VERIFY: programmatic checks first (schema valid? file exists? test
     passes?) — only then LLM REVIEW (bounded, max 2 retries with critique)
  7. SYNTHESIZE with CONFLICT CONTRACT:
     - synthesizer MUST surface contradictions, not smooth them:
       `conflicts: [{claim, agents[], evidence[]}]` in the deliverable
     - resolution policy per mission: first_result | highest_reliability
       (uses §5 reliability stats) | judge_model | ask_user (default for
       user-facing factual claims)
     - losing results are archived on the blackboard, not deleted
  8. REPLY to user + all agents return to IDLE, still alive
```

**Chatter economics (budget enforcement in Bus/Registry, not the
coordinator):**

- Every bus event carries `est_tokens`; the per-mission budget decrements
  per event with per-edge and per-agent sub-quotas (default: no single agent
  may consume >40% of a mission's budget).
- 70% → `budget_warning` event: coordinator must request more (user
  approve) or switch workers to cheaper models (Vault heterogeneity).
- 100% → hard stop, `mission_terminated{reason: budget}`, partial synthesis
  with an explicit `missing: [...]` manifest still delivered.
- Vault RPM/TPM/RPD applies to agent-to-agent turns, not just user tasks.
  Optional MetaGPT-style `invest($)` dollar cap as a user-facing alias.
- Global daily cap across all missions (§13) as the final backstop.

**HITL gates (LangGraph interrupt-style, resumable):** missions declare
`hitl_gates`: `after_decompose`, `after_review`, `on_conflict`,
`on_budget_warning`, `on_stall_replan`. Default: all off except
`on_budget_warning`; parallel-all defaults `after_decompose` on. A gate
suspends the mission (fully checkpointed — resume is replay, not new code),
the dashboard shows approve/edit/reject, `POST
/api/fleet/missions/{id}/gate {action, edits?}` resolves it.

**Parallel-all mode:** broadcast the same prompt to all healthy agents (or
all healthy account-distinct bindings via the Vault) and synthesize —
`model_fleet.fanout` semantics, but agents stay alive afterwards.

---

## 8. System & internet control (Ultron reach, JARVIS manners)

Nothing new is invented — agents route through the **existing** `ToolGateway`:

- **Internet:** `tools/web_search.py`, `tools/browser.py` (Playwright),
  `core/web/*` (crawl/extract/sessions), `tools/internet_eyes.py`,
  `tools/public_apis.py`.
- **System:** `tools/shell.py`, `tools/file_tools.py`, `tools/vision.py`,
  `core/computer/*` (desktop control), `core/android/*` (phone control).
- **Safety tiers wrap every call** (existing `safety_policy` + `approval`):
  - `safe` — read-only web/files/search → auto-approve
  - `confirm` — shell mutations, writes outside workspace, installs, screen
    clicks → agent enters **BLOCKED** state; user approves in dashboard/CLI
    (one click, or pre-authorized per session); BLOCKED agents are visibly
    distinct in the UI (§9)
  - `forbidden` — RED_LINES entries → hard refuse, logged
- Each LiveAgent's `skills` field is an allowlist, so a "researcher" can
  never touch your shell while a "sysop" agent can.

### Screen-seeing and control (see → click → navigate)

Mostly **already built** in `core/computer/*`; agents get it as a skill:

- **See:** `recorder.py`/`frame_sampler.py` (continuous capture) +
  `video_analyzer.py`/`tools/vision.py` (vision-model description) +
  `world_state.py` (live screen model) → agent tool `screen_see()`.
  Screen capture is **on-demand** while an agent holds a screen task, not
  always-on (§13 resource limits).
- **Click:** `mouse.py`/`keyboard.py` (real pyautogui + dry-run fallback) +
  `grounding.py` (ground "the Save button" → bounding box, verify before
  click) → agent tools `screen_click(target)`, `screen_type`, `screen_scroll`.
  All clicks go through the idempotency layer (§6) — a replayed event never
  re-clicks.
- **Navigate:** `grounded_controller.py`/`planner.py`/`verifier.py`/`repair.py`
  (observe → act → verify → retry loop) + `watcher.py` ("wait until X
  appears") → agent tool `screen_watch(condition)`.
- **Android** mirrors the same loop via `core/android/*`.
- All screen actions default to the **`confirm` safety tier**.

---

## 9. Gateway API & Dashboard

New canonical router `gateway/routes_fleet.py` (absorbs `routes_agents.py`).
The dashboard work is a **migration of the existing control room**, not a
greenfield widget — `control-room.js` already has error envelopes, retryable
toasts, `/readyz` readiness pill, ARIA tablist, Safety tab (pending
approvals), Missions tab (resume), and a global Emergency stop; fleet UI
reuses all of it. The fake VRAM monitor (`Math.random()`) is replaced with a
real probe.

```
GET    /api/fleet/agents              roster: id,name,model,provider,state,stats,key(full)
POST   /api/fleet/agents              spawn {name, persona, provider, model, key_name?}
PATCH  /api/fleet/agents/{id}         update persona/model/key/skills of a live agent
DELETE /api/fleet/agents/{id}         dismiss (the only kill; confirm dialog w/ consequences)
POST   /api/fleet/agents/{id}/task    assign task (idempotency-keyed)
DELETE /api/fleet/agents/{id}/task    cancel current task (agent survives)
POST   /api/fleet/agents/{id}/pause   freeze cursor consumption
POST   /api/fleet/agents/{id}/resume  thaw
POST   /api/fleet/broadcast           message all agents
POST   /api/fleet/orchestrate         {goal, agents?: ids|"all", budget?, rounds?, hitl_gates?}
GET    /api/fleet/missions            mission list w/ state machine status
POST   /api/fleet/missions/{id}/gate  resolve HITL gate {action: approve|edit|reject, edits?}
GET    /api/fleet/approvals           pending confirm-tier approvals (screen clicks, shell...)
POST   /api/fleet/approvals/{id}      {action: approve|reject}
GET    /api/fleet/keys                vault overview (FULL keys visible — open mode, per operator)
POST   /api/fleet/keys                add key {provider, name, key, base_url?, account_id?, rpm?, tpm?, rpd?}
POST   /api/fleet/keys/{id}/re-enable clear terminal auth_failed (manual only)
GET    /api/fleet/bus?tail=N&after_seq=  snapshot+cursor-aware event history
GET    /api/fleet/screen/frame        latest annotated screen frame (grounding boxes)
WS     /ws/fleet                      live stream: state changes, messages, results, approvals
WS     /ws/fleet/screen               low-fps live screen mirror
```

**Dashboard "Agents" section (rewire `control-room.js`):**

- **Roster grid:** avatar, name, model+provider, state pill (idle / working /
  thinking / **blocked-awaiting-approval** / paused / sleeping / error),
  current task, tokens/cost, last activity; fleet summary strip (live count,
  effective account-level RPM, today's spend, budget burn); optional
  topology view (who is DMing whom during a mission).
- **Intervention ladder (graduated, not just kill):** pause/resume,
  cancel-task, redirect (DM now vs queue), dismiss with consequence dialog
  ("Friday has 3 days of memory, 41 tasks completed — destroy permanently?").
  Integrates with the existing global Emergency stop.
- **Approval attention:** badge on Agents tab, approval inbox panel, per-agent
  BLOCKED pill — the system must be able to get the human's attention when
  blocked on a confirm-tier action or HITL gate.
- **Live bus feed:** the mission conversation rendered with filtering (by
  agent/kind/mission), threading, verbosity tiers (summary ↔ full), and
  replay (scrollback from snapshot+tail — refresh loses nothing).
- **Error visualization:** per-agent error pill + last error, per-key health
  (auth_failed/quarantined distinct from cooldown), mission failure reason,
  budget exhaustion — errors are first-class UI, not toast-and-forget.
- **Spawn dialog:** provider → model (Vault-discovered) → account/key pool →
  persona preset (`multi_ai.PERSONA_PRESETS`); guardrails (name uniqueness,
  tool-capable model check).
- **Live screen panel:** `/ws/fleet/screen` mirror with grounding boxes
  overlaid; click a highlighted target to approve/correct the agent's next
  click (human-in-the-loop); approval queue for pending clicks; privacy
  masking toggle for sensitive windows.
- **Onboarding:** first-run wizard (add key → detect free tiers → spawn demo
  team → run demo orchestration) + designed empty states for every panel.
- **Mobile:** approval-first layout (pending approvals + emergency stop
  reachable without tab-scrolling); touch-safe grounding boxes (large
  targets + pinch-zoom); absorb the existing `core/computer/remote.py`
  mobile page as the fleet remote.
- **Notifications:** browser Notification API with per-type opt-in (task
  done / approval needed / agent error / mission done) + notification center
  drawer replayed from the bus.
- **UI polish:** tab deep-linking (`/control#agents`), UI state persistence
  (scroll/filters/selected agent), WS reconnect + lag indicator parity with
  the existing SSE stream, multi-client conflict handling: mutating
  endpoints accept the client's known `state_seq`; the server rejects stale
  writers with `409 {current_seq}` and the client re-reads and retries —
  optimistic locking over the bus's monotonic `seq` (no new machinery), plus
  conflict toasts ("agent was dismissed by another session").

---

## 10. Implementation roadmap (v2 — ordered, each step shippable)

Foundation pre-steps (they change what everything else is built on):

- **0a. Vault economics** — `account_id` grouping + per-account budgets +
  persisted daily RPD budgets + terminal `auth_failed` with manual
  re-enable + account quarantine + header-driven dispatch. Without this, the
  free-key pooling premise fails on real providers. (~1–2 days,
  `core/multi_key.py` only.)
- **0b. Bus contract** — single writer, monotonic seq, per-agent cursors,
  idempotency keys, snapshot+replay, rotation/retention. Retrofitting later
  = rewriting registry + orchestrator + dashboard. (~1 day design + 2 impl.)
- **0c. Mission object** — durable mission state machine + claim leases +
  budget envelope schema as bus events. Small now, expensive later.

Main build:

1. **Fleet Registry** — `core/fleet/registry.py` on the v2 bus: durable
   spawn/list/update/dismiss/checkpoint; full state machine incl.
   PAUSED/BLOCKED; delete the `asyncio.sleep(0.5)` stub — `assign()` runs a
   real `FreeLLM` turn via the Vault; migrate `agent_manager` roster (import
   old agents as SLEEPING).
2. **Vault wiring** — `Vault.resolve()` binding + cooldown-rebind; env key
   auto-import; boot warm-up via `free_keys.discover_and_provision_free_models()`.
3. **Fleet Bus** — merge messaging+harness bus onto the 0b contract.
4. **Gateway + dashboard** — REST + WS per §9; migrate (not replace) the
   existing control-room infra; intervention ladder + approval inbox +
   onboarding wizard; `routes_agents.py` endpoints become facades.
5. **Orchestrator** — mission state machine, leases, stall-replan, blackboard,
   verify-before-review, conflict contract, HITL gates; absorb `multi_ai.py`
   and `model_fleet` strategies as tools.
6. **Sleep/resume** — true teardown + wake-on-message + LLM-summarizing
   compaction.
7. **Cleanup** — delete `subagents/`, fold `harness/swarm.py`, remove
   auto-destroy, consolidation gates in `tests/`, migration tooling (§13).

---

## 11. Stack decision (keep it Python, keep it simple)

- **Stay with Python + FastAPI + asyncio.** Agents-as-asyncio-tasks in the
  gateway process is the simplest correct model for persistent agents.
- **No LangChain / AutoGen / CrewAI.** v2 borrows their *patterns* (Magentic-One
  ledgers, LangGraph interrupt/resume, MetaGPT blackboard + invest($)) — not
  their frameworks. Importing one would add a sixth agent system; the fix is
  connection, not more machinery.
- **Storage:** JSON/JSONL files with `schema_version` fields (repo
  convention) for snapshots + bus log; SQLite later only if roster queries
  get heavy.
- **Provider surface:** the existing OpenAI-compatible preset list means any
  provider with a base_url + key works, with zero per-provider code.

---

## 12. Security & authentication (DEFERRED — operator decision)

**Decision (operator, recorded):** security hardening is intentionally
postponed; the system runs open for now.

- Dashboard and all `/api/fleet/*` + WebSocket endpoints are **unauthenticated
  in this phase**. No login screen yet.
- Screen endpoints are **unrestricted**: `/api/fleet/screen/*` and
  `/ws/fleet/screen` expose the live screen and click-approval to any caller
  that can reach the gateway.
- Existing `HERMUS_GATEWAY_TOKEN` gating stays available but is **not
  enforced** until the operator re-enables it.

**Known risk, accepted by operator:** an open gateway that can see and click
the screen is effectively an unauthenticated remote desktop. This is only
safe while the gateway binds to localhost / a trusted network. **Before
exposing via Tailscale, LAN, or any public interface, re-enable auth** —
minimum: gateway token on all fleet + screen routes and WS ticket handshake.

**Patch checklist (when revisited):**
1. Enforce `_check_gateway_auth` on all `/api/fleet/*` routes + `/ws/fleet*`.
2. Dashboard login → session token; single-use WS `?ticket=` handshake.
3. Auth required on screen endpoints even on localhost.
4. Mask provider keys in API responses; keep raw keys out of bus events,
   logs, and dashboard payloads.

---

## 13. Operations (observability, resources, testing, migration)

**Observability:**

- `GET /metrics` — requests, tokens, cost by provider / account / key /
  agent / mission (Prometheus-compatible text).
- `trace_id` propagated everywhere (bus envelope carries it; LLM calls, tool
  calls, and mission events join up).
- Alerts are bus events (`alert` kind) — dashboard notification center,
  Telegram/Discord channel hooks get them for free.

**Resource limits:**

- Roster cap ~50 live (non-SLEEPING) agents; beyond that, LRU idle agents
  are auto-slept (not destroyed). Each live agent = 1 asyncio task + cursor +
  memory; SLEEPING = ~0 RAM.
- Screen capture runs only while an agent holds a screen task (on-demand),
  not as an always-on recorder.
- Global daily token/cost cap across all missions (per-mission budgets are
  §7; this is the backstop).
- Bus log growth is bounded by §6 rotation + retention (archive/delete logs
  older than newest snapshot + grace).

**Testing strategy:**

- **Scripted mock provider** (httpx MockTransport, precedent exists in
  `multi_key` tests): deterministic LLM responses per agent → full
  orchestration runs offline in CI.
- **Chaos tests:** kill coordinator mid-mission (lease reclaim + resume),
  expire claim leases, auth-fail keys mid-flight, restart gateway mid-task
  (cursor replay, no duplicate side-effects).
- **Soak test:** N agents idle + periodic wake for 1h — assert no task leak,
  no memory growth, bounded replay time.
- **Consolidation gates:** no second agent registry importable; no direct
  provider SDK import outside the model subsystem (existing pattern).

**Migration (existing users' data):**

- Every persisted file carries `schema_version`; `core/fleet/migrate.py`
  does explicit N→N+1 steps with `--dry-run`.
- Backup-first: timestamped copies of `data/api_keys.json` + old roster/bus
  files before any migration (mirrors the existing `.corrupt-<ts>` pattern).
- Keys: reuse `_normalize_entry`'s backfill; `account_id` prompted at
  import (heuristic-proposed groupings confirmed by user).
- Old agent registries import as SLEEPING agents (no mass-respawn of tasks).
- Old bus logs are archived to `data/archive/` (not migrated into the new
  log) and noted in the dashboard so history visibly didn't vanish.
- Rollback: old modules stay importable-but-facaded for one release.

---

*End of spec v2. All 46 review gaps applied. Remaining P2 polish is
specified inline (§9 multi-client optimistic locking via state_seq, WS lag
telemetry, quiet-hours); topology-view layout is tracked in the gap-analysis
outcome as a pure UI detail, not blocking.*
