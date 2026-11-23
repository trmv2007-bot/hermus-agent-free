# HERMUS v2 — the target experience, and the distance to it

Written 2026-11-23 for HRS-6. Everything here is measured against the code and a
running instance, not against the README. Where a claim could not be verified,
it says so.

**How this was produced.** A fresh worktree of `trmv2007-bot/hermus-agent-free`
@ `3046846`, a project-local venv, the gateway on port **8123** with
`HERMUS_HOME` and `HERMUS_DATA_DIR` pointed at a scratch folder so the person's
`~/.hermus` (585 MB, with real credentials) and `.env` were never touched. The
Control Room was driven by Playwright at **1440×900** and **420×900**, every
surface screenshotted. With no local model installed (no Ollama on this PC,
installers do not install one), the model path was exercised with
`HERMUS_MODEL=mock/mock` and the ambient provider key cleared, so nothing spent
money and nothing reached a paid API.

Screenshots: `renders/HRS-6/default-1440-*.png`, `default-420-*.png`,
`after-1440-*.png`, `after-420-*.png` (before/after the five quick wins).
Raw measurements: `default-1440-audit.json`, `default-420-audit.json`, and the
`after-*` equivalents.

---

## 1. The target: the first hour, minute by minute

This is the experience the asks add up to. Not a feature list — an hour.

**0:00 — Install.** One command. It finds Python, installs into a project-local
venv, and at the end tells the person, in one line, what HERMUS can already do
and what it cannot: "Local model: none found. I can still answer, but with the
offline stub. Install Ollama (free) for real answers — here is the one command."
No fake green ticks.

**0:02 — It opens itself.** Not a browser tab the person has to find: a window
that looks like a room, dark, one accent, the fairy idle in the middle, a single
input line under it. Eleven tabs of debug panels exist, but they are not what a
person sees first; the first screen is the companion, the diagnostics live behind
one quiet control.

**0:03 — First words.** The person types "what can you do?" or presses the mic
and says it. Within two seconds the answer starts streaming, in the conversation,
in the same square they typed in. If no model is reachable, the answer says
exactly that, once, with the one command that fixes it — never a silent spinner
and never a mock paragraph dressed as an answer.

**0:05 — It knows the machine.** Without being asked, the fairy can say what is
running, which window has focus, what was copied a minute ago, how much disk is
left. It reads; it does not act. "You have 19 GB free and a 4.2 GB model download
queued" is a sentence it can produce from evidence, not a guess.

**0:08 — It does a thing.** "Rename the screenshots on my Desktop by date." It
shows the exact plan — 14 files, old name → new name — and waits. One keystroke
approves; it does it; it says where the undo record is. If the person presses
undo, the files go back. Nothing on the machine is touched without that preview.

**0:12 — It watches, quietly.** A build finishes, a download completes, a meeting
starts in five minutes: these arrive as one line in a margin, or a single soft
pulse from the character — never a modal, never a focus steal, never a banner
over the work in progress. A day's worth of them batch into one digest at a
moment the person is already looking.

**0:20 — It points.** The person says "where do I change the audio device?" and
the character does not open a settings page for them: it moves to the corner of
the screen, points at the speaker icon, and says what to click. This is the part
that makes it a companion instead of a chat window, and it is the part that does
not exist yet.

**0:30 — It remembers.** The next morning the person asks "the thing from
yesterday" and it knows which thing. The identity, the goals and the small
moments are durable; the conversation is not the memory.

**0:45 — It is still free.** No card, no trial clock, no key it must buy. Local
model, free tier, or the honest stub — in that order, always.

---

## 2. Where it actually stands, ask by ask

Every ask from the brief, with what exists, where, whether it runs, and the
evidence.

### 2.1 Jarvis feel — **exists, visually convincing, behaviourally hollow**

The Holo Core shell is real and running: `gateway/control.html` (888 lines) +
`gateway/static/nexus.css` (1,859) + `gateway/static/nexus.js` (1,209), a fairy
SVG with moods (`gateway/static/hud.js`), a canvas core, a command palette that
works (verified: `Ctrl+P` opens 10 real destinations), eleven ARIA tabs with
roving tabindex. It *looks* like the thing.

What it does not do is behave like one. The fairy's lines are hard-coded quips,
not observations — including `hud.js:148` `"Boot complete. Every system checked
— twice."`, printed on load whether or not anything was checked. Presence is a
JSON file, not a felt continuity. Jarvis feel is 80 % surface and 20 % behaviour;
the target hour above is the reverse.

### 2.2 Clean design — **fails the crew's own bar**

Measured on the default landing screen: 11 top-level tabs, a 10-button dock, 58
console panels behind the Systems tab, a telemetry feed, an activity feed, four
KPI rows, a model fleet table and a personal-space card. `docs/ARCHITECTURE_INVENTORY.md`
records the intent — "58 panels / 54 actions / 7 groups". That is a debug room,
not a companion, and it violates CREW.md rule 4 ("Jarvis, not a dashboard of
debug panels") on the very first screen a person sees.

Typography and spacing are genuinely good (hairline borders, one cyan accent,
monospace numerals), so this is a hierarchy problem, not a taste problem: the
diagnostics outrank the conversation.

### 2.3 Smooth — **the worst defect in the product, measured**

- Frame cadence on the landing screen at 1440×900: **54 frames in 4 s → ~13 fps,
  worst frame 152 ms** (rAF sampling, headless Chromium, this PC).
- With the Control Room open, **19–25 of ~75 page requests never complete**;
  Chrome holds exactly **6 sockets** to the gateway permanently
  (`Get-NetTCPConnection -RemotePort 8123` → 6, constant).
- A `POST /jobs` issued from inside the page measured **12.2 s**; the same call
  from curl against the same server took **24 ms** (server log: `POST /jobs ->
  200 in 24.3ms`). A page `GET /readyz` measured **22 s** while curl measured
  **4 ms**.
- Cause, in one sentence: the page runs 14 `setInterval` polls across six
  scripts (`control-room.js:1444`, `control-core.js:502-503`,
  `control-agents.js:381-384`, `nexus.js:479,861-862,1208`,
  `personal-space.js:123`, `hud.js:196`), several endpoints are slow on their
  first call (`/api/v1/system/health` 640–1,118 ms; `/doctor/status` **14.4 s**;
  `/engine/status` **8.5 s**; the very first page load serialises 20 requests at
  13–27 s each), and Chrome's six-per-host cap then starves the connection the
  answer needs.
- Consequence a person feels: **type a question, get nothing.** The job does run
  — `job_6682cf8222` succeeded in 13.8 s with a real result string recorded by
  the server — and the conversation stays empty, forever
  (`renders/HRS-6/mock-turn-final.png`).

Root cause is not proven to the last step (a CDP network trace is the next
experiment); the mechanism and the numbers are.

### 2.4 Free only — **the design holds; the first run does not**

`core/config.py:95` defaults to `ollama/llama3.1:8b`; the mock fallback and
`core/free_keys.py` exist; optional keys are optional. Nothing requires a paid
service. But **no installer mentions Ollama** (`grep -n ollama install.ps1
setup.cmd bootstrap.py` → nothing), so a fresh install has no model at all and
the first answer is the mock string
(`[MOCK mock] Echo: … Install Ollama or add any API key`). Free-only is
architecturally true and experientially broken: the free path is never set up.

### 2.5 Companion, not tool — **modules exist, the loop is missing**

`core/presence.py` (796 lines: identity, goals, moments, heartbeat),
`core/presence_kernel.py`, `core/personal_space.py` (576), `core/personal_os.py`,
`core/personal_context.py`. The Presence tab renders them: identity is editable,
`/presence` answers in 26 ms, the heartbeat ticks, 4,588 characters of real
content on that tab. This is the most complete of the "companion" asks.

What is missing is any moment where the companion *initiates* something the
person did not ask for and is glad about — see 2.10.

### 2.6 Full PC awareness — **narrow**

`core/perception.py` (60 lines) refreshes a `WorldModel` from
`core/connectors/`: `filesystem` (lists one workspace folder),
`runtime` (CPU/RAM via `core/computer/resources.py`), `services` (which env keys
are configured). That is the whole awareness layer: **423 lines of connectors**,
no focused window, no clipboard, no process list, no screen content.

Screen capture exists separately (`gateway/routes_screen`, `frame_sampler.py`,
`/screen/status` answers `running:false, fps:10, buffer 128 MB`) and is opt-in
per session. Nothing joins it to perception, so the agent cannot answer "what am
I looking at?" without being asked to start a capture.

### 2.7 Interact with anything — **largely real**

203 tools registered (`/tools`), a single `ToolGateway` boundary
(`core/tool_registry`), shell, files, browser automation (Playwright), local
defense, web acquisition, MCP client. Verified live: `/tools` → 203.
This ask is satisfied for *scripted* interaction and under-served for
*interaction with what is on screen* (see 2.11).

### 2.8 Advanced thinking — **built, unexercised without a model**

`core/reasoning/` (governor, strategies, scaffold, lessons, eval), `core/critic.py`,
`core/counsel/` (council with a constitution and 1 review logged), `core/long_horizon.py`,
`core/agent_dag.py`, `core/delegation.py`. All present, tests cover them
(`tests/test_capability_flows.py` etc.). None of it can be honestly evaluated on
this machine because there is no model to think with — which is itself the
finding: the thinking stack has never been measured against a real model in this
repo's own harness on Windows.

### 2.9 Never damages — **the strongest subsystem in the repo**

11 red lines read live from `/red-lines/policy`; `core/permissions.py` approvals
and scoped grants; `core/emergency_stop.py` with a UI brake; `core/sandbox.py`;
audit logs; checkpoints/backups/rollback (`/reliability/*`, `/rollback/*`).
`/reliability/status` → `healthy`. The design is preview → approve → act →
record, and the code enforces it in the tool path, not just the UI. Keep this;
do not let wave 2 route around it.

### 2.10 Proactive but unobtrusive — **machinery yes, judgement no**

`core/proactive.py` (rules, cooldowns, opt-in), `core/sentinel.py` (starts by
default — the log shows `[Sentinel] alert core running`), presence check-ins
**off by default** (`config.py:265`). The Attention card does surface one real
item ("Your environment changed"). What does not exist: a digest, quiet hours, or
any signal that suppresses a proactive surface while a person is typing, in a
call, or fullscreen. The rule CREW.md states ("never interrupt") is a policy
without an implementation behind it.

### 2.11 Full computer use — **code complete, host-dependent, unverified end to end**

`core/computer/` is 36 modules: `controller.py` (the single gated action path),
`keyboard.py`/`mouse.py` (pyautogui backends with a dry-run fallback),
`window_manager.py`, `state_machine.py`, `repair.py`, `verifier.py`,
`grounding.py`, `recorder.py`, `video_writer.py`. Live health after installing
the optional deps: `computer.mouse capable`, `computer.window capable`. So the
capability is honest about its dependencies and works when they are present.

Not verified: an actual click on this person's desktop. I did not move their
cursor or capture their screen — that is their machine, and the brief said do not
disturb them. The verification path exists (`tests/test_computer_agent_v1.py`,
`v2`, `test_computer_control_honesty.py`).

### 2.12 A floating character that points at the screen — **does not exist**

`grep -rn "topmost|SetWindowPos|WS_EX_LAYERED|always_on_top|frameless|tkinter|PyQt|pystray|electron|tauri|pywebview|tray"` over the whole tree returns **nothing** in
Python/HTML/JS. There is no desktop shell, no tray icon, no transparent overlay,
no always-on-top window. The fairy lives only inside the dashboard's
`#fairyStage` (`gateway/static/hud.js`), a 120×120 SVG in a web page.

This is the largest gap between what the person asked for and what exists: the
product's most distinctive promise — a character that lives on the screen and
points at things — is currently a decoration in a browser tab. Everything else in
the asks presumes it.

---

## 3. Dead code and false claims

Named so wave 2 does not build on them.

**Dead or unreachable in production**

| Thing | Where | Why |
|---|---|---|
| `VoiceExecutiveBridge` | `core/voice/executive_bridge.py` | only constructed by `tests/test_voice_executive_bridge.py`; no production caller |
| `VerifiedComputerExecutor` | `core/computer/execution_loop.py` | only imported by its test |
| example plugin | `core/plugins/example_plugin.py` | only referenced by a test fixture |
| Android companion | `android_companion/` | native app, never built here; `core/android/transport.py` degrades to `AndroidUnavailable` |
| Avatar pipeline | `core/avatar.py` (416 lines) | needs a local HeyGem service (`heygem_tts_url`), absent → permanent `unavailable` |
| Distributed fabric | `core/distributed*.py`, `/distributed/*` | single node; every route answers from one process |

**Claims the code does not back**

| Claim | Source | Reality |
|---|---|---|
| "Verified computer-control execution ✅ Complete" | `README.md` build table | accurate for the code, unverified on a real desktop here; depends on optional `pyautogui` |
| "Multimodal intelligence ✅ Complete" | `README.md` | `/multimodal/status` → `{"facts":[],"recent_events":[]}`; it is a facade over document/image tools |
| "Distributed HERMUS ✅ Complete" | `README.md`, `docs/PHASE_17_*` | one process, no peer transport exercised |
| "Boot complete. Every system checked — twice." | `gateway/static/hud.js:148` | nothing is checked; it is a caption |
| `rtt` in the header | was `control-room.js` health duration | printed a 27,630 ms "round trip" on a loopback socket (fixed in this PR) |
| "Every system checked" panel counts | `docs/ARCHITECTURE_INVENTORY.md` "58/58 verified" | the manifest is verified against routes, not against behaviour; 0 of 58 panels were probed in a fresh session (`0 PROBED`) |
| Mock mode | `gateway/static/nexus-mock.js` | fabricates every surface (`/api/v1/system/health` → all green) behind `?mock=1`; labelled on screen, but an easy way to demo a product that is not running |

---

## 4. The gap list, ranked by how much it changes that first hour

Each item is shaped to be handed to one thread: owner, files, change, proof.

### G1. Make the first minute work: one poller, one state endpoint, no starvation
**Changes the hour:** everything. Without it the first message gets no answer.
**Files:** `gateway/static/control-room.js` (14 intervals, `refreshOverview`,
`refreshTab`), `gateway/static/control-core.js:502-503`,
`gateway/static/control-agents.js:381-384`, `gateway/static/nexus.js:479,861-862,1208`,
`gateway/static/personal-space.js:123`, `gateway/static/hud.js:196`;
server side `gateway/routes_canonical.py` (`/api/v1/system/health`),
`core/doctor.py`, `gateway/routes_engine.py`.
**Change:** one scheduler with per-endpoint backoff, pause when
`document.hidden`, and a single `/api/v1/console/state` bundle so a page load
costs one request instead of twenty. Move `/doctor/status` (14.4 s) and
`/engine/status` (8.5 s) off the boot path entirely — they are diagnostics, not
state. Then re-measure: target < 300 ms cold for every first-screen request, and
zero pending requests after 10 s idle.
**Proof:** CDP network trace showing 0 pending requests; the numbers above as
the baseline; a Playwright test that types a command and asserts the answer text
appears within 5 s.

### G2. Give the product a body: a floating companion window
**Changes the hour:** it is the ask nobody has built.
**Files:** new `desktop/` package (not `core/`), `pyproject.toml` packages list,
one `include_router` line in `gateway/gateway.py`; reuse `core/presence.py` for
state and `gateway/static/hud.js` for the character art.
**Change:** a transparent, always-on-top, click-through-except-the-body window
(Windows first: a tiny Win32/Tk host, or `pywebview`), 240×240, that renders the
existing fairy SVG against `presence` state, sits in a screen corner, and can
*point*: given a target rect from `core/computer/grounding.py`, draw a ring/arrow
at that screen coordinate for N seconds. It must never take focus, never appear
over fullscreen apps, and be off by default until the person turns it on.
**Proof:** screenshot of the character over a real window, plus a test that the
"point at rect (x,y,w,h)" call produces a visible ring at those coordinates and
does not change the foreground window.

### G3. Make the first answer come from something free that is actually installed
**Changes the hour:** the difference between "it answered me" and "it echoed a
stub".
**Files:** `install.ps1`, `setup.cmd`, `bootstrap.py` (the optional-module report
already exists), `core/local_first.py`, `core/nollama.py`, `core/config.py:95`.
**Change:** the installer detects Ollama/LM Studio; if absent it offers (one
prompt, default yes) to install Ollama and pull the smallest usable model, or
falls back to the free-tier key flow. First run then says "answering with
llama3.2:1b on your machine" instead of echoing a mock.
**Proof:** a clean-machine dry run transcript, and `hermus doctor` reporting the
resolved model with its source.

### G4. Say what it knows: a real PC-awareness layer
**Changes the hour:** "it knows my machine" is the second thing a person tests.
**Files:** `core/connectors/` (add `desktop.py`), `core/perception.py`,
`core/computer/window_manager.py` (already enumerates windows), clipboard via a
small guarded reader.
**Change:** add read-only connectors for focused window, visible windows, battery
/AC, idle time, and clipboard *hash + kind* (never contents without consent).
Keep them read-only; they feed `WorldModel` only.
**Proof:** `perception.refresh()` returns facts for the four new connectors on
Windows, with a test that a denied connector degrades to `unavailable` instead of
raising.

### G5. One screen for a person, diagnostics behind a door
**Changes the hour:** the first impression is a debug console today.
**Files:** `gateway/control.html`, `gateway/static/nexus.css`,
`gateway/static/control-room.js` (`selectTab`, dock).
**Change:** default route shows three things — the conversation, the character,
one input. The eleven tabs collapse into one "Systems" entry that opens the
existing tabrail; the dock disappears; the palette keeps every destination, so
nothing is lost, it is just not shouted.
**Proof:** 1440 and 420 screenshots of the new first screen; every existing
destination still reachable in ≤ 2 actions (palette test already exists).

### G6. Finish proactive: quiet hours, focus guard, digest
**Changes the hour:** stops the product from becoming the thing people mute.
**Files:** `core/proactive.py`, `core/presence.py` (check-in suggestions),
`gateway/routes_presence.py`, `core/sentinel.py`, new `core/attention.py`.
**Change:** a single `AttentionGate` every proactive surface must pass:
suppress while typing (keystroke within 3 s), during fullscreen, during a call
(mic in use), and outside quiet hours; batch everything suppressed into one
digest shown when the person next returns to the window.
**Proof:** unit tests for each suppression rule and one for batching; a demo
where 5 events during a "call" produce exactly one digest afterwards.

### G7. Make the conversation survive the truth: transcript owns the answer
**Changes the hour:** complements G1 — even when the stream fails, the person
sees what happened.
**Files:** `gateway/static/nexus.js` (`connectRun`, `handleRunEvent`),
`gateway/realtime.py` (`_stream_run`), `core/run_events.py` (`subscribe`).
**Change:** on `run_finished` without an `agent_response`, fetch
`/jobs/{id}/result` and render the recorded answer (the server always has it);
on a stream that never delivers a first event, say so and link the job (shipped
in this PR as the watchdog); and make `RunBus.subscribe` replay the durable log
for runs that started before a gateway restart instead of synthesising an empty
run.
**Proof:** a test that kills the stream mid-run and finds the answer in the
transcript; a test that restarts the bus and replays a finished run.

### G8. Voice that works the first time
**Changes the hour:** the microphone button is the most tempting control on screen.
**Files:** `gateway/routes_voice.py`, `gateway/routes_speech.py`,
`core/speech.py`, `gateway/static/control-client.js`.
**Change:** today `speech.tts` is `unavailable` without `pyttsx3` and the mic
button silently does nothing useful. Make the button state truthful before the
click (disabled with the reason, or browser-speech fallback), make STT degrade to
browser transcription, and keep the sub-second ack.
**Proof:** with `pyttsx3` absent, pressing the mic gives an explicit message
naming the missing piece; with it present, the ack plays.

### G9. Windows test harness that can actually run
**Changes the hour:** none directly — it changes whether anyone can prove the rest.
**Files:** `tests/_control_room_source.py`, `tests/test_control_room_ux.py`,
`tests/test_command_answer_loop.py` (shared `node -e` pattern).
**Change:** write the harness to a temp file and run `node <file>`, exactly as
`tests/test_hrs6_quick_wins.py` does; today 13 of those tests fail on Windows
with `WinError 206` (command line over 32,767 characters) before they test
anything.
**Proof:** the same 13 tests passing on this PC.

### G10. Honest onboarding, no fake boots
**Changes the hour:** trust.
**Files:** `gateway/static/hud.js:148`, `gateway/static/nexus-mock.js`,
`README.md` build table, `docs/ARCHITECTURE_INVENTORY.md`.
**Change:** delete the "every system checked — twice" line or replace it with
the real result of the readiness probe; keep mock mode but require
`HERMUS_MOCK_UI=1` server-side as well, so it cannot be opened on a live install
by URL alone; make the README table state per-row evidence (`tests/...`) or drop
the row.
**Proof:** grep-level test that no static string claims a check the code does not
run.

---

## 5. Delete this

- `core/voice/executive_bridge.py` — no production caller; its one test can move
  to the router that actually speaks.
- `core/computer/execution_loop.py` — superseded by `controller.py` +
  `state_machine.py`; only a test imports it.
- `gateway/static/nexus-mock.js` — or, if it stays as a design tool, move it out
  of `gateway/static/` into `docs/assets/` so it is never served by a real
  install.
- `gateway/static/hud.js:148` boot quip.
- The `README.md` build-status table's ✅ column until each row names its test.
- `docs/PHASE_9..18_*.md` as *evidence*: keep as history, but stop treating them
  as status. `docs/ARCHITECTURE_INVENTORY.md` (rewritten in this PR) is the truth
  file now.

---

## 6. What shipped in this PR (five quick wins)

| Fix | File | Effect |
|---|---|---|
| Panels stop claiming to be hidden | `gateway/static/control-room.js` `selectTab` | the DOM, screen readers and tests now agree on which panel is showing |
| A request that never settles fails honestly | `control-room.js` `requestJSON` | 30 s bound → retryable `client_timeout` envelope instead of "loading…" forever |
| A silent run stream says where the answer is | `gateway/static/nexus.js` `connectRun` | 20 s watchdog: stops the core claiming WORKING and points at Jobs |
| Phone-width tables wrap and scroll | `gateway/static/nexus.css` (≤760 px) | measured at 420 px: Approvals went from 12 elements past the viewport to **0**, jobs stays at 0, and the jobs table measures 306 px inside a 340 px card |
| The latency readout tells the truth | `control-room.js` `refreshReadiness` | it is a `/readyz` round trip now, not the 27 s health probe |

Tests: `tests/test_hrs6_quick_wins.py` (6 tests, behaviour-level, Node harness
written to a temp file so it runs on Windows too).

---

## 7. Could not verify

- Any real model answer. No Ollama on this PC and the only credential present was
  the ambient environment's, which this thread must not spend.
- A real desktop action (mouse/keyboard/screenshot on the person's screen). Their
  machine, and the brief said do not disturb.
- Voice, TTS and microphone capture (no `pyttsx3`; no audio device claimed).
- Android bridge, distributed fabric, avatar pipeline — all need hardware or
  services that are not on this PC.
- The final root cause of the browser stall in G1 past "six sockets held,
  requests unsent for 12–26 s, server answering in 8 ms". A CDP trace is the next
  step, and it is the first thing wave 2 should do.
