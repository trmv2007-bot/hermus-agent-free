# OPS_GAPS — Operational Gaps from the Design Review

Scope: the 9 operational gaps surfaced when reviewing the Persistent Fleet spec
(`SPEC_PERSISTENT_FLEET.md`, "spec §" cross-refs below) against the shipped code
(`core/multi_key.py`, `core/providers.py`, `core/openai_compat.py`,
`gateway/`, `hermus_cli/g_models.py`). Each entry: ID, severity, why it bites,
spec cross-ref, implementation touchpoint.

Severity: **P0** = blocks roadmap step 0a / free-tier pooling premise; **P1** =
must land before a fleet runs unattended; **P2** = needed before any sustained
multi-agent run; **P3** = needed before v2 ships to existing users.

---

## OG-1 — Per-account vs per-key rate limits (P0)

**Why.** `MultiKeyManager` enforces RPM/TPM **per key**
(`_under_rpm`, `_tpm_used`, `_rpm_hits[provider][key]` in
`core/multi_key.py:316-336`), but provider quotas are per **account/org** —
this is stated in the `core/providers.py` docstring itself ("a budget is per
API key, but a provider's quota is usually per *organisation*"). The result:
**10 keys from one Groq account still share one 30-RPM pool.** Local windows
say each key has 30 RPM available; the account sees 10× oversubscription and
429s on all of them. Pooling only multiplies limits across *distinct
accounts*. The same problem applies to **daily RPD caps** that aren't modelled
at all: OpenRouter `:free` variants are capped at **50 requests/day** (1000
after $10 topped up — see the `openrouter` preset notes,
`core/providers.py:101-121`), Codestral at 2000/day
(`core/providers.py:188-200`), and Groq reports a *daily* request figure in
`x-ratelimit-limit-requests` (`requests_header_window == "day"`). Today a
restart also wipes the in-memory windows, so RPD counters must be persisted
(UTC-midnight anchored) to mean anything.

**Spec cross-ref.** §4 "Accounts are first-class", "Three budget windows,
persisted", "Header-driven dispatch"; §10 step 0a.

**Implementation touchpoint.** `core/multi_key.py` only (per roadmap):
add `account_id` / `rpd_limit` to key entries
(`add_key`, `_normalize_entry`), account-grouped windows next to
`_rpm_hits`/`_tpm_hits`, persisted daily counters in `data/api_keys.json`,
enforcement `min(per-key, per-account, daily)` inside `get_key()`.
Full design: [`VAULT_ACCOUNTS.md`](VAULT_ACCOUNTS.md).

---

## OG-2 — Dead-key resurrection every 5 minutes (P0)

**Why.** `get_key()` (`core/multi_key.py:362-374`): when a key has ≥3
failures it is skipped **for 5 minutes**, then its failure counter is reset to
0 and it goes back into rotation — regardless of *why* it failed. A revoked
or deleted key (401/403) therefore gets re-probed from every fleet worker
every 5 minutes forever. `mark_key_failed()` does record
`health_status = "auth_failed"` (`core/multi_key.py:570-575`), but nothing in
the selection path reads that status, so it has zero behavioural effect. On
free tiers this pattern (hammering a dead credential) is a known trigger for
**account-level bans** — which take out *every* key on that account, not just
the dead one.

**Spec cross-ref.** §4 "Terminal failure states": `401/403 → auth_failed` =
permanent removal from rotation until manual re-enable; `429 → cooldown` with
exponential backoff; `5xx → transient`; account quarantine on N auth-failures.

**Implementation touchpoint.** `core/multi_key.py`: extend
`mark_key_failed()` to write `auth_failed_at` + terminal state, gate
`get_key()` on it, add `reenable_key()` (manual only), account quarantine in
the same mutator. Sketch in [`VAULT_ACCOUNTS.md`](VAULT_ACCOUNTS.md) §3.

---

## OG-3 — Budget enforcement + global daily cap (P1)

**Why.** Per-minute budgets exist, but there is no spend/token **budget
envelope** per mission and no **global daily cap** as a backstop. A runaway
orchestrator loop (or a stuck stall-replan cycle) can burn the entire day's
free-tier quota across every provider before anyone notices — and with
paid keys attached, burn real money. `usage_count` is tracked per key
(`mark_key_success`, `core/multi_key.py:525-552`) but nothing ever reads it
to *stop* anything.

**Spec cross-ref.** §13 "Resource limits": "Global daily token/cost cap
across all missions (per-mission budgets are §7; this is the backstop)";
§7 mission budget envelope.

**Implementation touchpoint.** New: a persisted daily ledger (UTC-anchored,
same file/locking pattern as the key store — `_update()` in
`core/multi_key.py:84-90`), checked in the dispatch path before binding a
key; per-mission envelope enforced by the orchestrator (roadmap step 5) with
tokens returned from `usage` in `aexecute_parallel_with_keys()`
(`core/multi_key.py:788-843`) as the accounting source. Cap-exceeded must
surface as a typed failure, not a silent "no keys".

---

## OG-4 — JSONL growth bounds (P1)

**Why.** Event/audit/trajectory logs are append-only JSONL
(e.g. `core/approval.py` audit appends, `core/events/bus.py` journal,
`core/trajectory.py` checkpoints). Nothing rotates or prunes them. An
event-sourced bus on a busy fleet writes continuously; unbounded growth fills
the disk and — worse — makes replay-from-log (`since_cursor`) slower on every
restart.

**Spec cross-ref.** §13: "Bus log growth is bounded by §6 rotation +
retention (archive/delete logs older than newest snapshot + grace)"; §6
event-sourced bus; §0b bus contract (snapshot+replay).

**Implementation touchpoint.** `core/events/bus.py` (new canonical log):
segment the JSONL (size- or seq-based rollover), snapshot + truncate on
checkpoint, retention sweep for segments older than newest snapshot + grace;
`core/approval.py` and `core/trajectory.py` get simple size caps with
rotation to `data/archive/`. Must land with the 0b bus contract, not after.


---

## OG-5 — Idle-agent resource cost + roster cap (P1)

**Why.** Each live agent is an asyncio task + bus cursor + context memory.
Without a cap, a long-running gateway accumulates idle agents until RSS
grows without bound; there is no auto-sleep, so "idle" and "live" cost the
same. The existing `asyncio.sleep(0.5)` stub in the assignment path means
even "working" agents may not be doing real work while holding resources.

**Spec cross-ref.** §13: "Roster cap ~50 live (non-SLEEPING) agents; beyond
that, LRU idle agents are auto-slept (not destroyed)... SLEEPING = ~0 RAM";
§3 LiveAgent lifecycle; §10 step 1 (registry) and step 6 (sleep/resume).

**Implementation touchpoint.** `core/fleet/registry.py` (new, roadmap step
1): enforce `MAX_LIVE_AGENTS = 50` at spawn/update time; LRU-by-last-activity
eviction transitions agents to SLEEPING via the step-6 teardown path;
`agent_manager` roster imports as SLEEPING so upgrades don't mass-respawn
tasks (§13 migration).

---

## OG-6 — Metrics, tracing, alerts (P2)

**Why.** Today there is `rate_status()` (`core/multi_key.py:699+`) and the
canonical probes in `gateway/routes_canonical.py`, but no aggregated
`/metrics`, no `trace_id` joining LLM calls ↔ tool calls ↔ mission events,
and no alert channel. The first time a key dies or an account gets
quarantined in production, the only evidence is a log line.

**Spec cross-ref.** §13 "Observability": `GET /metrics` (Prometheus text;
requests, tokens, cost by provider/account/key/agent/mission); `trace_id`
propagated in the bus envelope; alerts as `alert`-kind bus events feeding the
dashboard notification center and Telegram/Discord hooks.

**Implementation touchpoint.** New `GET /metrics` in the gateway

---

## OG-7 — Multi-agent test strategy: mock provider, chaos, soak (P2)

**Why.** The fleet's failure modes are emergent (leases, rebinding, replay)
and only appear with several agents and a failing provider. There is good
precedent — `tests/test_universal_keys.py` runs a fake HTTP server and
`aexecute_parallel_with_keys` already accepts an injectable
`httpx.MockTransport` client (`core/multi_key.py:788-796`) — but no scripted
multi-agent orchestration test, no chaos coverage of coordinator death, and
no leak/soak test exists.

**Spec cross-ref.** §13 "Testing strategy": scripted mock provider
(deterministic per-agent responses → full orchestration offline in CI);
chaos tests (kill coordinator mid-mission, expire claim leases, auth-fail
keys mid-flight, restart gateway mid-task — cursor replay, no duplicate
side-effects); soak test (N agents idle + periodic wake for 1h — no task
leak, no memory growth, bounded replay); consolidation gates.

**Implementation touchpoint.** `tests/`: new `test_fleet_mock_provider.py`
(MockTransport scripted per agent), `test_fleet_chaos.py` (lease expiry,
mid-flight 401 via OG-2 terminal path, gateway restart + cursor replay),
`test_fleet_soak.py` (1h, RSS + task-count assertions). Gate consolidation in
CI per ARCHITECTURE.md §4.

---

## OG-8 — Partial orchestration failure semantics (P2)

**Why.** The parallel dispatch paths already define partial-failure behaviour
at the *task* level: `execute_parallel_with_keys` /
`aexecute_parallel_with_keys` return per-task `{success, error, ...}` dicts
and never raise on individual failure (`core/multi_key.py:767-843`). But at
the *mission* level nothing defines what happens when 3 of 5 subagents fail:
does the mission fail, replan, or synthesize from partial results? Without a
contract, every orchestrator retry doubles spend (OG-3) and risks duplicate
side-effects on replay (OG-7).

**Spec cross-ref.** §7 self-orchestration (mission state machine,
stall-replan, verify-before-review, conflict contract); §0c mission object
(durable state machine + claim leases + budget envelope).

**Implementation touchpoint.** Roadmap steps 0c + 5: mission state machine
with explicit `PARTIAL` outcome; per-subtask idempotency keys so replan
retries don't re-execute succeeded work; synthesis step declares required
quorum (e.g. "need ≥4/5 or replan once, then fail"). Typed failure classes
from `core/contracts.FailureClass` are the vocabulary (precedent:
`tests/test_capability_flows.py`).

---

## OG-9 — Migration path (P3)

**Why.** Existing users have `data/api_keys.json`, old agent rosters, and old
bus logs. `MultiKeyManager._normalize_entry()`
(`core/multi_key.py:97-135`) already backfills missing fields on load (the
`rpm_limit` backfill comment shows the pattern), and corrupt-file backups use
the `.corrupt-<ts>` pattern (`core/multi_key.py:63-72`) — but there is no
versioned, dry-runnable migration that adds `account_id`, imports rosters as
SLEEPING, and archives old bus logs without destroying history.

**Spec cross-ref.** §13 "Migration": every persisted file carries
`schema_version`; `core/fleet/migrate.py` does explicit N→N+1 steps with
`--dry-run`; backup-first (timestamped copies before any migration); keys
reuse `_normalize_entry`'s backfill with `account_id` prompted/heuristic
grouped; old rosters import as SLEEPING; old bus logs archived to
`data/archive/`; rollback via one release of importable-but-facaded old
modules.

**Implementation touchpoint.** New `core/fleet/migrate.py` + `schema_version`
in `data/api_keys.json` (the v1→v2 step is exactly the
[`VAULT_ACCOUNTS.md`](VAULT_ACCOUNTS.md) §5 backfill); roster import in
`core/fleet/registry.py` (step 1); archive step in the 0b bus work.

---

## Cross-reference table

| ID | Sev | Gap | Spec § | Primary touchpoint |
|----|-----|-----|--------|--------------------|
| OG-1 | P0 | Per-account limits + RPD caps | §4, §10-0a | `core/multi_key.py` (→ VAULT_ACCOUNTS.md) |
| OG-2 | P0 | Dead-key resurrection / ban risk | §4 | `core/multi_key.py` `get_key`, `mark_key_failed` |
| OG-3 | P1 | Budget enforcement + global daily cap | §13, §7 | dispatch path + orchestrator |
| OG-4 | P1 | JSONL growth bounds | §13, §6 | `core/events/bus.py`, `core/approval.py` |
| OG-5 | P1 | Idle-agent cost + roster cap | §13, §3 | `core/fleet/registry.py` |
| OG-6 | P2 | Metrics / tracing / alerts | §13 | gateway `/metrics`, bus envelope |
| OG-7 | P2 | Multi-agent test strategy | §13 | `tests/test_fleet_*.py` |
| OG-8 | P2 | Partial orchestration failure semantics | §7, §0c | mission state machine |
| OG-9 | P3 | Migration path | §13 | `core/fleet/migrate.py` |

(Prometheus text format) fed by `MultiKeyManager` counters + bus counters;
`trace_id` field on the 0b bus envelope and threaded through
`achat_completions` / `aexecute_parallel_with_keys`; emit `alert` events from
`mark_key_failed` terminal transitions and account quarantine (OG-2).

