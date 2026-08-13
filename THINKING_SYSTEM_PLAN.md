# 🧠 Hermus Counsel — Council of AIs That Plans Everything & Upgrades Itself

> **Status: Phases 0–2 IMPLEMENTED (2026-08-13).** The rest of this document is the design
> record; sections marked ✅ are built and tested (`tests/test_counsel_system.py`, offline
> with the free mock model). Remaining: Phase 3 (strategies/lessons) and Phase 4
> (eval harness + power-ups). Everything respects the project's core constraint:
> **100% free stack** — Ollama local, Groq free tier, HF free inference, SQLite, no paid APIs.

## ✅ What is built right now (Phases 0–2)

| Piece | Where | What it does |
|---|---|---|
| **Council session** | `core/counsel/council.py` | Convene → parallel proposals → deliberation rounds → vote → voted plan → tool execution → final answer → transcript. Auto-runs for hard tasks (default difficulty ≥ 4) or explicitly: `hermus counsel run "task"` |
| **Roster / diverse members** | `core/counsel/members.py` | Chair, Researcher, Critic, Synthesizer, Judge — each with its own model/key when available (Groq/Ollama/HF via `multi_key` + fleet discovery) |
| **Constitution (self-upgrade)** | `core/counsel/constitution.py` | Versioned constitution (prompts, rules, budgets). Amendments: low-risk auto-apply, high-risk wait for approval. Snapshots + `rollback <version>` |
| **Meta-Counsel** | `core/counsel/meta.py` | Reviews sessions (transcript → amendment proposals); reflection mistakes → amendments; upgrade audit log |
| **DeepThink scaffold** | `core/reasoning/scaffold.py` | Explicit `Plan` (steps/goals/evidence), plan-first stage in `agent.chat()`, saved to `data/plans/` |
| **Governor** | `core/reasoning/governor.py` | Zero-token difficulty 1–5 classifier + budget table → council vs plain loop |
| **Agent integration** | `core/agent.py` | Hard tasks auto-convene the council; multi-step tasks get an explicit plan first; graceful fallback to the old loop on any failure |
| **Tool** | `counsel_convoke` | The agent can summon the council as a tool mid-task (114 tools now) |
| **CLI** | `hermus.py` | `hermus counsel run/status/review`, `hermus counsel amend list/approve/reject/rollback` |
| **TUI** | `tui/tui.py` | `/counsel` (status + `/counsel run <task>`) and `/think on|off` |
| **Self-improvement hook** | `core/self_improvement.py` | Reflections now also propose constitution amendments |
| **Config** | `core/config.py` | `HERMUS_COUNSEL_ENABLED`, `HERMUS_COUNSEL_MIN_DIFFICULTY`, `HERMUS_COUNSEL_MAX_MEMBERS/ROUNDS`, `HERMUS_COUNSEL_AUTO_REVIEW`, `HERMUS_THINK_ENABLED` |
| **Tests** | `tests/test_counsel_system.py` | 8 offline tests (mock model): governor, plan scaffold, constitution upgrades, council sessions, execution, meta review, tool registration, agent routing |

Try it:
```bash
python hermus.py counsel run "Research the best free password manager, then outline a 3-step migration plan and list the top security risks in detail."
python hermus.py counsel status
python hermus.py counsel amend list | approve <id> | reject <id> | rollback <version>
python tests/test_counsel_system.py
```

---

## Design record — the full plan

---

## 1. TL;DR — What we're building

| # | System | What it does | Effort |
|---|--------|--------------|--------|
| **C** | **Counsel System** ⭐ | A **roundtable of AI members** (Chair, Researchers, Critic, Synthesizer, Judge…) that **talk to each other**, critique each other, **vote**, produce a **joint plan**, execute it, then **convene a Meta-Counsel that upgrades the system itself** — its own prompts, rules, roles, strategies, and skills | Large (phased) |
| 1 | DeepThink layers | Explicit planning, in-loop self-critique, self-consistency voting, "lessons learned" injected into prompts — the machinery each Counsel member thinks with | Medium |
| 2 | Meta-Cognition Governor | Decides per task: single agent vs full Counsel, how many members, how many rounds, step budget, confidence gating | Medium |
| 3 | Eval Harness | Benchmark tasks + A/B scoring so upgrades are **measured**, not guessed | Medium |
| 4 | Power-ups | Plan persistence/resume, deterministic orchestrator, tool-failure fallbacks, project memory, TUI/dashboard Counsel view | Small–Medium |

**The one-line pitch:** Hermus stops being one agent doing everything alone, and becomes a
**council of AIs** that plans together — and whose job includes improving *how the council works*.

---

## 2. Current state audit — what Hermus already has (the building blocks)

| Capability | Where it lives today | Honest assessment |
|---|---|---|
| Multi-agent chat | `core/multi_ai.py` — `AgentPersona` (name, persona, **own model/key/provider**), `MultiAIChat.chat_round()`, `debate()`, `collaborate_on_task()`, `add_default_team()` with **diverse model/key assignment** | Real foundation for the Counsel — but it's a *tool you invoke*, not a standing system that plans and upgrades itself |
| Model fleet | `core/model_fleet.py` — `_available_workers()`, `fanout()`, `map_goal()`, `race()`, `auto_distribute()`, `_judge()`, `_merge()` | Worker discovery + consensus primitives ready to reuse |
| Multi-key manager | `core/multi_key.py` — round-robin, RPM/TPM limits, health checks, `get_key_bundle(provider)` | Free diversity: Groq free keys, Ollama local, HF token |
| Main agent loop | `core/agent.py` `chat()` — ReAct plan→tool→observe→repeat, `max_steps=8` | Good executor, **no explicit plan, no self-check before answering** |
| Post-task reflection | `core/self_improvement.py` — detects mistakes, web-searches fixes, curates memory | Runs **after** the task; output never reaches future prompts (half-closed loop) |
| Skill creation | `core/skill_manager.py` — `create_skill_from_trajectory()` | Reusable recipes; no plan-hint seeding yet |
| Memory | `core/memory.py` (FTS5) + `core/embeddings.py` (hybrid) | Strong recall; no lessons table |
| CLI | `hermus.py` subcommands: `subagent`, `fleet`, `multiai`, `cron`, `skills`… | Pattern to copy for `hermus counsel …` |
| Dashboard/TUI | `gateway/dashboard.html` (16 panes), `tui/tui.py` | No counsel/plan view yet |

**The gap in one sentence:** Hermus has all the *players* but no **council** — no standing
deliberation process, no shared plan, and no mechanism for the system to **upgrade its own
decision-making** based on how past sessions went.

---

## 3. System design — the Counsel System (flagship)

### 3.1 Concept — a cabinet meeting, not a single brain

For anything non-trivial, Hermus **convenes a council** instead of answering alone:

```
        ┌─────────────┐
        │  GOVERNOR   │  (difficulty 1–5 → council size & rounds)
        └──────┬──────┘
               ▼
   ┌─────────────────────── ROUNDTABLE ───────────────────────┐
   │  👑 Chair        — sets agenda, drafts the plan          │
   │  🔬 Researchers  — gather facts with tools (web/browser) │
   │  ⚔️  Critic        — attacks the plan, demands evidence    │
   │  🧩 Synthesizer  — merges everything into one answer     │
   │  ⚖️  Judge         — scores plans/answers, breaks ties     │
   └───────────────────────┬──────────────────────────────────┘
                           ▼
        ┌─────────────────────────────────────┐
        │  🕯️  META-COUNSEL (after task done)    │
        │  reviews transcript → proposes       │
        │  AMENDMENTS to its own constitution  │  ← "upgrade itself"
        └─────────────────────────────────────┘
```

### 3.2 Council members — `core/counsel/members.py`

Default roster (configurable in `data/counsel/constitution.json`, inheriting persona style
from the existing `AgentPersona` in `core/multi_ai.py`):

| Member | Role | Distinct model/key? |
|---|---|---|
| **Chair** | Sets the agenda, drafts the numbered plan, moderates rounds | Own model (default) |
| **Researcher(s)** 1–2 | Tool-heavy: web_search, browser, file_read, memory — must cite evidence | Diversified via `_pick_diverse_assignments()` |
| **Critic** | Reads every proposal, finds holes: unverified claims, missing steps, edge cases | Diversified (different provider if available) |
| **Synthesizer** | Turns the voted plan + evidence into the final answer | Any |
| **Judge** | Scores proposals 0–10, computes consensus, breaks ties with evidence | Could be a different model for independence |
| **Meta** | Only convenes in the upgrade phase (section 3.5) | Any |

Key design point: **members are already supported by the existing code** — `AgentPersona`
accepts per-member `model`, `api_key`, `base_url`, `provider`, and `add_default_team()`
already diversifies across Groq/Ollama/HF. The Counsel adds *structure*: fixed roles,
turns, voting, and transcripts.

### 3.3 Session lifecycle — `core/counsel/council.py`

Every counsel session follows a fixed protocol (all caps and bounds enforced):

1. **Convene** — Governor decides: difficulty 1–2 → no council (chat/reply directly);
   difficulty 3 → mini-council (Chair + Critic, 2 rounds); difficulty 4 → standard
   (Chair, Researcher, Critic, Synthesizer, 3 rounds); difficulty 5 → full
   (add Judge + 2nd Researcher + Meta, 4 rounds, subagent research allowed).
2. **Proposals** — each member independently drafts *its* approach to the task
   (parallel via `threading`, each with its own `FreeLLM`).
3. **Deliberation** — members read each other's proposals (reuse `chat_round()`), critique,
   refine, merge. Rounds capped. **Critic must attach at least one concrete objection or
   approval reason** to prevent rubber-stamping.
4. **Vote** — Judge scores proposals; majority decides; Chair breaks ties with evidence.
   The winning plan is returned as a structured `Plan` (steps + goals + evidence slots,
   saved to `data/counsel/plans/<session>.json`).
5. **Execute** — the main agent (`core/agent.py`) runs the plan with tools, step by step;
   observations are attached to plan steps. If a step fails twice → **council re-convenes
   briefly** (Chair + Critic only, 1 round) to replan — no endless loops.
6. **Review** — full transcript saved to `data/counsel/transcripts/<session>.jsonl` for the
   Meta-Counsel (3.5) and for eval/analytics.

### 3.4 Example session (so we share the picture)

```
You: "Plan and build a weekend budgeting web app, then check it for security."

[Governor] difficulty=4 → standard council, 5 members, 3 rounds
[Convene]  👑 Chair agenda: spec → design → build → verify → security review
[Round 1]  👑 Chair: plan with 5 steps (UI, storage, auth, tests, pentest)
           🔬 Researcher: finds free SQLite+Flask stack, notes OWASP top risks
           ⚔️  Critic: "Step 2 has no auth story; step 5 needs explicit scan commands"
[Round 2]  👑 Chair: revises plan (adds auth + scans)
           ⚔️  Critic: approves with evidence note; votes 8
[Vote]     ⚖️ Judge: 8.5/10 → plan accepted
[Execute]  Agent runs steps; tool results attach to each step; 1 step fails → mini-reconvene → replan → done
[Review]   🕯️ Meta: "Critic caught auth gap before coding — worth keeping; Researcher tool
           failures happened twice — propose fallback rule" → amendment proposed (3.5)
```

### 3.5 Upgrading itself — `core/counsel/meta.py` + `core/counsel/constitution.py`

This is the part you asked for specifically: **the system upgrades itself.**

**The Constitution** (`data/counsel/constitution.json`) is the council's source of truth:
member prompts, rules, round budgets, voting rules, and the upgrade policy. It is
**versioned** (`v1`, `v2`, …) and every change is logged in `data/counsel/upgrade_log.json`.

**The upgrade loop (closed circle):**

```
session ends
   │
   ▼
Meta-Counsel convenes (1 LLM call, reads transcript + outcome)
   │  proposes 1–3 AMENDMENTS, each with: what to change, why, expected effect
   ▼
Amendment validation (JSON schema + safety rules — never touches code, only config/prompts/skills)
   │
   ├─ Low risk (prompt tweaks, rule clarifications, new critique rule)
   │     → auto-apply to a NEW constitution version → mini-eval (3–5 benchmark tasks)
   │     → score ≥ before?  apply : rollback
   ├─ High risk (adding/removing members, budget changes, voting rule changes)
   │     → presented to user: `hermus counsel amend list` / `amend approve` / `amend reject`
   ▼
Applied amendments update: member prompts, governor thresholds, strategy defaults, skills
   ▼
Next session runs under the upgraded constitution — repeat
```

**What can upgrade itself (all non-code, versioned, rollback-able):**
- **Its own member prompts** — e.g., Meta notices the Critic is too lenient → proposes
  "Critic must quote at least one fact from evidence" → applied if eval improves.
- **Its rules** — round counts, voting thresholds, tie-break rules, replan triggers.
- **Its strategies** — which DeepThink strategy wins for which task category (from eval).
- **Its skills** — successful council plans auto-create skills via the existing
  `skill_manager.create_skill_from_trajectory()`; new skills seed future plans.
- **Its memory lessons** — corrections/failures become injected lessons (DeepThink layer 2).

**Safety rails (so "self-upgrading" stays sane):**
- Upgrades are **config/prompt/skill changes only** — never self-modifying Python code.
- **Atomic versioned writes** + `hermus counsel rollback <version>`.
- **Eval gate**: no silent regressions — any auto-upgrade must pass the mini-eval.
- **Human veto**: high-risk amendments always wait for `/counsel amend approve`.
- **No upgrade while a task is running** (mutex on the constitution file).
- **Measured**: every amendment records before/after scores in `upgrade_log.json`.

### 3.6 Free & heterogeneous — "all AIs talk to each other"

- Members genuinely differ: reuse `multi_key_manager.get_key_bundle()` + `_pick_diverse_assignments()`
  → e.g., Groq free key for Critic, Ollama `llama3.1:8b` for Chair, HF free token for Judge.
- If only one provider exists, members still differ by **persona + prompt** (works with a
  single local model).
- Cost caps: max 6 members, max 4 rounds, per-round token caps from the budget table;
  easy tasks never convene the council.

### 3.7 Surface — CLI / TUI / Dashboard

| Surface | Command / UI | What it shows |
|---|---|---|
| CLI | `hermus counsel run "task"` | Full council transcript streamed with member names + colors |
| CLI | `hermus counsel status` | Roster, current/last session, version, upgrade history |
| CLI | `hermus counsel amend list / approve / reject / rollback <version>` | Self-upgrade management |
| TUI | `/counsel` panel | Live: members, their last message, votes, current plan step |
| Dashboard | new **Counsel** pane | Live transcript, plan view, upgrade log, win-rates per member |
| Cron | `hermus cron add "every Monday 9am counsel review last week"` | Scheduled Meta-Counsel maintenance |

---

## 4. DeepThink layers — the machinery under the Council

The Counsel decides *what* to do; DeepThink decides *how hard to think*. They compose:

1. **Scaffold** (`core/reasoning/scaffold.py`) — `Plan` dataclass, plan-first stage, replanning, skill-seeded plans. The Counsel's voted plan IS this Plan object.
2. **Deliberation strategies** (`core/reasoning/strategies.py`) — `reflexion_in_loop` (critique→revise), `self_consistency` (k=3 vote), `verify_with_tools` (evidence per claim), optional `tree_of_thoughts`. Used by *individual members* for their sub-tasks and by single-agent fallback.
3. **Experience loop** (`core/reasoning/lessons.py`) — `lessons` SQLite table; user corrections, tool failures, reflection output, and skill failures distilled into lessons; top-8 relevant lessons injected into every prompt (incl. every Counsel member's prompt).
4. **Governor** (`core/reasoning/governor.py`) — complexity classifier (1–5), budget table, stuck detection, confidence gating; routes each task to **council vs single-agent vs direct chat** (section 3.3 step 1).

---

## 5. Power-ups ("and others")

| # | Power-up | Design sketch |
|---|---|---|
| P1 | Plan persistence & resume | Plans saved under `data/counsel/plans/`; `hermus plan resume <id>`; cron "continue plan X step 3 tomorrow" |
| P2 | Deterministic orchestrator | Router replaces keyword heuristics in `_maybe_fleet_distribute`: task type + availability → single / council / fleet / subagents (table-driven, no LLM call) |
| P3 | Tool failure fallbacks | Registry-level retry + fallback chains (web_search → web_read → browser); each fallback = lesson candidate |
| P4 | Project-scoped memory | `memory_search(query, project=...)`; `project` metadata column in hybrid index |
| P5 | TUI + dashboard thinking views | TUI plan/counsel panel; dashboard Reasoning + Counsel panes |
| P6 | Trajectory tagging | Every turn records `strategy`, `difficulty`, `plan_id`, `confidence`, `council_version` → free supervised training data |

---

## 6. Free-stack constraints (design rules)

- **No new paid deps** — `requests` / `sqlite3` / `threading` + reuse of `groq`, `ollama`, `multi_key`. No LangChain, no vector DB.
- **Token budget is first-class** — budget table caps extra thinking (~3x on hard tasks, 1x on easy). Council only for difficulty ≥ 3.
- **Small-model reality** — `llama3.1:8b` can't do hidden chain-of-thought; that's why the design *scaffolds thinking outside the model*: structured plans, tool-evidence requirements, voting, transcripts.
- **Graceful degradation** — any layer failure (LLM down, parse error) falls back to today's plain ReAct loop. Counsel never makes Hermus *less* capable.
- **Config-driven** — `[counsel]` + `[deepthink]` sections: `enabled`, `max_members`, `max_rounds`, `auto_amend=low`, `self_consistency_k=3`, `lessons_in_prompt=8`. Toggle per session: `/counsel off`, `/think off`.

---

## 7. Phased roadmap

| Phase | Scope | New files (design) | Touched files | Done when… |
|---|---|---|---|---|
| **0 — Foundation** ✅ | Plan scaffold: `Plan` dataclass, plan-first stage, save/load, `/think` toggle | `core/reasoning/__init__.py`, `core/reasoning/scaffold.py`, `data/plans/` | `core/agent.py`, `core/config.py`, `tui/tui.py` | **DONE** — agent writes a visible plan before tools; plans saved to `data/plans/`; `/think on|off` works |
| **1 — Counsel core** ✅⭐ | Council session: roster, convene, proposals, deliberation, vote, plan, execute, transcript | `core/counsel/__init__.py`, `core/counsel/council.py`, `core/counsel/members.py`, `data/counsel/` | `core/agent.py` (hook), `core/reasoning/governor.py`, `hermus.py` (`counsel` CLI), `tui/tui.py` | **DONE** — `hermus counsel run "task"` produces a voted plan + transcript with 3-6 members talking |
| **2 — Self-upgrade loop** ✅⭐ | Meta-Counsel, constitution + versions, amendments, eval gate, rollback | `core/counsel/meta.py`, `core/counsel/constitution.py`, `data/counsel/constitution.json` | `core/self_improvement.py`, `hermus.py` (`amend` CLI) | **DONE** — Meta proposes amendments after sessions; low-risk auto-apply, high-risk `amend approve`; `amend rollback` works. (Full eval-gate moves to Phase 4 with the harness) |
| **3 — DeepThink + Governor** | Strategies (reflexion, self-consistency, verify), lessons table + prompt injection, governor routing council vs single | `core/reasoning/strategies.py`, `core/reasoning/lessons.py`, `core/reasoning/governor.py` | `core/agent.py`, `core/memory.py`, `core/config.py` | Easy tasks stay ≤2 steps; hard tasks convene council; lessons from yesterday appear in today's prompts |
| **4 — Eval + power-ups** | Benchmark harness, `hermus eval compare`, orchestrator, fallbacks, project memory, dashboard panes, plan resume | `core/reasoning/eval.py`, `tests/eval/benchmark_tasks.json`, `core/counsel/router.py` | `hermus.py`, `core/tool_registry.py`, `core/memory.py`, `gateway/dashboard.html`, `scheduler/cron.py` | `eval compare --a single --b counsel` works; upgrades gated by scores; P1–P6 shipped |

**Suggested build order:** Phase 0 (foundation) → Phase 1 (counsel core, the thing you asked for) → Phase 2 (self-upgrade, the second thing you asked for) → Phase 3 → Phase 4. Phases 1–2 are independently shippable and demo-able.

---

## 8. Risks & trade-offs

| Risk | Mitigation |
|---|---|
| Council is slow/expensive for every task | Governor: difficulty 1–2 never convene; max members/rounds enforced; `/counsel off` |
| Small models produce weak debate (agreeing with each other) | Critic must attach concrete evidence; Judge is a different model; eval gate compares counsel vs single-agent |
| Self-upgrade goes wrong / loops | Config/prompt-only changes, versioned + atomic, eval gate, human veto on high-risk, rollback command, no upgrades mid-task |
| Token cost | Budget table, per-round caps, proposals in parallel, transcripts truncated for Meta review |
| Scope creep | Phased roadmap with acceptance criteria; each phase shippable alone |

---

## 9. Open questions for us to decide

1. **Build now or keep planning?** — I can start **Phase 0 + 1** (visible planning + a working council with Chair/Researcher/Critic/Synthesizer that votes on a plan) whenever you say go.
2. **Default behavior** — should the council auto-convene for every "hard" task, or only when asked (`/counsel run` / `hermus counsel run`)?
3. **Heterogeneous models** — do you have (or want to add) free Groq/HF keys so members use *different models*, or keep everyone on Ollama local (still works, just less diverse)?
4. **Self-upgrade trust level** — auto-apply low-risk amendments with eval gate, or require your approval for *everything*?
5. **Naming** — "Counsel System" good, or prefer Council/Cabinet/Roundtable/Think Tank?
6. **Members** — start with the default 5 (Chair, Researcher, Critic, Synthesizer, Judge) or fewer for the first version?

---

*This is a plan only — no code was changed. Next step: your answers to the open questions, especially #1, and we start building.*
