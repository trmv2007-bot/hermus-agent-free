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

If `HERMUS_GATEWAY_TOKEN` is configured, open the dashboard with `?token=...`.
The browser may also use a token saved under `hermus_gateway_token` in local
storage. Keys and provider credentials remain server-side.

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

The left rail opens live data modules for Missions, Computer, Agent Crew,
Memory, Models, Connections, and Settings. The Computer module links to the
existing detailed computer-agent dashboard rather than replacing it.

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
