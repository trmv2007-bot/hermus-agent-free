# HERMUS Living Agent Control Room

The gateway control room at `http://localhost:8000/control` is a local-first,
task-focused operations console for Hermus. Its HTML, CSS, and JavaScript are
served entirely by the gateway; it does not require a CDN or hosted frontend.
It is the single production UI (root `/` redirects to `/control`). The legacy
`/dashboard`, `/jarvis`, `/computer/dashboard`, `/remote` surfaces were folded into
it and their routes/assets removed.

## Start it

```bash
./hermus-gateway
# open http://localhost:8000/control
```

If `HERMUS_GATEWAY_TOKEN` is configured, open the room as
`/control?token=...`. The browser forwards it as an auth header for HTTP and as
a query credential only where browser SSE/WebSocket APIs cannot set headers.
The browser may also use a token saved under `hermus_gateway_token` in local
storage. Keys and provider credentials remain server-side.

## Design language: the Flight Deck

The room is a restrained holographic HUD, not a sci-fi wallpaper. One 14px
UI scale, one mono scale for data, 1 px hairlines, HUD corner brackets on
panels, and colour used only as a signal.

| Token | Value | Meaning |
|---|---|---|
| `--cyan` | `#5ee8ff` | primary / ready |
| `--mint` | `#7dffc4` | listening, healthy |
| `--amber` | `#ffbe5c` | blocked, acting |
| `--red` | `#ff5f6d` | error |
| `--violet` | `#9d8cff` | deliberation / verification |
| `--ink-0` | `#04070c` | page |

**The living core** is a canvas projection, never an animation loop divorced
from truth. It resolves one state per poll:

| State | Derived from |
|---|---|
| `offline` | `/readyz` not 200 |
| `idle` | ready, queue clear |
| `thinking` | active jobs/runs in `/api/jarvis/status` |
| `acting` | a real tool/computer event, or a running computer task |
| `verifying` | a verification event |
| `blocked` | pending approvals in `/remote/approvals` |
| `error` | a failure event, held briefly |

Presence identity (`/presence`) labels the core; `/api/v1/harness/overview`
supplies the context tile. Motion pauses when the tab is hidden, is replaced by
a still frame under `prefers-reduced-motion`, and can be switched off with the
topbar **Calm** toggle (persisted in `localStorage`).

Assets are plain files served by the gateway, no build step:

| File | Role |
|---|---|
| `gateway/static/control.css` | token system + every component |
| `gateway/static/control-room.js` | panels, envelope-aware API layer, tabs |
| `gateway/static/console.js` | manifest-driven Systems tab |
| `gateway/static/control-core.js` | living-core canvas + state polling |
| `gateway/static/control-shell.js` | rail, command palette, drawer, composer |
| `gateway/static/control-agents.js` | Agents tab controller + real GPU probe |

`docs/assets/flightdeck-full.png` is the same room at a narrow viewport,
captured from the running gateway; `flightdeck-palette.png` shows the command
palette, and `flightdeck-widths.png` renders 1440 / 1024 / 768 / 520 px side by
side (vertical rail, two-row bar, single-column sections).

## Control Room

The opening view prioritizes running and monitoring tasks. The **Presence** tab
also gives Hermus durable continuity rather than only a task spinner:

- editable local identity (name, role, tone, values and greeting);
- current operational state (idle, thinking, working, verifying, learning or waiting for approval);
- ongoing user-approved goals and explicit check-in suggestions;
- short recent continuity moments from real turns and goal changes;
- a safe heartbeat that never calls a model or runs tools by itself;
- voice-first requests and optional local speech/talking-avatar rendering that
  leave redacted continuity moments while keeping execution on the canonical
  queue.

- a large directive composer with Agent, Agent Crew, Computer, and Talking modes;
- current mission state, progress phases, live events, and recent missions;
- a living Hermus core with distinct Researcher, Critic, Tool Runner, and
  Verifier robotic units;
- visible data exchange and event-driven agent activation;
- the pending human-authorization gate;
- local service and speech telemetry;
- a permanent computer-control Halt action.

Ambient motion can be paused independently of live data. The interface also
honors the browser's reduced-motion preference.

## Command modes

- **Agent** — regular Hermus chat or tool-capable agent operation.
- **Agent Crew** — multi-chat in preview mode or multi-agent execution when
  Preview First is disabled.
- **Computer** — starts a background computer-agent task. Preview First maps to
  `dry_run=true`.
- **Talking** — opens Live Agent Theatre and requests backend-generated speech.

With **Preview First** enabled, normal Agent directives are sent through a
planning/chat mode that avoids tool execution. Computer directives use the
computer agent's actual dry-run path. Permission checks and the global
computer-control halt remain active regardless of mode.

## Live Agent Theatre

Selecting **Talking** or **Enter Live View** opens the cinematic agent-watching
surface. It includes:

- an expressive Hermus core and four visibly distinct robotic crew units;
- animated collaboration links and data packets;
- current directive, active unit, mission progress, and core state;
- gateway and computer-agent event streams;
- large live captions;
- typed and microphone directives;
- backend-generated audio playback, mute, stop, and exit controls.

Talking sessions also open automatically when the dashboard receives a
`session_started` event with `talking=true`. Native browser fullscreen requires
a user gesture, so externally started sessions still open the viewport-filling
panel but may not enter native fullscreen.

## Connected modules

The control room is organised as eleven tabs (Overview, Missions, Computer,
Jobs, Telemetry, Safety, Remote, Presence, Voice, Agents, Systems). The
**Systems** tab is not
a fixed set of cards: it renders `GET /api/v1/console/manifest` — the capability
manifest declared in `core/console.py` — and probes every panel through
`GET /api/v1/console/panels`. Registered subsystems therefore show up
automatically (Runtime, Agents & devices, Memory & learning, Models & keys,
Capabilities, Safety & recovery, Workspace); a new subsystem appears by adding a
manifest row, and a panel whose owner is unavailable says so honestly instead of
rendering as ready.

The other tabs link to the live data modules they have always driven (Missions,
Computer, Agent Crew, Memory, Models, Connections, Settings) through the same
backend APIs.

## Local speech

Speech does not require a paid API. Backends are discovered in this order:

1. **Piper**, when `piper` and `HERMUS_PIPER_MODEL` are available.
2. **espeak-ng** or **espeak**.
3. Optional **pyttsx3**.

Example Piper configuration:

```bash
export HERMUS_TTS_BACKEND=piper
export HERMUS_PIPER_MODEL=/opt/piper/en_US-lessac-medium.onnx
```

Lightweight Linux fallback:

```bash
sudo apt install espeak-ng
export HERMUS_TTS_BACKEND=espeak
```

If no TTS backend is installed, Talking Mode continues with captions and live
operations telemetry. `/speech/status` reports the selected backend or setup
instructions.

Microphone input uses local faster-whisper. The browser sends a raw audio blob
to `/speech/transcribe`; input is capped at 25 MB and deleted after
transcription.

## Dashboard API surface

| Endpoint | Method | Purpose |
|---|---:|---|
| `/dashboard/status` | GET | Gateway, task, channel, and speech aggregate |
| `/dashboard/events` | WS | Agent and speech lifecycle events |
| `/computer/events` | WS | Computer-agent lifecycle events |
| `/speech/status` | GET | Local TTS discovery |
| `/speech/synthesize` | POST | Generate a local WAV speech clip |
| `/speech/audio/{audio_id}` | GET | Serve one traversal-safe generated clip |
| `/speech/transcribe` | POST | Transcribe microphone audio locally |
| `/control` | GET | Single production control room (root `/` → `/control`) |
| `/presence` | GET | Identity, current state, goals, heartbeat and continuity moments |
| `/presence/identity` | PUT | Update the local Hermus identity |
| `/presence/goals` | GET/POST | List or add ongoing goals |
| `/presence/check-in` | POST | Queue an explicit, read-only continuity check-in |
| `/api/v1/system/health` | GET | Live system health probe |
| `/api/v1/system/capabilities` | GET | Providers + tools + circuit status |
| `/api/v1/console/manifest` | GET | Capability manifest (panels, groups, actions, endpoints, verification) |
| `/api/v1/console/panels` | GET | Live probe of panels (`?ids=` narrows) |
| `/api/v1/console/projection/{panel_id}` | GET | One panel as a read-only projection |
| `/api/v1/console/action/{panel_id}/{action}` | POST | Server-side panel action (declared actions only; confirmation-gated) |
| `/api/v1/harness/overview` | GET | Harness counts, pending notices, last real turn, live budget |
| `/api/v1/harness/sessions` | GET | Recent server-owned sessions (bounded, redacted) |
| `/api/v1/harness/sessions/{id}` | GET | One session + pending notices/mail (404 when unknown) |
| `/api/v1/harness/events` | GET | File-shift notices and swarm mail, newest first, truncated |
| `/android/*` | — | Android control API (consent-gated, audited) |

`POST /command` accepts additive `talking: true` or `speak: true` fields and can
return `run_id`, `talking`, and `speech` metadata without breaking existing
callers.

## Safety and privacy

- Halt is permanently visible and calls the computer emergency-stop endpoint.
- Pause and Cancel control asynchronous computer missions at safe boundaries.
- Pending remote approvals are rendered from the local authorization queue.
- API keys remain server-side; the dashboard receives redacted metadata only.
- Generated speech is stored under `data/speech`, served through validated
  random IDs, and removed by age-based cleanup.
- Markdown code blocks and raw URLs are removed before speech synthesis.
- No synthetic task or approval is shown after the live gateway connection is
  established; empty states remain honest while the visuals stay active.
