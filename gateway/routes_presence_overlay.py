"""Presence overlay API + renderer page.

The bridge between the floating character and the rest of HERMUS. Four
endpoints the shell and the agent call (``state``, ``point``, ``say``,
``hide``), a read surface (``/api/presence/stream``, server-sent events) the
shell subscribes to, and the renderer page itself at ``/presence/overlay``.

Design notes that matter:

* **Never crash on a payload.** Every body is parsed through
  ``presence_app.geometry``; a malformed rect or an unknown state comes back as
  a 400 with the offending field named, and the gateway keeps serving. There is
  no ``assert`` and no unhandled key lookup on the request path.
* **SSE, not WebSocket.** Same-origin ``EventSource`` needs no handshake code in
  the renderer, reconnects on its own, and keeps the whole overlay on one gated
  HTTP router (WebSocket routes cannot take a ``Request``-based auth dependency).
* **The stream is a projection, not an event log.** Each frame is a full
  snapshot, so a renderer that connects late — or drops for a second — is always
  exactly correct rather than missing a delta.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from core.config import config
from core.log import get_logger
from presence_app.bridge import get_presence_bridge
from presence_app.geometry import PresencePayloadError

logger = get_logger(__name__)

router = APIRouter(tags=["presence-overlay"])

STATIC_DIR = Path(__file__).parent / "static" / "presence"
_STREAM_TICK_SECONDS = 0.2
_STREAM_HEARTBEAT_SECONDS = 10.0


def _bad(error: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error}, status_code=status_code)


def _token_query() -> str:
    """The gateway token as a query suffix, so a rendered URL stays authenticated."""
    token = config.gateway_api_token
    return f"?token={token}" if token else ""


# --------------------------------------------------------------------- page
def _serve(name: str, media_type: str) -> Response:
    path = STATIC_DIR / name
    if not path.exists():
        return Response(f"{name} not found", status_code=404, media_type="text/plain")
    return Response(
        path.read_text(encoding="utf-8"),
        media_type=media_type,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/presence/overlay")
async def presence_overlay_page():
    """The presence renderer (a self-contained page; no build step).

    Query parameters drive it deterministically for screenshots and for the
    shell: ``state``, ``say``, ``point=x,y,w,h``, ``label``, ``dock``,
    ``reduced=1``, ``static=1``.
    """
    path = STATIC_DIR / "index.html"
    if not path.exists():
        return HTMLResponse("Presence renderer not found", status_code=404)
    html = path.read_text(encoding="utf-8").replace("__TOKEN_QUERY__", _token_query())
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/presence/overlay/app.css")
async def presence_overlay_css():
    return _serve("app.css", "text/css; charset=utf-8")


@router.get("/presence/overlay/app.js")
async def presence_overlay_js():
    return _serve("app.js", "application/javascript; charset=utf-8")


# ------------------------------------------------------------------ read api
@router.get("/api/presence/state")
async def presence_state():
    return get_presence_bridge().snapshot()


@router.get("/api/presence/stream")
async def presence_stream():
    """Server-sent snapshot stream: one frame per change, plus a heartbeat."""
    bridge = get_presence_bridge()

    async def frames():
        bridge.enter()
        try:
            yield "retry: 1000\n\n"
            last = -1
            idle_ticks = 0
            while True:
                snapshot = bridge.snapshot()
                version = int(snapshot.get("version", 0))
                if version != last:
                    last = version
                    idle_ticks = 0
                    yield f"data: {json.dumps(snapshot, separators=(',', ':'))}\n\n"
                else:
                    idle_ticks += 1
                    if idle_ticks * _STREAM_TICK_SECONDS >= _STREAM_HEARTBEAT_SECONDS:
                        idle_ticks = 0
                        yield ": ping\n\n"
                await asyncio.sleep(_STREAM_TICK_SECONDS)
        except asyncio.CancelledError:  # client went away
            raise
        finally:
            bridge.leave()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ----------------------------------------------------------------- write api
@router.post("/api/presence/state")
async def presence_set_state(payload: dict[str, Any] | None = None):
    """Move the character: ``{state, ttl_ms?, rationale?}``."""
    payload = payload or {}
    state = payload.get("state")
    if not isinstance(state, str) or not state.strip():
        return _bad("state is required")
    bridge = get_presence_bridge()
    if state.strip().lower() not in bridge.snapshot()["states"]:
        return _bad(f"unknown state {state!r}")
    ttl = payload.get("ttl_ms")
    if ttl is not None and not isinstance(ttl, (int, float)):
        return _bad("ttl_ms must be a number")
    return bridge.set_state(state, ttl_ms=int(ttl or 0), rationale=str(payload.get("rationale") or ""))


@router.post("/api/presence/point")
async def presence_point(payload: dict[str, Any] | None = None):
    """Point at a screen rect: ``{x, y, w, h, label?, ttl_ms?, space?}``.

    ``space`` is ``dip`` (default) or ``physical``; physical rects are converted
    through the registered display layout so a click on a second high-DPI
    monitor lands on the right one.
    """
    payload = payload or {}
    if not isinstance(payload, dict):
        return _bad("payload must be an object")
    space = str(payload.get("space") or "dip")
    if space.lower() not in ("dip", "physical", "px", "device"):
        return _bad("space must be 'dip' or 'physical'")
    ttl = payload.get("ttl_ms")
    if ttl is not None and not isinstance(ttl, (int, float)):
        return _bad("ttl_ms must be a number")
    label = payload.get("label", "")
    if label is not None and not isinstance(label, str):
        return _bad("label must be a string")
    try:
        return get_presence_bridge().point(
            payload, label=label or "", ttl_ms=int(ttl or 0), space=space, source=str(payload.get("source") or "api")
        )
    except PresencePayloadError as exc:
        return _bad(str(exc))


@router.post("/api/presence/say")
async def presence_say(payload: dict[str, Any] | None = None):
    """Show a subtitle: ``{text, ttl_ms?}`` (max two lines, 240 characters)."""
    payload = payload or {}
    text = payload.get("text")
    if text is None:
        return _bad("text is required")
    if not isinstance(text, str):
        return _bad("text must be a string")
    ttl = payload.get("ttl_ms")
    if ttl is not None and not isinstance(ttl, (int, float)):
        return _bad("ttl_ms must be a number")
    try:
        return get_presence_bridge().say(text, ttl_ms=int(ttl or 0), source=str(payload.get("source") or "api"))
    except PresencePayloadError as exc:
        return _bad(str(exc))


@router.post("/api/presence/hide")
async def presence_hide():
    """Retract everything and put the character to sleep (the shell hides itself)."""
    return get_presence_bridge().hide()


@router.post("/api/presence/show")
async def presence_show():
    return get_presence_bridge().show()


@router.post("/api/presence/retract")
async def presence_retract():
    """Drop the current point but keep the character and any subtitle."""
    return get_presence_bridge().retract()


@router.post("/api/presence/input")
async def presence_input(payload: dict[str, Any] | None = None):
    """Report keyboard quiet time so subtitles never appear mid-typing."""
    payload = payload or {}
    try:
        return get_presence_bridge().note_input(payload.get("idle_ms"))
    except PresencePayloadError as exc:
        return _bad(str(exc))


@router.post("/api/presence/displays")
async def presence_displays(payload: dict[str, Any] | list[Any] | None = None):
    """Register the monitor layout: ``[{x, y, w, h, scale, physical?}]`` in DIP."""
    body = payload if isinstance(payload, list) else (payload or {}).get("displays")
    try:
        return get_presence_bridge().set_displays(body or [])
    except PresencePayloadError as exc:
        return _bad(str(exc))


@router.get("/api/presence/health")
async def presence_health(deep: int = Query(0, ge=0, le=1)):
    """Liveness for the CLI: is the bridge here, and does the renderer exist?"""
    status = {
        "ok": True,
        "renderer": (STATIC_DIR / "index.html").exists(),
        "clients": get_presence_bridge().snapshot()["clients"],
    }
    if deep:
        status["renderer_dir"] = str(STATIC_DIR)
    return status
