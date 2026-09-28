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
import threading
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

log = logging.getLogger(__name__)

from core.config import config  # noqa: E402

router = APIRouter()

# A chat completion. Generous for a free provider behind a cold TLS handshake,
# and short enough that a wedged turn is reclaimed rather than held.
TURN_TIMEOUT_S = 120.0
MAX_MESSAGE_CHARS = 8000
MAX_HISTORY = 40

def _persona() -> str:
    """The system prompt, built from the live tool registry.

    Not a constant. A hardcoded persona goes stale the moment a tool is added
    or removed, and a model that believes it is more capable than it is will
    confidently promise something it cannot do. Building it per turn costs one
    registry read, which is already in memory.
    """
    try:
        from core.persona import build_persona

        return build_persona().describe()
    except Exception as exc:  # noqa: BLE001
        log.warning("persona unavailable (%s); using the minimal prompt", exc)
        return (
            "You are HERMUS, a voice assistant running on the user's own machine. "
            "Be direct and concrete. If you do not know, say so plainly."
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


async def _maybe_escalate(
    answer: str,
    logprobs,
    messages: list[dict],
    elapsed_s: float | None = None,
) -> tuple[str | None, object | None]:
    """Ask the main model when the local one is not confident enough.

    The local model answers first because it is free, private and warm. This is
    the check that decides whether its answer is good enough to show.

    Returns (replacement_text, reading). replacement_text is None when the
    local answer stands. It never raises: a gate that can fail a conversation
    is worse than no gate, so every failure here keeps the local answer and
    says why in the log.
    """
    try:
        from core.confidence import assess
        from core.config import config
    except Exception as exc:  # noqa: BLE001
        log.warning("confidence gate unavailable: %s: %s", type(exc).__name__, exc)
        return None, None

    bar = float(getattr(config, "confidence_bar", 0.0) or 0.0)
    if bar <= 0:
        return None, None

    reading = assess(logprobs, text=answer, bar=bar, elapsed_s=elapsed_s)
    # No logprobs means unmeasured, not confident, but escalating every call
    # from a provider that omits them would make the local tier unusable.
    if not reading.escalate or reading.confidence is None:
        return None, reading

    from core.config import config as _cfg
    from core.models import get_model_gateway

    big = _cfg.model

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(get_model_gateway().chat, messages, model=big),
            timeout=TURN_TIMEOUT_S,
        )
        text = _extract_text(response)
    except Exception as exc:  # noqa: BLE001
        log.warning("escalation to %s failed (%s); keeping the local answer", big, exc)
        return None, reading

    if not text.strip():
        # The big model also produced nothing. Showing the local answer beats
        # an empty bubble, and the frame below already told the client.
        log.warning("escalation to %s produced no text; keeping the local answer", big)
        return None, reading
    return text, reading


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
        {"role": "system", "content": system if isinstance(system, str) and system.strip() else _persona()}
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

        # Stream tokens when the provider will, so the first word lands in the
        # panel while the rest is still being written.
        #
        # This is the whole latency story. The provider's first token arrives in
        # well under a second and the rest trails behind it, so a client that
        # waits for the final string converts a sub-second first word into a
        # multi-second silence and then dumps the whole answer at once. The
        # blocking call below is still the fallback for a provider that cannot
        # stream, and for the test suite, which asserts on a final frame.
        queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()
        streamed: list[str] = []
        # Holds the provider's response so the confidence bar can read its
        # logprobs. This has to exist: without it the assignment in _produce
        # raises NameError *after* the deltas have already been handed over, so
        # the turn looks fine to the client and is silently marked truncated in
        # the log, and the gate never sees a single logprob.
        result: dict[str, object] = {}

        def _on_delta(chunk: str) -> None:
            # Called from the provider's worker thread, so the hand-off to the
            # event loop has to go through a thread-safe call. A bare
            # queue.put_nowait from another thread is not safe against a
            # running loop.
            loop.call_soon_threadsafe(queue.put_nowait, ("delta", chunk))

        def _produce() -> None:
            try:
                if payload.get("stream", True):
                    from core.openai_compat import stream_chat_completions

                    bundle = llm._resolve_bundle()
                    # Kept, not discarded: the confidence bar reads its
                    # logprobs, and the streaming path is the one people use.
                    result["value"] = stream_chat_completions(
                        provider=llm.provider,
                        model=llm.model_name,
                        messages=messages,
                        api_key=bundle.get("key") or None,
                        base_url=bundle.get("base_url") or None,
                        on_delta=_on_delta,
                    )
                else:
                    _ = llm.chat(messages)
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, (_SENTINEL, None))

        loop = asyncio.get_running_loop()
        worker = threading.Thread(target=_produce, daemon=True, name="chat-turn")
        worker.start()

        first_token_s: float | None = None
        deadline = time.monotonic() + TURN_TIMEOUT_S
        error: Exception | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = TimeoutError(f"the model did not answer within {TURN_TIMEOUT_S:.0f}s")
                break
            try:
                kind, item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                if not worker.is_alive():
                    break
                yield _sse(_event("keepalive", {"elapsed_s": round(time.monotonic() - started, 1)}))
                continue
            if kind is _SENTINEL:
                break
            if kind == "error":
                error = item
                break
            if kind == "event":
                # A frame about the turn rather than part of the answer, e.g.
                # the confidence gate handing the question to the main model.
                # It must not touch `streamed` or it would end up rendered
                # inside the answer text.
                for name, data in (item or {}).items():
                    yield _sse(_event(name, data))
                continue
            if item:
                if first_token_s is None:
                    first_token_s = round(time.monotonic() - started, 3)
                    # Tells the panel to move the orb to speaking now, rather
                    # than at the end, which is what makes it feel alive.
                    yield _sse(_event("speaking", {"first_token_s": first_token_s}))
                streamed.append(item)
                yield _sse(_event("delta", {"text": item}))

        if error is not None:
            if streamed:
                # Keep the partial answer. Truncating a real answer because the
                # deadline hit is worse than showing it short.
                yield _sse(
                    _event(
                        "final",
                        {
                            "ok": True,
                            "content": "".join(streamed),
                            "elapsed_s": round(time.monotonic() - started, 2),
                            "first_token_s": first_token_s,
                            "truncated": True,
                            "note": str(error),
                        },
                    )
                )
                return
            if isinstance(error, TimeoutError):
                yield _sse(_event("error", {"error": str(error)}))
            else:
                log.exception("chat failed")
                yield _sse(_event("error", {"error": f"{type(error).__name__}: {error}"}))
            return

        if streamed:
            # The confidence bar runs here too, not only on the blocking path.
            # Placing it after this return is what made it dead code: streaming
            # always succeeded, so the gate never executed once.
            escalated, reading = await _maybe_escalate(
                "".join(streamed),
                getattr(result.get("value"), "logprobs", None),
                messages,
                elapsed_s=time.monotonic() - started,
            )
            if reading is not None and reading.escalate:
                # Yielded here rather than pushed onto the queue: the stream loop
                # has already consumed the sentinel by this point, so anything
                # queued would sit unread and the user would see a slow answer
                # with no explanation.
                yield _sse(
                    _event(
                        "escalated",
                        {
                            "reason": reading.reason,
                            "confidence": reading.confidence,
                            "bar": reading.bar,
                            "to": config.model,
                            "replaced": escalated is not None,
                        },
                    )
                )
            if escalated is not None:
                streamed = [escalated]
            yield _sse(
                _event(
                    "final",
                    {
                        "ok": True,
                        "content": "".join(streamed),
                        "elapsed_s": round(time.monotonic() - started, 2),
                        "first_token_s": first_token_s,
                    },
                )
            )
            return

        # Nothing streamed, so the provider declined to stream. Do the blocking
        # call and report it honestly rather than rendering an empty bubble.
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

        # The confidence bar. The local model answers first because it is free,
        # private and warm; this is the check that decides whether its answer is
        # good enough to show or whether the main model should be asked instead.
        #
        # It is deliberately after the answer arrives rather than before, so the
        # fast path pays nothing, and the streamed path runs it too, so a slow
        # answer is explainable instead of mysterious.
        escalated, reading = await _maybe_escalate(
            answer,
            getattr(response, "logprobs", None),
            messages,
            elapsed_s=time.monotonic() - started,
        )
        if reading is not None and reading.escalate:
            yield _sse(
                _event(
                    "escalated",
                    {
                        "reason": reading.reason,
                        "confidence": reading.confidence,
                        "bar": reading.bar,
                        "to": config.model,
                        "replaced": escalated is not None,
                    },
                )
            )
        if escalated is not None:
            answer = escalated

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
