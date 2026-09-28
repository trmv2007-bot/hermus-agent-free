"""Conversation. A real answer, streamed, in a room that keeps working.

The turn itself lives in :mod:`gateway.chat_turn`. This module is the HTTP
surface over it and nothing else: validate, stream, return.

Why the turn is not inlined here any more
-----------------------------------------
It used to be, and the confidence gate was an ``async def`` coroutine sitting
inside it. A coroutine cannot ``yield``, so every progress line it produced
had to be pushed onto a queue for some other task to pick up — and twice, the
queue's reader had already exited. The first escalation notice vanished that
way, and so did the first progress messages. Both failures were silent and
looked exactly like "the server was slow". The turn is now an async generator,
so a frame is a value handed to ``yield``: there is no second reader, no
queue, and no window in which a frame can be written into the void.

Why this is a direct model call and not the agent
-------------------------------------------------
The first version called ``HermusAgent.chat()`` in a worker thread and wedged
the entire gateway — ``GET /`` stopped answering, not just chat. An agent turn
is 188 tools across ``max_steps=32`` against a slow provider: minutes of
near-continuous Python bytecode. A thread cannot yield that, the GIL stays
held, and the event loop — which lives in the same interpreter — simply stops
being scheduled. ``multiprocessing.Process`` is the textbook answer and it did
not save it either: on Windows, spawn re-imports the module graph, the agent
came up in the parent again, and the loop died the same way.

So the design changed rather than the mechanism. This runs a plain chat
completion with the history the client sends. That is seconds of model time,
it is what a chat surface is actually for, and it cannot starve a loop that is
only ever waiting on a socket. The agent is still the right thing for work that
*does* need 188 tools — that is what ``POST /api/v1/commands``,
``POST /workspace/act`` and the job kinds run. The room has both: this for
talking, those for acting. Conflating them is what made the original one
endpoint that could do neither reliably.

Long work does not hold the request open
----------------------------------------
``POST /api/v1/chat`` streams one turn and returns. Work long enough that the
user would rather start typing again — or leave the room and come back —
goes through ``POST /api/v1/chat/background``: it hands the turn to the gateway
queue and answers in microseconds with an id. The turn keeps running, its
progress is published on the run bus, and the client can attach to the same
SSE stream later and replay from wherever it was. See ``gateway/handlers.py``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from gateway.chat_turn import (
    MAX_MESSAGE_CHARS,
    _extract_text,
    _render_content,
    run_turn,
    sse as _sse_frame,
)
from core.proactivity import get_floor

log = logging.getLogger(__name__)

router = APIRouter()

# Accepted spellings of "what the user said", so a caller does not have to
# guess which name this particular endpoint wanted.
_TEXT_KEYS = ("message", "prompt", "text")


def _text_of(payload: dict) -> str:
    for key in _TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _too_long(text: str) -> JSONResponse | None:
    if len(text) > MAX_MESSAGE_CHARS:
        return JSONResponse(
            {"ok": False, "error": f"message too long: {len(text)} chars (max {MAX_MESSAGE_CHARS})"},
            status_code=400,
        )
    return None


@router.post("/api/v1/chat")
async def chat(payload: dict):
    """One conversational turn, streamed as Server-Sent Events.

    Accepts `message` or `prompt` or `text`. Every frame is a value from the
    turn generator, rendered as it arrives — including the escalated answer,
    which used to arrive as one blob after a long silence.

    This endpoint is also where the room's *floor* is observed, and that is
    not incidental. The intent judge refuses to infer user state from
    behavioural signals — that refusal is a design decision, not a missing
    feature — so the only honest way it learns that someone is halfway through
    a sentence is for the thing handling the turn to say so. A chat turn is
    exactly that: it opens with the user holding the floor and closes with them
    no longer holding it, and the ambient loop reads that state to decide
    whether it is allowed to speak over it.
    """
    payload = payload or {}
    text = _text_of(payload)
    if not text:
        return JSONResponse({"ok": False, "error": 'no message — send {"message": "..."}'}, status_code=400)
    refusal = _too_long(text)
    if refusal is not None:
        return refusal

    model = payload.get("model") if isinstance(payload.get("model"), str) else None
    # `stream: false` is honoured, not ignored: a caller asking for one blob
    # is a caller (a script, a test) that reads the final frame, and silently
    # streaming at it would be a surprise.
    want_stream = payload.get("stream", True) is not False

    async def stream():
        # The floor is marked held around the *generator*, not around the
        # request. A StreamingResponse body runs after the handler returns, so
        # marking it held at request time and clearing it at return time would
        # release the floor before the first token — which is exactly the
        # window where the user is still composing their next thought.
        floor = get_floor()
        floor.note_user_speech(mid_sentence=True)
        try:
            async for frame in run_turn(
                text=text,
                history=payload.get("history"),
                system=payload.get("system"),
                model=model,
                max_tokens=payload.get("max_tokens"),
                want_stream=want_stream,
            ):
                yield _sse_frame(frame)
        finally:
            # Released on every exit: a client that disconnects mid-stream, a
            # provider timeout, an error. Leaving the floor held on any of
            # those would mute the assistant until something else cleared it.
            floor.clear_floor()
            floor.note_spoke()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/v1/chat/background")
async def chat_background(payload: dict):
    """Hand a long turn to the queue and answer immediately.

    The whole point is that the user is not held. A question that needs the
    big model can take a minute; making the browser sit on a connection for a
    minute means they cannot ask anything else, and if they navigate away the
    answer is lost even though the server went on and computed it.

    So this returns a job id in microseconds and the work continues. Progress
    goes onto the run bus, which is what ``GET /stream/run/{run_id}`` and
    ``GET /jobs/{id}/events`` already stream — including replay, so a client
    that connects late still sees the whole answer rather than a gap.
    """
    payload = payload or {}
    text = _text_of(payload)
    if not text:
        return JSONResponse({"ok": False, "error": 'no message — send {"message": "..."}'}, status_code=400)
    refusal = _too_long(text)
    if refusal is not None:
        return refusal

    from gateway.queue import job_queue

    if not job_queue.enabled or not job_queue.started:
        # Refuse rather than quietly running it inline. "Background" that
        # blocks is worse than an honest 503: the caller has already decided it
        # can walk away, and silently holding the connection defies that.
        return JSONResponse(
            {
                "ok": False,
                "error": "the job queue is not running — use /api/v1/chat and keep the connection open",
                "queue_started": bool(job_queue.started),
                "queue_enabled": bool(job_queue.enabled),
            },
            status_code=503,
        )

    session_key = str(payload.get("session_key") or "").strip()
    try:
        job = job_queue.submit(
            "chat.turn",
            {
                "text": text,
                "history": payload.get("history"),
                "system": payload.get("system"),
                "model": payload.get("model"),
                "max_tokens": payload.get("max_tokens"),
                "session_key": session_key,
            },
            # Its own lane by default. A background turn is a question the user
            # walked away from, not a message to be appended behind another one
            # — serializing them means a two-minute research answer blocks the
            # next quick question asked while it ran. An explicit session_key
            # opts back into ordering.
            session_key=session_key or f"chat-turn:{uuid.uuid4().hex[:8]}",
            timeout=float(payload.get("timeout") or 0) or None,
        )
    except KeyError as exc:
        return JSONResponse({"ok": False, "error": f"chat.turn is not registered — {exc}"}, status_code=503)
    except Exception as exc:  # noqa: BLE001 — refuse honestly rather than 500 blankly
        log.warning("could not queue a background turn: %s: %s", type(exc).__name__, exc)
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=503)

    return JSONResponse(
        {
            "ok": True,
            "job_id": job.id,
            "run_id": job.run_id,
            "status": job.status,
            # All three already exist, and all three replay. The user can open
            # any of them and watch from the beginning, or pick up mid-answer.
            "stream_url": f"/stream/run/{job.run_id}",
            "events_url": f"/jobs/{job.id}/events",
            "status_url": f"/jobs/{job.id}",
            "result_url": f"/jobs/{job.id}/result",
        }
    )


@router.get("/api/v1/chat/background/{job_id}")
async def chat_background_status(job_id: str):
    """Where a background turn got to, without opening a stream for it."""
    from gateway.queue import job_queue

    return JSONResponse(job_queue.status(job_id))
