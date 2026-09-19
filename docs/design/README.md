# docs/design — Persistent Fleet v2 Design Docs

Index for the design documents behind the **Persistent Fleet** rebuild. The
master spec is [`SPEC_PERSISTENT_FLEET.md`](../../SPEC_PERSISTENT_FLEET.md)
(v2, repo root); everything in this folder is a companion to it — gap
registers, deep-dive implementation notes, and UX design — never a
replacement. Consolidation rule applies throughout: **one canonical owner per
concern, no parallel v2 subsystems.**

---

## Documents

| Document | What it is | Feeds roadmap step(s) |
|---|---|---|
| [`SPEC_PERSISTENT_FLEET.md`](../../SPEC_PERSISTENT_FLEET.md) (root) | **Master spec v2.** Audit of what exists (§1), target architecture (§3–§9), operations (§13), ordered roadmap (§10). | all |
| [`ARCH_GAPS.md`](ARCH_GAPS.md) | Architecture gap register **A1–A15** (event-sourcing core, lifecycle, persistence, single-owner rule). Each gap quotes its spec fix and names the files that change; includes a gap → roadmap-step mapping table. | 0b, 0c, 1, 3, 4, 6, 7 |
| [`OPS_GAPS.md`](OPS_GAPS.md) | Operational gap register **OG-1–OG-9** (per-account limits, dead-key resurrection, budgets, log growth, observability, testing, migration). | 0a, 0b, 0c, 5, 7 |
| [`VAULT_ACCOUNTS.md`](VAULT_ACCOUNTS.md) | Implementation note for **step 0a** — account-grouped budgets, persisted RPD counters, terminal `auth_failed`, account quarantine, header-driven dispatch, CLI surface, acceptance checklist. `core/multi_key.py` only. | **0a** |
| [`DASHBOARD_UX.md`](DASHBOARD_UX.md) | Dashboard / control-room UX design for the fleet era — intervention ladder, approval inbox, live screen panel, onboarding, mobile. (In progress.) | 4 |

---

## Roadmap checklist (spec §10 — ordered, each step shippable)

Foundation pre-steps (they change what everything else is built on;
retrofitting later = rewriting registry + orchestrator + dashboard):

- [ ] **0a. Vault economics** — `account_id` grouping, per-account budgets,
      persisted daily RPD, terminal `auth_failed` + manual re-enable, account
      quarantine, header-driven dispatch. `core/multi_key.py` only.
      _Informed by:_ [`VAULT_ACCOUNTS.md`](VAULT_ACCOUNTS.md) (full design +
      acceptance checklist), [`OPS_GAPS.md`](OPS_GAPS.md) OG-1, OG-2.
- [ ] **0b. Bus contract** — single writer, monotonic `seq`, per-agent
      cursors, idempotency keys, snapshot+replay, rotation/retention.
      _Informed by:_ [`ARCH_GAPS.md`](ARCH_GAPS.md) A1–A4, A10, A12;
      [`OPS_GAPS.md`](OPS_GAPS.md) OG-4.
- [ ] **0c. Mission object** — durable mission state machine + claim leases +
      budget envelope schema as bus events.
      _Informed by:_ [`ARCH_GAPS.md`](ARCH_GAPS.md) A5, A9;
      [`OPS_GAPS.md`](OPS_GAPS.md) OG-8 (partial-failure semantics).

Main build:

- [ ] **1. Fleet Registry** — `core/fleet/registry.py` on the v2 bus: durable
      spawn/list/update/dismiss/checkpoint; PAUSED/BLOCKED states; `assign()`
      runs a real `FreeLLM` turn via the Vault; migrate old rosters as
      SLEEPING.
      _Informed by:_ [`ARCH_GAPS.md`](ARCH_GAPS.md) A5–A7, A9, A10, A13.
- [ ] **2. Vault wiring** — `Vault.resolve()` binding + cooldown-rebind; env
      key auto-import; boot warm-up.
      _Informed by:_ [`VAULT_ACCOUNTS.md`](VAULT_ACCOUNTS.md);
      [`OPS_GAPS.md`](OPS_GAPS.md) OG-1.
- [ ] **3. Fleet Bus** — merge `core/agents/messaging.py` +
      `core/harness/bus.py` onto the 0b contract.
      _Informed by:_ [`ARCH_GAPS.md`](ARCH_GAPS.md) A1–A4, A12.
- [ ] **4. Gateway + dashboard** — REST + WS per spec §9; migrate (not
      replace) the existing control-room; intervention ladder + approval
      inbox + onboarding wizard; `routes_agents.py` becomes facades.
      _Informed by:_ [`DASHBOARD_UX.md`](DASHBOARD_UX.md);
      [`ARCH_GAPS.md`](ARCH_GAPS.md) A3, A7, A14.
- [ ] **5. Orchestrator** — mission state machine, leases, stall-replan,
      blackboard, verify-before-review, conflict contract, HITL gates; absorb
      `multi_ai.py` + `model_fleet` as tools.
      _Informed by:_ [`OPS_GAPS.md`](OPS_GAPS.md) OG-3, OG-8;
      [`ARCH_GAPS.md`](ARCH_GAPS.md) A13.
- [ ] **6. Sleep/resume** — true teardown + wake-on-message +
      LLM-summarizing compaction.
      _Informed by:_ [`ARCH_GAPS.md`](ARCH_GAPS.md) A8, A9;
      [`OPS_GAPS.md`](OPS_GAPS.md) OG-5.
- [ ] **7. Cleanup** — delete `subagents/`, fold `harness/swarm.py`, remove
      auto-destroy, consolidation gates in `tests/`, migration tooling.
      _Informed by:_ [`ARCH_GAPS.md`](ARCH_GAPS.md) A11, A13, A15;
      [`OPS_GAPS.md`](OPS_GAPS.md) OG-7, OG-9.


---

## Where things live — existing code → future canonical module

| Concern | Existing code (today) | Canonical owner (target) |
|---|---|---|
| API keys, budgets, health | `core/multi_key.py` (`MultiKeyManager`) | **Same module, extended** — becomes the Credential Vault (step 0a) |
| Provider presets / model abstraction | `core/providers.py`, `core/llm.py` (`FreeLLM`), `core/provider_resolver.py` | Kept as-is; Vault sits in front (`Vault.resolve()`, step 2) |
| Fan-out / map / race across models | `core/model_fleet.py` | Kept — becomes a **tool** the orchestrator calls (step 5) |
| Agent state machine | `core/agents/agent.py` (model kept, runtime is a stub) | `core/fleet/registry.py` (step 1) — delete the `asyncio.sleep(0.5)` stub |
| Agent messaging / bus | `core/agents/messaging.py`, `core/harness/bus.py` | `core/fleet/bus.py` — one merged, event-sourced bus (steps 0b, 3) |
| Agent identity registry | `core/agent_manager.py`, `gateway/queue.py` | `core/fleet/registry.py` (step 1); `gateway/queue.py` stays for user-initiated jobs only (A14) |
| Multi-AI debate personas | `core/multi_ai.py` | Absorbed into Orchestrator protocols; `PERSONA_PRESETS` kept (step 5) |
| Turn compaction | `core/harness/compaction.py` (truncates only) | LLM-summarizing compaction in sleep/resume (step 6) |
| Swarm spawn | `core/harness/swarm.py`, `subagents/` | Folded into fleet / **deleted** (step 7) |
| Tools (shell, files, web, computer, android) | `tools/*`, `core/tools/ToolGateway` | Kept — the only legal action path |
| Safety tiers, approvals | `core/safety_policy.py`, `core/approval.py` | Kept — wraps every agent action; drives the approval inbox (step 4) |
| Dashboard projection | `gateway/routes_jarvis.py`, `gateway/realtime.py`, `gateway/static/control-room.js` | **Migrated** (not greenfield) onto Fleet Registry (step 4); replace simulated VRAM probe with a real one |
| Mobile pause/resume page | `core/computer/remote.py` | Absorbed as the mobile fleet remote (step 4) |
| Migration tooling | none (`_normalize_entry` backfill + `.corrupt-<ts>` backups are the pattern) | `core/fleet/migrate.py` with `schema_version` + `--dry-run` (step 7) |

---

*Update this index when a doc is added or a roadmap step ships — check the
box, link the closing commit in the relevant gap register, don't delete
history.*
