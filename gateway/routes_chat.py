"""Conversation. A real answer, streamed, in a room that keeps working.

Why this is a direct model call and not the agent
-------------------------------------------------
The first version of this endpoint called ``HermusAgent.chat()`` in a worker
thread, and it wedged the entire gateway — ``GET /`` stopped answering, not
just chat. The cause is worth writing down because it is not obvious and the
obvious fix does not work.

An agent turn is 188 tools across ``max_steps=32`` against a slow provider:
minutes of near-continuous Python bytecode. A thread cannot yield that, the
GIL stays held, and the event loop — which lives in the same interpreter —
simply stops being scheduled. Moving the turn to a ``multiprocessing.Process``
is the textbook answer and it did not save it either: on Windows, spawn
re-imports the module graph, the agent came up in the parent again, and the
loop died the same way.

So the design changed rather than the mechanism. This endpoint runs a plain
chat completion with the conversation history the client sends. That is
2.5s of model time, not minutes, it is what a chat surface is actually for,
and it cannot starve a loop that is only ever waiting on a socket.

The agent is still the right thing for work that *does* need 188 tools — it
is what ``POST /api/v1/commands`` and ``POST /workspace/act`` run. The room
now has both: this for talking, those for acting. Conflating them is what made
the original one endpoint that could do neither reliably.

A thread is used for the model call, and here that is correct for the exact
opposite reason to the agent case: this work spends its life waiting on a
socket, and ``requests`` releases the GIL for the whole wait.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

log = logging.getLogger(__name__)

router = APIRouter()

# A chat completion. Generous for a free provider behind a cold TLS handshake,
# and short enough that a wedged turn is reclaimed rather than held.
TURN_TIMEOUT_S = 120.0
MAX_MESSAGE_CHARS = 8000
MAX_HISTORY = 40

PERSONA = (
    "You are HERMUS, a local voice assistant running on the user's own machine. "
    "Be direct and concrete. Prefer a short true answer over a long hedged one. "
    "If you do not know, say so plainly rather than guessing."
)


def _event(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """One SSE frame, named so a client can ignore frames it does not
    understand instead of inferring their meaning from the payload shape."""
    return {"event": name, "data": data}


def _clean_history(raw: Any) -> list[dict[str, str]]:
    """Keep the last few turns, in the shape the provider wants.

    History arrives from the browser, so it is untrusted input. Anything that
    is not a string role/content pair with a known role is dropped rather than
    forwarded, because a caller able to inject an arbitrary role can steer the
    system prompt.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw[-MAX_HISTORY:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if content:
            out.append({"role": role, "content": content[:MAX_MESSAGE_CHARS]})
    return out


@router.post("/api/v1/chat")
async def chat(payload: dict):
    """One conversational turn, streamed as Server-Sent Events.

    Accepts `message` or `prompt` or `text`, so a caller does not have to
    guess which name this particular endpoint wanted.
    """
    text = payload.get("message") or payload.get("prompt") or payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"ok": False, "error": 'no message — send {"message": "..."}'}, status_code=400)
    text = text.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        return JSONResponse(
            {"ok": False, "error": f"message too long: {len(text)} chars (max {MAX_MESSAGE_CHARS})"},
            status_code=400,
        )

    history = _clean_history(payload.get("history"))
    system = payload.get("system")
    messages = [
        {"role": "system", "content": system if isinstance(system, str) and system.strip() else PERSONA}
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": text})

    async def stream():
        # Progress first, so the panel has something honest to show during the
        # provider's cold start, which on a free tier can be several seconds.
        yield _sse(_event("status", {"phase": "thinking", "messages": len(messages) - 1}))
        started = time.monotonic()

        try:
            from core.models import get_model_gateway

            model = payload.get("model") if isinstance(payload.get("model"), str) else None
            llm = await asyncio.to_thread(get_model_gateway().llm, model=model or None)
        except Exception as exc:  # noqa: BLE001
            yield _sse(_event("error", {"error": f"no model is reachable: {type(exc).__name__}: {exc}"}))
            return

        try:
            response = await asyncio.wait_for(asyncio.to_thread(llm.chat, messages), timeout=TURN_TIMEOUT_S)
        except asyncio.TimeoutError:
            yield _sse(_event("error", {"error": f"the model did not answer within {TURN_TIMEOUT_S:.0f}s"}))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("chat failed")
            yield _sse(_event("error", {"error": f"{type(exc).__name__}: {exc}"}))
            return

        answer = _extract_text(response)
        if not answer.strip():
            # A successful call that produced nothing must not render as an
            # empty bubble, which is indistinguishable from a model that chose
            # to say nothing.
            yield _sse(
                _event(
                    "error",
                    {
                        "error": "the model answered with no text — check the main model in Settings",
                        "raw_type": type(response).__name__,
                    },
                )
            )
            return

        yield _sse(
            _event(
                "final",
                {"ok": True, "content": answer, "elapsed_s": round(time.monotonic() - started, 2)},
            )
        )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(frame: dict[str, Any]) -> str:
    return f"event: {frame['event']}\ndata: {json.dumps(frame['data'], default=str)}\n\n"


def _extract_text(response: Any) -> str:
    """Pull the words out of whatever the model layer returned.

    The return shape is not one documented contract, and guessing wrong here
    means a chat panel showing an empty bubble next to a spinner that already
    stopped. So: try the known shapes, and never return "[object Object]".
    """
    # An LLMResponse with a .content, which is what this gateway's own model
    # gateway returns.
    for attr in ("content", "text", "output_text"):
        value = getattr(response, attr, None)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (list, dict)):
            rendered = _render_content(value)
            if rendered:
                return rendered

    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return ""

    # OpenAI-compatible shape.
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                rendered = _render_content(message.get("content"))
                if rendered:
                    return rendered
                # Reasoning models put the answer in a sibling field.
                for key in ("reasoning_content", "reasoning"):
                    if isinstance(message.get(key), str) and message[key].strip():
                        return message[key]
            if isinstance(first.get("text"), str):
                return first["text"]

    for key in ("content", "response", "text", "answer", "output", "message", "final"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (list, dict)):
            rendered = _render_content(value)
            if rendered:
                return rendered
    return ""


def _render_content(content: Any) -> str:
    """Anthropic/OpenAI-style content blocks down to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if isinstance(block.get("text"), str):
                parts.append(block["text"])
            # Thinking blocks are dropped on purpose: a chat surface that
            # shows the model's scratchpad is showing the user the plumbing.
    return "\n\n".join(p for p in parts if p.strip())
