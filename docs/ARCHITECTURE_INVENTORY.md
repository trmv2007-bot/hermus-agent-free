# Architecture inventory — measured, not claimed

Replaces the previous version of this file, which listed canonical owners and
then reported progress ("58/58 verified", "1354 passed") that no longer matched
what a person can run. This version answers three questions per subsystem:
**does it exist, does it run, and how do we know?** Every row was checked on
2026-11-23 against `main` @ `3046846` with the gateway running on port 8123
(isolated `HERMUS_HOME`, no `.env`, no model provider).

Sizes are `git ls-files` line counts, not estimates.

## Shape of the repository

| Piece | Size | Notes |
|---|---|---|
| Python | 140,002 lines / 544 files | `core/` alone is 81,599 lines in 256 modules |
| `gateway/` | 12,674 lines Python + 7,537 lines JS/CSS static | 41 routers, **424 route paths / 444 operations** (`/openapi.json`) |
| Control Room UI | `control.html` 888 + `nexus.css` 1,859 + `nexus.js` 1,209 + `control-room.js` ~1,500 + 5 more scripts | 11 tabs, 58 console panels, 203 tools behind `/tools` |
| Tests | 30,489 lines / 166 files, **1,627 collected** | UI tests use a Node + DOM-stub harness |
| Docs | 75 markdown files, 10,005 lines | `README.md` claims 16 of 18 capabilities "Complete" |

## Runtime truth (measured on this machine)

Started with `python hermus.py gateway start --port 8123`,
`HERMUS_MODEL=mock/mock`, `HERMUS_AUTO_CHANNELS=0`. Cold start to
`/control` returning 200: ~12 s.

| Surface | Endpoint | Measured | Verdict |
|---|---|---|---|
| Gateway boots | `/readyz` | 200, 3–5 ms warm | real |
| Control Room | `/control` | 200, zero console errors, zero page errors, all 11 panels render | real |
| System health | `/api/v1/system/health` | 200, 6 ms warm / 640–1,118 ms cold; **honest** | real; reports `computer.mouse unavailable` with the install command when `pyautogui` is missing |
| Capabilities | `/api/v1/system/capabilities` | 200; 29 of 41 ready on a dev install | real |
| Doctor | `/doctor/status` | 200 **14.4 s** | real but far too slow for a polled panel |
| Local engine | `/engine/status` | 200 **8.5 s**, `needs_install` | real, honest, slow |
| Console manifest | `/api/v1/console/manifest` | 58 panels, every endpoint present in the route table | manifest real; **0 panels probed** in a fresh session |
| Jobs / runs | `POST /jobs`, `/stream/run/{id}` | job succeeds (13.8 s cold, 131 ms warm); SSE replay works for live runs | real |
| Chat turn | Control Room hero input | job succeeds server-side; **the browser never receives the stream** (see PRODUCT_V2 §2.3) | broken end to end |
| Presence | `/presence`, `/presence/kernel` | 200, 26 ms; identity/goals/moments present | real |
| Personal space | `/personal-space` | 200 | real |
| Memory | `/memory/stats`, `/memory2/*` | 200; FTS5 on, 0 memories | real, empty |
| Red lines / safety | `/red-lines/policy`, `/safety/*` | 11 red lines, `/reliability/status` healthy | real |
| Computer control | `/computer/status` | `backends.mouse: pyautogui` once installed; dry-run otherwise | real, host-dependent |
| Screen capture | `/screen/status` | `running:false, fps:10, buffer 128 MB` | real, opt-in |
| Voice | `/voice/status` | `enabled:false` (needs `HERMUS_VOICE_ENABLED`), `ack_mode: canned` | partial: TTS unavailable without `pyttsx3` |
| Models | `/models/catalog`, `/models/health` | catalog of providers; `models:{}` with no provider | real; nothing reachable by default |
| MCP / tools | `/tools`, `/mcp/servers` | 203 tools registered | real |
| Android | `/android/*` | `AndroidUnavailable` | contract only |
| Avatar | `/speech/avatar/status` | unavailable (needs a HeyGem service) | not usable |

## Canonical ownership (from the previous inventory, checked)

The one-owner-per-responsibility claims still hold in the import graph; the
gate `tests/test_architecture_gates.py` enforces several of them. Abbreviated to
what a reviewer needs:

| Responsibility | Owner | Checked |
|---|---|---|
| Autonomy / missions | `core/mission.MissionEngine` | yes, single engine |
| Durable events | `core/events.EventBus` | yes; other buses are projections |
| Live run stream | `core/run_events.RunBus` | yes; **replay after a gateway restart is broken** (PRODUCT_V2 G7) |
| Memory | `core/memory` facade over `core/memory2` | yes |
| Model selection | `core/models.ModelGateway` | yes |
| Tool boundary | `core/tool_registry` → `ToolGateway` | yes; agent does not bypass it |
| Jobs | `gateway/queue.JobQueue` | yes |
| Computer action | `core/computer/controller.ComputerActionController` | yes, single gated path |
| Safety | `core/permissions`, `core/emergency_stop`, `policies/` | yes |
| Control Room | `gateway/control.html` + `/control` | yes, single UI |

## Dead / unreachable in production

| Module | Lines | Evidence |
|---|---|---|
| `core/voice/executive_bridge.py` | 66 | constructed only in `tests/test_voice_executive_bridge.py` |
| `core/computer/execution_loop.py` | — | imported only by its test |
| `core/plugins/example_plugin.py` | — | referenced only by a test fixture |
| `core/avatar.py` | 416 | needs `heygem_tts_url` / `heygem_face2face_url`; absent → permanent `unavailable` |
| `core/free_keys.py` | — | used by `/keys/auto-provision-free`; works, but nothing calls it on the install path |
| `android_companion/` | native | never built here; transport degrades explicitly |
| `core/distributed*.py` + `/distributed/*` | — | single-process; no peer transport exercised |

Low-reference scan (a module referenced by ≤ 1 file across the tree) returned
exactly these seven plus `core/local_first.py` and `core/web_status.py`, which
*are* wired (`core/diagnostics.py` and the local-first path) — so the dead list
above is the accurate subset.

## What the docs claim vs. what runs

- `README.md` marks 16 capabilities "✅ Complete". Verified true for: safety,
  memory, jobs, tool gateway, control room, scheduling. Not true as written for:
  distributed, multimodal, avatar, fresh-machine installation (installers never
  install a model).
- `docs/PHASE_9..18_*.md` describe intent. They are history, not status.
- `docs/ARCHITECTURE_INVENTORY.md` (this file) is the status file.

## Re-running this inventory

```
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
HERMUS_HOME=<scratch> HERMUS_DATA_DIR=<scratch> HERMUS_GATEWAY_PORT=8123 \
  HERMUS_MODEL=mock/mock .venv/Scripts/python hermus.py gateway start --port 8123
curl -s localhost:8123/api/v1/system/health
python ../drive/audit.py <tag> 1440   # Playwright, writes renders + json
```

Nothing above writes to `~/.hermus`, and no route in this inventory mutates
state (all probes are GETs).
