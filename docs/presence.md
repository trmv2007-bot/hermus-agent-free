# Presence overlay — the floating character

HERMUS has a face now. A holographic character lives on the desktop, breathes
while it is idle, changes state while it works, says one sentence at a time, and
physically points an arrow at the spot it is about to click or type in.

It is **optional software**: nothing else in HERMUS imports it at startup, and
the gateway behaves identically when the character is asleep, absent, or has
never been installed.

```
gateway/static/presence/     the renderer (HTML + CSS + canvas JS, no build step)
presence_app/                state bridge, geometry/DPI, shell launcher, demo, client
gateway/routes_presence_overlay.py   the API + the /presence/overlay page
core/presence_pointer.py     the computer-use seam
hermus_cli/presence_cmd.py   hermus presence …
```

## Run it

```bash
hermus gateway start          # the API + the renderer page (port 8000 by default)
hermus presence install       # one-time: Electron runtime into data/presence_shell
hermus presence start         # the floating character, always-on-top
hermus presence demo          # it speaks and points at three screen regions
hermus presence stop          # closes the shell (and a gateway it started itself)
```

Without the shell you can still see everything in a browser tab:
`http://localhost:8000/presence/overlay`. The page is the same renderer the
shell hosts, so screenshots, theming and testing all work without Electron.

## The seven states

| state       | what it reads as                                  |
| ----------- | ------------------------------------------------- |
| `idle`      | breathing, three slow orbitals, occasional drift   |
| `listening` | rings widen, soft ripples travel outward          |
| `thinking`  | counter-rotating arcs speed up, dust spirals in    |
| `acting`    | rings tighten, one bright arc holds the target bearing |
| `speaking`  | the core pulses in a speech rhythm                 |
| `error`     | warm accent shift, a broken dash ring              |
| `sleeping`  | everything dims and sinks, one faint ring          |

Every transition is a cross-fade of colour, radius and speed (380 ms for
adjacent moods, 700 ms otherwise, slower still when leaving `error`), so nothing
cuts. `prefers-reduced-motion` — or `?reduced=1` — stops the travelling arrow,
the pulses and the transitions.

```bash
curl -X POST localhost:8000/api/presence/state -H 'content-type: application/json' \
     -d '{"state":"thinking","ttl_ms":4000}'   # decays back to idle by itself
curl -X POST localhost:8000/api/presence/say -H 'content-type: application/json' \
     -d '{"text":"Reading the page you left open."}'
```

## The API

| method | path                     | body                                            |
| ------ | ------------------------ | ----------------------------------------------- |
| GET    | `/api/presence/state`    | —                                               |
| GET    | `/api/presence/stream`   | — (server-sent snapshots)                       |
| POST   | `/api/presence/state`    | `{state, ttl_ms?, rationale?}`                  |
| POST   | `/api/presence/point`    | `{x, y, w, h, label?, ttl_ms?, space?}`         |
| POST   | `/api/presence/say`      | `{text, ttl_ms?}`                               |
| POST   | `/api/presence/hide`     | —                                               |
| POST   | `/api/presence/show`     | —                                               |
| POST   | `/api/presence/retract`  | —                                               |
| POST   | `/api/presence/input`    | `{idle_ms}` (typing guard, sent by the shell)   |
| POST   | `/api/presence/displays` | `[{x, y, w, h, scale, physical?}]` in DIP       |

`space` is `dip` (default) or `physical`. Physical rects are converted per
monitor through the registered display layout, which is what keeps the arrow on
the right screen on a mixed-DPI multimonitor machine.

**Validation is total.** `x` must be a number, sides must be positive and under
20 000 px, TTLs clamp to 300 ms…60 s, labels to 96 characters, subtitles to 240.
Anything else answers `400 {"ok": false, "error": "…"}` naming the field. The
gateway never sees an exception from a malformed rect — there is a test for it.

## Pointing, exactly

A point is a rect plus a TTL. The renderer:

1. grows the rect by 7 px and rings *that* box — the ring's inner edge is
   outside the target at any stroke width up to the gap, so the target is never
   drawn over;
2. sends a travelling arrow from the character to the nearest point on that ring,
   so the arrowhead also stops outside the target;
3. pulses the ring, then retracts the arrow back to the character before the TTL
   ends, and clears the label.

The label is placed above the ring, below it at the top of the screen, or
dropped entirely if neither fits — never on top of the target.

## Wire real actions

`core/computer/controller.py` calls `core.presence_pointer.announce(action, args)`
immediately before every gated action and `announce_result` after it. Clicks and
moves point at the cursor; typing points at the focused window (measured with
Win32 `GetForegroundWindow`/`GetWindowRect`, and skipped when that cannot be
measured rather than guessed). A failed action puts the character in `error`.

* `HERMUS_PRESENCE_POINTER=0` unhooks it entirely.
* `HERMUS_PRESENCE_LEAD_MS=350` makes the controller wait up to that long before
  acting, so the arrow visibly lands *before* the click. Default `0`: the overlay
  never delays a real action.

## Platform support, honestly

| platform | shell                    | what was actually verified                         |
| -------- | ------------------------ | -------------------------------------------------- |
| Windows 11 | Electron, verified     | transparent click-through window, pointing at real windows, multi-monitor display layout, `GetForegroundWindow` typing target |
| macOS    | Electron, same code path | not run here — transparency + `setVisibleOnAllWorkspaces` are the documented Electron behaviour, nothing was measured |
| Linux    | Electron, same code path | not run here — needs a compositor (`enable-transparent-visuals` is set); X11 only |
| any      | browser tab              | the renderer alone, which is the whole visual layer |

The typing target is Windows-only: on macOS/Linux the character simply does not
point for keystrokes instead of pointing somewhere wrong.

## Known limits

* **Fullscreen** (games, video) is not detected: a click-through overlay cannot
  reliably see another app's exclusive fullscreen state. The character is
  invisible until something points or says, and `hermus presence hide` /
  `/api/presence/hide` puts it away.
* The window can only *see* the pointer over the character; the desktop below it
  is untouched.
* The browser-tab renderer cannot know the desktop layout, so `physical` rects
  fall back to the page's own device pixel ratio there. The shell registers the
  real layout.
