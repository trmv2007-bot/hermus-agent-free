# Dashboard UX — Design Review Gap Register → UI Spec (D1–D14)

> Companion to `docs/design/ARCH_GAPS.md`. Each gap below is an **actionable
> UI spec** against the real frontend: `gateway/control.html` (1,294 lines,
> 11 tabs), `gateway/static/control-room.js` (1,177 lines),
> `gateway/static/control.css` (1,948 lines).
> All endpoints are from `SPEC_PERSISTENT_FLEET.md` **§9 Gateway API &
> Dashboard** (`gateway/routes_fleet.py`). This is a **migration of the
> existing control room**, not a greenfield widget — §9 says so explicitly.

## What already exists (and every gap below reuses)

- **11-tab ARIA tablist** (`control.html:340–350`): `overview, agents,
  presence, voice, jobs, missions, telemetry, computer, remote, safety,
  systems`; keyboard-navigable roving tabindex in
  `control-room.js:250–289` (`selectTab`, `initTabs`, `refreshTab`).
- **Error envelope plumbing**: `requestJSON`/`getJSON`
  (`control-room.js:79–124`) parse `gateway/envelope.py` envelopes
  (`{success, error, code, message, retryable}`, `x-request-id`);
  `reportFailure` (line 170) shows the server's own message with a Retry
  button when `retryable === true`; `RETRY_ACTIONS` registry (line 215).
- **Toasts**: `toast(message, kind, {retry, rid})` (line 127) into
  `#toasts` (`control.html:798`, `role="status" aria-live="polite"`),
  retryable TTL 12s / error 9s / info 4s.
- **`/readyz` pill**: `refreshReadiness` (line 44) drives `#conn`
  (`live` / `offline (preview)`) with envelope `reasons` surfaced in the
  pill title.
- **Safety tab** (`tab-safety`, `control.html:678`): pending approvals
  (`#pendingCount`), blocked missions (`#blockedMissionCount`), safety-core
  pill (`updateSafetyCore`, line 222) + `refreshSafety*` (lines 694–715).
- **Missions tab** (`tab-missions`, `control.html:609`): mission list with
  resume action (`refreshMissions`, line 490).
- **Emergency stop**: `#emergencyBtn` (`control.html:335`, `.danger`),
  global, in the header next to the pills.
- **State rendering**: `stateHtml` / `stateRowHtml` (lines 189–213) —
  inline panel states with error code tag, request-id, retry hook.

## DELETE list

- **Fake VRAM monitor** — `updateVRAMMonitor()` at `control.html:1204–1243`
  generates `Math.random() * 4 + 1` GB (line 1213) and mutates
  `#vramFill / #vramPercent / #vramUsed / #vramUsage` (markup at
  `control.html:334, 363–367`, stub state `vramUsage: 0` at line 840) and
  even drives `#jarvisOrb` working/error classes off the random number.
  **Delete the function, its interval, and the four DOM nodes.** Replace
  with a real GPU probe (SPEC §13) shown inside the **systems** tab —
  until the probe exists, show a designed empty state
  ("no GPU probe configured"), never simulated data. See D5/D8.

---
## P0 — attention, intervention, truth

### D1 — Intervention ladder (graduated, not just kill) — **P0**

**Gap.** Today the only fleet-level control is the global `#emergencyBtn`.
An operator cannot pause a runaway agent, cancel one bad task, or talk to
an agent mid-task without killing it.

**Migrate/extend.** The Agents tab roster (`tab-agents`,
`control.html:429`). Add an action column to each roster row; reuse the
existing `.danger`/`.ghost` button styles in `control.css` and the
`reportFailure`/toast pipeline for all failures.

**Interaction spec.**

1. **Pause / Resume** — `POST /api/fleet/agents/{id}/pause`, `POST
   /api/fleet/agents/{id}/resume`. One toggle button per row; label flips
   with the state pill. Optimistic pill change, rollback via
   `reportFailure` on envelope error.
2. **Cancel task** — `DELETE /api/fleet/agents/{id}/task`. Inline confirm
   (button turns into "confirm cancel?" for 3s, then reverts) — no modal
   for a recoverable action. Agent row keeps its identity; only the
   `current task` cell clears.
3. **Redirect (DM now vs queue)** — `POST /api/fleet/agents/{id}/task`
   (idempotency-keyed — client generates `Idempotency-Key`, see A4). A
   split input in the row detail drawer: **Send now** (interrupts current
   task; requires the same inline confirm as cancel) vs **Queue** (appends
   after current task; no confirm). Queue depth shown as a count chip.
4. **Safe-dismiss consequence dialog** — `DELETE /api/fleet/agents/{id}`
   (the only kill). Real modal (new component; none exists today — add a
   `.modal` pattern to `control.css`): server returns agent stats with the
   `GET /api/fleet/agents` row, dialog renders *"Friday has 3 days of
   memory, 41 tasks completed — destroy permanently?"* with a
   type-the-name confirm. Integrates with Emergency stop: while the
   emergency brake is ACTIVE (see `#emergencyState` read in
   `updateSafetyCore`), all ladder buttons except Resume are disabled with
   an explanatory tooltip.

### D2 — Approval-attention system — **P0**

**Gap.** When an agent is `blocked-awaiting-approval` nothing in the
current chrome escalates it; the Safety tab has counts but no per-agent
attribution, and no tab badge exists.

**Migrate/extend.** `updateSafetyCore` (`control-room.js:222`) already
computes "needs review" from `#pendingCount`/`#blockedMissionCount` —
extend it; add badge support to the tab buttons (`control.html:340–350`);
add a new inbox panel inside `tab-safety`.

**Interaction spec.**

1. **BLOCKED agent state pill** — roster pill gains
   `blocked-awaiting-approval` rendering (`pill warn` + pulsing border via
   a new `.pill.blocked` class). Full state set: idle / working / thinking
   / blocked-awaiting-approval / paused / sleeping / error (§9).
2. **Approval inbox panel** — new panel in `tab-safety` fed by
   `GET /api/fleet/approvals` (confirm-tier approvals: screen clicks,
   shell, …) plus HITL gates from `GET /api/fleet/missions`. Each row:
   agent, action preview, age, Approve / Edit (gates only) / Reject →
   `POST /api/fleet/approvals/{id} {action}` /
   `POST /api/fleet/missions/{id}/gate {action: approve|edit|reject, edits?}`.
   Errors through `reportFailure` with Retry (envelope-aware).
3. **Tab badges** — count chip on `tabbtn-agents` (blocked agents) and
   `tabbtn-safety` (pending approvals + blocked missions). Driven by
   `WS /ws/fleet` events, not polling. Badge also appears in the browser
   tab title (`(3) HERMUS`) when the window is unfocused — the system must
   be able to get the human's attention when blocked.

### D3 — Bus feed (live mission conversation) — **P0**

**Gap.** The legacy `agentSystem.messages` renderer in
`control.html:1181–1203` (`updateMessagesUI`) is a flat, unstyled,
filter-less dump of `sender -> target: content`. No threading, no
persistence across refresh, no verbosity control.

**Migrate/extend.** Replace `updateMessagesUI` + `#agentMessages` markup
entirely (absorbed by the fleet bus anyway); new "Bus" panel lives in
`tab-missions` (per-mission thread view) with a fleet-wide variant docked
in `tab-overview`.

**Interaction spec.**

1. **Filter bar** — by agent (multi-select chips from roster), by kind
   (message / task / result / approval / error), by mission. Filter state
   round-trips through D11 persistence.
2. **Threading** — events group under their mission/correlation root;
   collapsed threads show `+N` counts.
3. **Verbosity tiers** — three-way toggle: `summary` (one line/event,
   default), `detail` (payloads truncated), `full` (raw envelope JSON in a
   `<details>` block).
4. **Replay scrollback** — boot from `GET
   /api/fleet/bus?tail=N&after_seq=` (snapshot+cursor — A2/A3); live
   append via `WS /ws/fleet`. Refresh loses nothing: the client resumes
   from its last seen `seq`. "Scroll to live" FAB when the user scrolls
   up; auto-follow resumes on click.

### D4 — Error visualization (first-class, not toast-and-forget) — **P0**

**Gap.** `reportFailure` is excellent for request failures, but *domain*
errors (agent crashed, key auth_failed, mission failed, budget exhausted)
currently vanish into the same transient toast stream with no persistent
surface.

**Migrate/extend.** `stateHtml`/`stateRowHtml` (`control-room.js:189–213`)
are the pattern to generalize — inline error states with code tag,
request-id, and retry hook. Extend roster rows, the keys panel (D5), and
mission rows.

**Interaction spec.**

1. **Per-agent error pill + last error** — roster row carries a persistent
   `pill err` plus the last error message (one line, expandable) until
   acknowledged or cleared by a successful task.
2. **Per-key health** — keys panel (D5) renders `auth_failed` /
   `quarantined` **distinctly** from `cooldown` (different color + icon;
   quarantined keys need `POST /api/fleet/keys/{id}/re-enable` — manual
   only, button with inline confirm).
3. **Mission failure reason** — failed mission rows in `tab-missions`
   show the terminal reason inline (not behind a hover).
4. **Budget exhaustion** — budget-burn cell turns `pill err` at 100% and
   the affected agents/missions link to it. Source: `stats`/budget fields
   on `GET /api/fleet/agents` and `GET /api/fleet/missions`.

---

## P1 — depth and control

### D5 — Roster depth (key health, cost rollup, fleet strip, topology) — **P1**

**Migrate/extend.** `tab-agents` (`control.html:429`) gets a header strip
and richer rows; new Keys panel (sub-panel in `tab-agents`, or a 12th tab
if the tablist gets crowded — prefer sub-panel to preserve the 11-tab
keyboard nav model in `initTabs`).

**Interaction spec.**

1. **Fleet summary strip** — above the roster grid: live agent count,
   effective account-level RPM, today's spend, budget burn (four `kpi()`
   blocks — the existing `kpi(v,l)` helper at `control-room.js:243`).
   Source: rollup over `GET /api/fleet/agents` (`stats`, `key` fields).
2. **Roster grid** — avatar, name, model+provider, state pill (D2 set),
   current task, tokens/cost, last activity. Source: `GET
   /api/fleet/agents`.
3. **Key health panel** — `GET /api/fleet/keys` (open mode: full keys
   visible per operator decision §12); add-key form → `POST
   /api/fleet/keys {provider, name, key, base_url?, account_id?, rpm?,
   tpm?, rpd?}`; per-key RPM/TPM/RPD limits rendered as meter bars;
   re-enable action per D4.2.
4. **Topology view** — toggle on the roster: graph of who is DMing whom
   during the active mission, edges animated by `WS /ws/fleet` message
   events. Canvas or SVG; keep it dependency-free (consistent with the
   no-build-step frontend).

### D6 — Edit live agents + spawn guardrails — **P1**

**Migrate/extend.** Row detail drawer in `tab-agents` (new component),
plus a Spawn dialog reusing the D1 modal pattern.

**Interaction spec.**

1. **Edit live agent** — drawer form over `PATCH
   /api/fleet/agents/{id}`: persona, model, key, skills. Model dropdown is
   Vault-discovered (per provider + key's available models). Dirty-field
   diff summary before save; `409` handling per D12.
2. **Spawn dialog** — `POST /api/fleet/agents {name, persona, provider,
   model, key_name?}`. Stepper: provider → model (filtered to tool-capable
   models — **guardrail**: spawn button disabled with reason when the
   selected model can't call tools) → account/key pool (from
   `/api/fleet/keys`) → persona preset (from `multi_ai.PERSONA_PRESETS`,
   rendered as cards). **Guardrails:** name uniqueness checked against the
   loaded roster client-side *and* enforced server-side; duplicate →
   inline error, not a toast.

### D7 — Screen panel (approval queue, control lock, privacy masking) — **P1**

**Migrate/extend.** `tab-computer` (`control.html:638`) — this is where
the screen-seeing UI belongs; absorb the existing
`core/computer/remote.py` mobile page into `tab-remote` (see D9) rather
than duplicating.

**Interaction spec.**

1. **Live mirror** — `WS /ws/fleet/screen` low-fps stream into a
   `<canvas>`; grounding boxes from `GET /api/fleet/screen/frame` overlaid
   as absolutely-positioned outlines (label + confidence).
2. **Approval queue for clicks** — pending click approvals (confirm-tier,
   same `/api/fleet/approvals` feed as D2) rendered *on the frame*: click
   a highlighted target to approve or correct the agent's next click
   (corrected coordinates posted back with the approval).
3. **Control lock** — toggle "observer / operator": observer hides all
   approve/correct affordances; default is observer.
4. **Privacy masking toggle** — sensitive windows (title-match list,
   server-provided) render as solid blocks with a lock glyph; toggle
   requires the control lock and is itself a confirm-tier action.

### D8 — Onboarding wizard + empty states — **P1**

**Migrate/extend.** `tab-overview` (`control.html:355`) hosts the
first-run wizard; every panel gets a designed empty state in place of the
current ad-hoc `'No agent messages yet'` strings (e.g.
`control.html:1188`).

**Interaction spec.**

1. **First-run wizard** (shown when `GET /api/fleet/agents` returns empty
   AND `GET /api/fleet/keys` returns empty): 4 steps — add key (D5.3 form)
   → detect free tiers (server probes RPM/RPD) → spawn demo team (D6
   dialog pre-filled) → run demo orchestration (`POST
   /api/fleet/orchestrate {goal, agents: "all"}`). Dismissible;
   re-openable from overview. Wizard steps reuse the D6 modal/stepper
   styles.
2. **Empty states** — every list panel (roster, missions, approvals, bus,
   keys, jobs) gets: icon, one-line explanation, and a primary action
   button that starts the obvious next step (e.g. empty approvals →
   "Nothing needs your attention ✓" with no action; empty roster → "Spawn
   your first agent"). Replaces the VRAM area deleted from overview: show
   "no GPU probe configured" until SPEC §13 lands the real probe.

---

## P2 — reach and resilience

### D9 — Mobile: approval-first layout + touch-safe grounding — **P2**

**Migrate/extend.** Media queries in `control.css` (add a mobile section,
don't fork the file); absorb `core/computer/remote.py`'s mobile page as
the fleet remote in `tab-remote` (`control.html:662`).

**Interaction spec.**

1. **Approval-first layout** — below 720px: the Safety approval inbox
   (D2) and `#emergencyBtn` are reachable **without tab-scrolling**: the
   tab bar collapses to a bottom sheet, and pending approvals pin to the
   top of whichever tab is open (sticky banner with count, tap → jump to
   inbox).
2. **Touch-safe grounding** — screen-panel grounding boxes get ≥44px hit
   targets and pinch-zoom on the canvas; approve/correct (D7.2) works
   one-handed.

### D10 — Notifications + notification center — **P2**

**Migrate/extend.** New drawer component (right-edge slide-in) triggered
from a bell icon in the header pill row (`control.html:334` area); toasts
(`control-room.js:127`) stay for transient action feedback and do **not**
duplicate notifications.

**Interaction spec.**

1. **Browser Notification API** — per-type opt-in toggles (Settings
   section in `tab-systems`): task done / approval needed / agent error /
   mission done. "Approval needed" defaults ON after permission grant.
2. **Notification center drawer** — history replayed from the bus (`GET
   /api/fleet/bus?tail=N` filtered to notify-worthy kinds); unread count
   badge on the bell; items deep-link (D11) to the relevant tab + entity.

### D11 — UI state persistence + deep links — **P2**

**Migrate/extend.** `selectTab`/`initTabs` (`control-room.js:250–289`) and
the refresh dispatch in `refreshTab` (line 289).

**Interaction spec.**

1. **Deep links** — `/control#agents`, `/control#missions`, etc.: on
   load, `initTabs` reads `location.hash`; `selectTab` writes it
   (`history.replaceState`, no scroll jump). Entity-level:
   `#agents/{id}`, `#missions/{id}` open the row detail drawer / mission
   thread.
2. **State persistence** — `localStorage` (keyed `hermus.ui.v1`): active
   tab, bus filters + verbosity tier (D3), roster scroll position,
   selected agent, drawer open/closed. Restored on load *before* first
   paint to avoid flash.

### D12 — Multi-client conflict toasts — **P2**

**Migrate/extend.** `requestJSON` (`control-room.js:79`) and
`reportFailure` (line 170): add a `409` branch.

**Interaction spec.**

1. **Optimistic locking over bus `seq`** — all mutating `/api/fleet/*`
   calls include the client's known `state_seq`; server rejects stale
   writers with `409 {current_seq}`. Client re-reads
   (`GET /api/fleet/agents` or the affected resource) and retries once
   automatically.
2. **Conflict toasts** — when a `WS /ws/fleet` event mutates an entity
   the local operator has open in a drawer/dialog (e.g. editing an agent
   that another session dismisses): persistent toast ("Friday was
   dismissed by another session") with a **Reload state** action; the
   drawer closes rather than editing a ghost.

### D13 — WS reconnect + lag indicator — **P2**

**Migrate/extend.** The `#conn` readiness pill pattern
(`healthy()`/`down()`, `control-room.js:34–35`) and `refreshReadiness`
(line 44) — extend the same pill to cover the fleet socket, reaching
parity with the existing SSE stream behavior.

**Interaction spec.**

1. **Reconnect** — `WS /ws/fleet` reconnects with exponential backoff
   (1s→30s cap, jitter); on reconnect, resume from last `seq` via
   `GET /api/fleet/bus?after_seq=` so no events are lost (D3.4).
2. **Lag indicator** — pill shows `live` / `reconnecting (3s)` /
   `lagging ~12s` (computed from newest event timestamp vs now); `pill
   warn` while lagging, `pill err` while disconnected. Events that arrive
   after a gap flash a "caught up: N events" notice in the bus feed.

---

## Gap → component → endpoint matrix

| # | Gap | Pri | Migrates/extends | Endpoints (SPEC §9) |
|---|-----|-----|------------------|---------------------|
| D1 | Intervention ladder | P0 | Agents roster rows + new modal; `#emergencyBtn` state | `POST /agents/{id}/pause`, `/resume`, `/task`; `DELETE /agents/{id}/task`, `DELETE /agents/{id}` |
| D2 | Approval attention | P0 | `updateSafetyCore`, tab buttons, `tab-safety` | `GET/POST /api/fleet/approvals[/{id}]`, `GET /api/fleet/missions`, `POST /missions/{id}/gate`, `WS /ws/fleet` |
| D3 | Bus feed | P0 | Replaces `updateMessagesUI` (`control.html:1181`) | `GET /api/fleet/bus?tail=&after_seq=`, `WS /ws/fleet` |
| D4 | Error visualization | P0 | `stateHtml`/`stateRowHtml`, roster, missions, keys | `GET /api/fleet/agents`, `/missions`, `/keys`, `POST /keys/{id}/re-enable` |
| D5 | Roster depth | P1 | `tab-agents` + new keys sub-panel | `GET/POST /api/fleet/keys`, `GET /api/fleet/agents`, `WS /ws/fleet` |
| D6 | Edit + spawn guardrails | P1 | Row drawer + spawn modal | `PATCH /api/fleet/agents/{id}`, `POST /api/fleet/agents`, `GET /api/fleet/keys` |
| D7 | Screen panel | P1 | `tab-computer` | `GET /api/fleet/screen/frame`, `WS /ws/fleet/screen`, `/api/fleet/approvals` |
| D8 | Onboarding + empty states | P1 | `tab-overview` wizard; all list panels | `GET /api/fleet/agents`, `/keys`, `POST /api/fleet/orchestrate` |
| D9 | Mobile approval-first | P2 | `control.css` media queries; `tab-remote` absorbs `core/computer/remote.py` | same as D2/D7 |
| D10 | Notifications | P2 | New drawer + bell; `tab-systems` settings | `GET /api/fleet/bus`, `WS /ws/fleet` |
| D11 | Persistence + deep links | P2 | `selectTab`/`initTabs`/`refreshTab` | — (client-side; entity links target D1/D3 surfaces) |
| D12 | Conflict toasts | P2 | `requestJSON`/`reportFailure` `409` branch | all mutating `/api/fleet/*` (state_seq optimistic locking) |
| D13 | WS reconnect/lag | P2 | `#conn` pill + `refreshReadiness` | `WS /ws/fleet`, `GET /api/fleet/bus?after_seq=` |

**Cross-cutting delete:** fake VRAM monitor (`control.html:1204–1243`,
markup `:334, :363–367`, stub `:840`) — remove before any D-numbered work
ships; the overview panel must not render simulated telemetry.

*13 numbered gaps + the VRAM deletion = the 14 dashboard/UX gaps from the
design review. 3 priority tiers. Update this file when a gap closes —
same convention as ARCH_GAPS.md.*

