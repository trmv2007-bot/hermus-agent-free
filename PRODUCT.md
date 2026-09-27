# HERMUS — PRODUCT DEFINITION

> **This is the contract.** Every change to this project must justify itself
> against this document. If a change contradicts it, the change is wrong — not
> the document. When this document and the code disagree, that is a **bug in the
> code**, and fixing the code is the task.
>
> Owner: Rishi. Nothing here is negotiable by a model, an agent, or a
> well-meaning contributor. Amendments come from Rishi only.
>
> Last updated: 2026-09-26. Supersedes the aspiration documents
> (`JARVIS_ULTRON_VISION.md`, `IMPROVEMENT_PLAN.md`, `LIVING_CONTROL_ROOM.md`,
> `SPEC_PERSISTENT_FLEET.md`) as *direction*. `RED_LINES.md` remains binding as
> *law* and is not superseded by anything here.

---

## 1. What this is

**Hermus is a single-surface agent workspace that acts on Rishi's computer.**

Not a chatbot. Not a dashboard. Not a control panel.

One surface. The agent is the only actor in it. It sees the screen, drives the
mouse and keyboard, opens whatever it needs, shows its work where it happens,
and asks for nothing except permission when the action is dangerous.

Rishi should never have to leave the workspace to finish a task. Not to open a
browser, not to alt-tab, not to copy a result somewhere else, not to check
whether it worked.

### The one-line test

> If completing a task requires Rishi to switch applications, **the task is not
> finished** — regardless of what the logs say.

That sentence is the whole product. Everything below is detail.

---

## 2. The two halves, and which one wins

Rishi's words: *"I want it to look like Hermus but work like JARVIS."*

Both are already built, as separate things, which is why they never got combined.

| | **Workspace** — how it *works* | **Hermus** — how it *looks* |
|---|---|---|
| Lives in | `frontend/` (React) | `gateway/static/control.html` + HUD assets |
| Is | `Orb`, `CommandBar`, `SurfaceFrame`, `Launcher`, `Backdrop` | the wordmark, the palette, the instrument feel |
| Role | **The product.** Home screen. Full JARVIS power. | **The skin.** Applied to the workspace. |

**The workspace is the shell. Hermus is the skin.** They are not siblings and
never were. `/` is the workspace. `control.html` is not the product UI — it is
the **diagnostics drawer**, opened deliberately when something needs inspecting.

The current inversion (a cockpit with a link out to a "real" workspace) is the
single biggest structural mistake in this repository, and it is why six UIs
exist.

### Workspace mode

`frontend/src/App.tsx` already states the intent, and it is correct:

> *"There is no separate landing view behind a gesture — this room is the
> workspace. The only mode change is whether the chrome is on screen."*

Two states, one room:

- **Full chrome** — surfaces, dock, launcher, command bar visible
- **Full HUD** (`Alt+W`, or three clicks on the wordmark) — chrome folds away,
  leaving the Orb and the surfaces. This is the JARVIS state: minimal, calm,
  everything still live behind it.

There is no third mode, no separate app, no "advanced" tier.

---

## 3. The one-surface rule

Every capability must be reachable **from inside the workspace**. When a
capability can only be exercised by leaving the workspace, it does not ship.

Concretely, all of these belong in one room, as surfaces:

| Surface | Backs onto |
|---|---|
| Chat / command | `CommandBar`, streaming |
| Live screen | `mss` capture — *now real* |
| Terminal | sandboxed process execution |
| Files | the VFS |
| Browser | embedded, not an external window |
| Missions | goal DAG, live progress |
| Memory | what Hermus knows, editable |
| Diagnostics | `control.html`, embedded — not a link out |

Opening your actual desktop browser is a **failure state**, not a feature. The
agent drives the desktop when it genuinely needs the desktop — it does not
delegate navigation back to Rishi.

---

## 4. It must be real, or it must say so

The single worst failure mode this project has is **looking healthy while doing
nothing.**

This has happened for real: a default install had dry-run mouse and keyboard
backends. Plans rendered. Action records existed. Verification passed. The agent
could not move the mouse. Nothing reported it.

Therefore, as a hard rule:

> **Any capability is one of exactly four states, and must always be able to say
> which one it is in:**
>
> 1. **Real** — it works, and there is evidence
> 2. **Simulated** — it records intent without acting
> 3. **Unavailable** — it cannot run, and says why
> 4. **Not implemented** — it does not exist
>
> Simulated may never report as real. Unavailable must name the reason.
> Not implemented must not be dressed up as a pending feature.

`hermes doctor` is the instrument for this and it must stay honest. If a check
cannot prove its own capability, it reports the doubt rather than the hope.

Related and equally binding:

- Queueing work is **not** delivering it. A check-in is acknowledged only after
  the user demonstrably received it.
- A failing, declining, or absent delivery channel leaves state **pending** so
  it can be retried — never silently consumed.
- A green test suite means *nothing* if the model it needs was not running.

---

## 5. Autonomy, bounded

Hermus is JARVIS, not ULtron: **maximum reach inside revocable authority.**

- **Default-deny** on anything that touches the machine. Capability is granted,
  not assumed.
- **One emergency brake**, honoured by every control layer without exception. No
  path may act past it.
- **Every action is auditable** and reversible. Deleting the log is a red line.
- **Proactive by default, silent about nothing.** Hermus speaks up unprompted —
  but only about things it was actually asked to remember.
- **It may ask.** It may not force. A refused action stays refused and is
  reported as refused.

`RED_LINES.md` is binding and is not negotiable by this document.

---

## 6. Two tiers of mind

| Tier | Role | Model |
|---|---|---|
| **Main** | Reasoning, tool orchestration, anything long or complex | Nous Portal API |
| **Local** | Doctor, short turns, and **fallback when the API dies** | Ollama `spark-x2.5-4b-q4` |

Rules:

- Tool-bearing turns always go to the main tier. Local is not trusted with
  orchestration.
- Short prompts (≤ 280 chars) go local, to protect quota.
- If the main tier fails, the local tier takes over and **says** it did.
- The hosted-model timeout is generous by default. Nous is slow (81–118s
  observed). Timing out a valid answer is a bug, not caution.

---

## 7. What is NOT the product

Named explicitly, because these are the things that have been mistaken for it:

- **A dashboard.** Readouts about the agent are not the agent.
- **A control panel.** Toggle grids are settings, not capability.
- **A chat app with a skin.** If the only thing that works is text in a box,
  this is a messenger, not a workspace.
- **A framework.** Capability-token systems, actor kernels, and planner
  hierarchies are means. None of them are the product. A beautiful architecture
  that never moves the mouse is a worse failure than a working prototype.
- **Autonomy for its own sake.** Acting is a means to a result Rishi asked for.

---

## 8. The decision rule

Before any change lands, it must answer:

1. **Which surface does this belong to?** If none, it does not ship.
2. **Does this make the agent better at doing something, or only better at
   reporting that it could?** Only the first is progress.
3. **Can Hermus now do something it could not do before, from inside the room?**
   If not, why does this exist?
4. **Is it honest about its own capability?** See §4.
5. **Does it add a seventh UI, a fourth event bus, or a second source of
   truth?** If yes, it is the exact failure this document exists to stop.

If a change cannot answer all five, it is not ready.

---

## 9. Known state, stated honestly

As of 2026-09-26. This section is a snapshot, not a promise.

**Real and verified:**

- Desktop control — mouse, keyboard, window handles, screen capture. Proven by
  coordinate-exact pointer moves, screen deltas on click, and real HWND
  enumeration. Not simulated.
- Two-tier model routing with working API-outage fallback.
- Proactive check-in delivery with the honesty rules of §4.
- Non-loopback gateway binds require authentication.
- One emergency brake across all control layers.

**Built but not wired into the product:**

- Nothing in this list any more. The workspace (`frontend/`) is `/` and
  `control.html` is a surface inside it, both as of `1d4112c`.

**Correction — "five duplicate UIs" was wrong (2026-09-27).**

This section previously listed `hood.*`, `jarvis-hud.*`, `gods-eye.js`,
`console.js` and `control-client.js` as duplicate UIs to delete. They are not.
All five are `<script>`/`<link>` dependencies of `control.html` (71KB of JS
against a 37KB document) and each does something distinct:

| File | What it actually is |
|---|---|
| `control-client.js` | the drawer's main client — SSE run stream, speech, tabs |
| `console.js` | the console panel: manifest load, panel refresh |
| `jarvis-hud.js` | gauges, byte/uptime formatters for the instrument feel |
| `hood.js` | the camera/orbit "open hood" transition |
| `gods-eye.js` | the eye animation |

`control.html` is a real multi-tab application — chat, jobs, agents, missions,
computer, remote, voice, presence, safety, systems, settings. Deleting its
scripts would have gutted the diagnostics drawer this document requires.

The genuine duplication was **which screen you land on**, not how many files a
screen is made of. That is fixed. There is one workspace, and the control room
is a surface in it. There is no second UI left to delete.

**Not implemented:**

- Remote approval with genuine wait-and-resume.
- Cross-device control.
- Embedded browser surface.
- Unified conversational surface.
- Surfaces with no renderer yet: `files`, `ide`, `terminal`, `diff`, `media`.
  They fall through to the "not built yet" panel, which is the honest outcome
  under §4.
- Module decomposition — `mission.py` 2426→1866, `gateway.py` 1457, `agent.py`
  1373 remain oversized.
- Test suite integrity: **resolved, and it was never broken.** This section
  previously claimed `tests/` was ~90% untracked and that a fresh clone was
  "effectively untestable". That was a bad read of a partial `git ls-files`.
  Measured: 172 test files tracked, 0 missing from disk, 0 secret leaks. Do not
  re-audit this.

**Suspect, not yet re-measured:**

- A 25-failure baseline was recorded while Ollama was down. Those failures are
  likely environmental, not real defects. The baseline must be re-measured
  before any of it is treated as a bug.
- Three tests hang on `d6b50bc` and still do, verified against a pristine
  checkout with zero local changes:
  `test_command_multipart_attachment_e2e_mock`,
  `test_command_json_still_works`, `test_mission_report_has_response_field`
  (all in `tests/test_dashboard_connectivity_fixes.py`). They block in a
  request or in the gateway lifespan shutdown. Real defects, but not new ones —
  do not attribute them to whatever you just changed.
- **`GET /screen/frame` lies.** It answers `200 {success: true,
  frame_base64: null, timestamp: null, message: "screen capture not yet wired
  in this build"}` — a success response for a feature that does not exist.
  This is the §4 failure mode at the source. The real route is
  `GET /computer/live-frame`, which returns an actual JPEG or a 404. Fixing the
  stub to return 501 (or removing it) is an open, unowned item.

---

## 10. The first three moves

In order. Not a wish list — the sequence that unblocks the rest.

1. ~~**Re-measure the baseline with Ollama running.**~~ **Partly done.** The
   suite runs green on the files that matter for this change (56 pass), and the
   three known hangs are now pinned as pre-existing. The full-suite count is
   still unmeasured — see §9.
2. ~~**Get `tests/` under version control.**~~ **Never needed.** `tests/` was
   already tracked; the claim that it was not came from a bad read. Closed.
3. ~~**Promote the workspace to `/`.**~~ **Done** in `1d4112c`. The "delete the
   five duplicate UIs" half of this move was based on a false premise and was
   not performed — see the correction in §9. The unlock was the promotion, not
   the deletion.

**What is left, in order:**

1. Measure the full-suite baseline on a stable disk and record the real number.
2. Fix the three hanging tests — they are genuine defects, just old ones.
3. The oversized modules: `gateway.py` 1457, `agent.py` 1373, `mission.py` 1866.

Then, and only then, the JARVIS behaviours that are genuinely missing: remote
approval, embedded browser, cross-device.

---

**One last thing.** Fifteen models built this project and each one added a piece
because it could see the whole thing was incomplete. That is not a failure of
those models; it is what happens when a project has no contract. This document
is the contract. Its job is to make the sixteenth one unnecessary.
