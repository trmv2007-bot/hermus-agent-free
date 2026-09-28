"""One conversational turn, expressed as an async generator of SSE frames.

Why this is a separate module, and why it is a generator
-------------------------------------------------------
The first version of this logic lived inline in ``/api/v1/chat`` and the
confidence gate was an ``async def`` coroutine. A coroutine **cannot yield**,
so every piece of progress it produced had to be pushed onto a queue for some
other task to pick up. That is exactly the design that lost frames twice: the
first escalation notice and the first progress lines were queued onto a queue
whose reader had already exited, so they were never seen by anyone. The failure
mode is silent and it looks identical to "the server was just slow".

Making the whole turn an ``async generator`` removes the failure mode by
construction. There is no second reader, no queue, and no window in which a
frame can be written into the void: a frame exists only as a value handed to
``yield``, and if the consumer stops iterating the frame was never created.
The same generator is what the background job handler drives, so both paths
cannot drift apart.

Why conversation is a direct model call and not the agent
---------------------------------------------------------
An agent turn is 188 tools across ``max_steps=32`` against a slow provider:
minutes of near-continuous Python bytecode. A thread cannot yield that, the
GIL stays held, and the event loop — which lives in the same interpreter —
stops being scheduled. It wedged this gateway once already; ``GET /`` stopped
answering, not just chat. So this module runs a plain chat completion with the
conversation history the client sends. That is seconds of socket-waiting, not
minutes of bytecode, and it cannot starve a loop that is only ever waiting on
a socket. Work that genuinely needs 188 tools belongs on ``runtime.turn`` and
the action endpoints, not here.

The one thread this module does use is correct for the opposite reason: the
provider call spends its life waiting on a socket, and ``requests`` releases
the GIL for the whole wait.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import replace
from typing import Any, AsyncIterator, Callable

log = logging.getLogger(__name__)

# A chat completion. Generous for a free provider behind a cold TLS handshake,
# and short enough that a wedged turn is reclaimed rather than held.
TURN_TIMEOUT_S = 120.0

# How often to say "still here" while waiting. Silence past ~1s reads as a
# dead socket to some proxies and to the user, and a heartbeat costs one frame.
KEEPALIVE_S = 1.0

# Ceiling on what the local tier may say in one turn. Override with
# HERMUS_LOCAL_MAX_TOKENS.
LOCAL_MAX_TOKENS = int(os.getenv("HERMUS_LOCAL_MAX_TOKENS", "400") or 400)

# The escalated tier gets a bigger budget than the local one — it is the model
# that was promoted *because* the question was hard — but it is still capped.
# Unbounded is what made the local tier take 78s and then throw the answer
# away for exceeding its length check.
ESCALATED_MAX_TOKENS = int(os.getenv("HERMUS_ESCALATED_MAX_TOKENS", "1200") or 1200)

# How long the local tier gets to produce its first *word*.
#
# The local model is a reasoning model: it emits its thinking as deltas on the
# same stream and only then answers. Measured on this machine with the live
# persona prompt, on a question it can handle, its first word lands between
# 3.4s and 7.7s. On a question it cannot, it reasons until max_tokens is gone
# and returns zero content — 10s, 53s, and a full 120s timeout, every run,
# with nothing at the end of any of them.
#
# So the deadline is set above the entire observed success band, not inside
# it. An earlier 8s value was chosen from the same measurements and was wrong:
# it sat inside the band, so a question that would have been answered in 4.5s
# was abandoned and escalated instead — trading a 4.5s answer for a 14s one.
# The value has to clear the *worst good* run, not the average.
#
# 12s clears the observed 7.7s with margin, and still cuts a 120s dead turn
# down to 12s. Override with HERMUS_LOCAL_FIRST_TOKEN_S; 0 waits it out.
LOCAL_FIRST_TOKEN_S = float(os.getenv("HERMUS_LOCAL_FIRST_TOKEN_S", "12") or 0)

MAX_MESSAGE_CHARS = 8000
MAX_HISTORY = 40

Frame = tuple[str, dict[str, Any]]


class FirstTokenTimeout(Exception):
    """A model produced no *content* within its deadline.

    Its own type because the recovery differs from every other failure. A
    provider that errors is retried; a provider that is merely slow is not.
    This one is known to have started generating and chosen to spend the whole
    budget thinking, so the useful response is to stop waiting and ask someone
    who can answer — not to wait longer, and not to re-ask the same model.
    """


# --------------------------------------------------------------------- prompts
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


def _last_user_text(messages: list[dict]) -> str:
    """The user's own words, for deciding what to look up.

    Takes the last user turn rather than the whole prompt: searching for a
    system prompt or a pasted document is how a retrieval step starts
    answering a question nobody asked.
    """
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()[:300]
    return ""


def _retrieve_context(question: str, limit: int = 3) -> str:
    """Anything already stored locally that is relevant to this question.

    This is what makes the memory store part of the conversation rather than a
    feature you visit. The agent path already did this; the direct chat path
    did not, so a fact you had ingested was unreachable by asking for it, and
    the only retrieval that ever ran was web search -- which is how the model
    could answer "I checked the search results [1][2][3]" while citing the
    internet for something that was sitting in its own database.

    Returns an empty string when there is nothing genuinely relevant, and an
    empty string is the correct answer most of the time. Injecting low-scoring
    context teaches the model to treat everything it is told as relevant, which
    is how a local store ends up making answers worse.
    """
    if not question or len(question.strip()) < 4:
        return ""
    try:
        from core.embeddings import embedding_store

        result = embedding_store.hybrid_search(question, limit=limit)
    except Exception:
        return ""

    rows = (result or {}).get("results") or []
    # A store that has not been reindexed returns rows whose vectors cannot be
    # compared at all; cosine scores them 0.0 and they are indistinguishable
    # from "nothing matched". Better to say nothing than to quote a document
    # the search never actually found.
    try:
        stale = embedding_store.stale_count()
    except Exception:
        stale = 0
    if stale:
        return ""

    picked = [r for r in rows if float(r.get("score") or 0) >= 0.35][:limit]
    if not picked:
        return ""

    lines = [f"- {r['content'][:400]}" for r in picked]
    return "Relevant things you already know (from local notes, not the web):\n" + "\n".join(lines)


def build_messages(text: str, history: Any = None, system: Any = None) -> list[dict[str, str]]:
    """The message list for one turn: system, cleaned history, the new ask."""
    messages = [
        {
            "role": "system",
            "content": system if isinstance(system, str) and system.strip() else _persona(),
        }
    ]
    messages.extend(_clean_history(history))
    messages.append({"role": "user", "content": text})
    return messages


# ------------------------------------------------------------ response shapes
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


def _extract_text(response: Any) -> str:
    """Pull the words out of whatever the model layer returned.

    The return shape is not one documented contract, and guessing wrong here
    means a chat panel showing an empty bubble next to a spinner that already
    stopped. So: try the known shapes, and never return "[object Object]".
    """
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


# ------------------------------------------------------------ provider plumbing
class _ProviderStream:
    """A streaming provider call, consumed as an async iterator.

    The call itself is blocking ``requests`` in a worker thread — correct here
    precisely because it is socket-waiting, and ``requests`` releases the GIL
    for the whole wait. The thread hands deltas back to the loop with
    ``call_soon_threadsafe``; a bare ``put_nowait`` from another thread is not
    safe against a running loop.

    Yields ``str`` deltas, and ``None`` for a keepalive tick (so a caller can
    prove to the user it is still alive without inventing progress). The
    accumulated :class:`CompatResponse` lands on ``.result`` once the call
    completes, and the exception on ``.error``.
    """

    _SENTINEL = object()

    def __init__(self, loop: asyncio.AbstractEventLoop, call: Callable[[Callable[[str], None]], Any], *, timeout: float):
        self._loop = loop
        self._call = call
        self._queue: asyncio.Queue = asyncio.Queue()
        self._deadline = time.monotonic() + timeout
        self.result: Any = None
        self.error: BaseException | None = None
        self.first_delta_at: float | None = None
        self._finished = False
        self._thread = threading.Thread(target=self._produce, daemon=True, name="hermus-provider")
        self._thread.start()

    def _on_delta(self, chunk: str) -> None:
        if not chunk:
            return
        if self.first_delta_at is None:
            self.first_delta_at = time.monotonic()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, ("delta", chunk))

    def _produce(self) -> None:
        try:
            self.result = self._call(self._on_delta)
        except BaseException as exc:  # noqa: BLE001 — surfaced to the consumer
            self.error = exc
        finally:
            # Always close the stream, or the consumer waits out the full
            # timeout for a call that already failed.
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, (self._SENTINEL, None))
            except RuntimeError:
                pass

    def __aiter__(self) -> "_ProviderStream":
        return self

    async def __anext__(self) -> str | None:
        if self._finished:
            raise StopAsyncIteration
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self._finished = True
            if self.error is not None:
                raise self.error
            raise TimeoutError("the provider did not finish within the turn budget")
        try:
            kind, item = await asyncio.wait_for(self._queue.get(), timeout=min(remaining, KEEPALIVE_S))
        except asyncio.TimeoutError:
            return None  # keepalive tick
        if kind is self._SENTINEL:
            self._finished = True
            if self.error is not None:
                raise self.error
            raise StopAsyncIteration
        return item


async def _tier_stream(
    tier: str,
    messages: list[dict],
    *,
    cap: int,
    first_token_deadline: float | None = None,
) -> AsyncIterator[Frame]:
    """Stream one model's answer, yielding ``("delta", text)`` frames.

    Yields a final ``("response", CompatResponse)`` so the caller can read
    logprobs. Raises on provider failure; the caller decides whether to fall
    back to the blocking call or move to the next tier.

    ``first_token_deadline`` abandons a model that has not produced a single
    *content* token in that many seconds. It exists because of a measured
    failure, not a hypothetical one: the local model emits its thinking as
    `delta.reasoning` on the same stream, so on a hard question it reasons
    until max_tokens is gone and then returns zero content — 10s, 53s, or a
    full 120s timeout spent producing nothing at all. Waiting out a model that
    has already demonstrated it cannot answer is not patience, it is just a
    longer wait for the same empty result.
    """
    from core.models import get_model_gateway
    from core.openai_compat import stream_chat_completions

    llm = await asyncio.to_thread(get_model_gateway().llm, model=tier)
    bundle = await asyncio.to_thread(llm._resolve_bundle)

    def call(on_delta: Callable[[str], None]) -> Any:
        return stream_chat_completions(
            provider=llm.provider,
            model=llm.model_name,
            max_tokens=cap,
            messages=messages,
            api_key=bundle.get("key") or None,
            base_url=bundle.get("base_url") or None,
            on_delta=on_delta,
        )

    began = time.monotonic()
    stream = _ProviderStream(asyncio.get_running_loop(), call, timeout=TURN_TIMEOUT_S)
    parts: list[str] = []
    abandoned = False
    async for delta in stream:
        if delta is None:
            # A keepalive tick, not a token. The caller decides whether to show
            # it; forwarding it as text would put a stray "None" in the answer.
            if first_token_deadline and stream.first_delta_at is None:
                if time.monotonic() - began > first_token_deadline:
                    # The worker thread is left to die on its own; it is a
                    # daemon doing socket I/O, and killing it mid-write would
                    # be worse than letting it finish unread.
                    abandoned = True
                    break
            yield ("keepalive", {})
            continue
        if delta:
            parts.append(delta)
            yield ("delta", delta)
    if abandoned:
        raise FirstTokenTimeout(
            f"{tier} produced no text within {first_token_deadline:.0f}s"
            + (f" (it reasoned {stream.result.usage.get('reasoning_chars', 0)} chars instead)" if stream.result else "")
        )
    response = stream.result
    log.info(
        "tier %s streamed %d chars in %.1fs (first token at %.1fs)",
        tier,
        len("".join(parts)),
        time.monotonic() - began,
        (stream.first_delta_at - began) if stream.first_delta_at else -1.0,
    )
    yield ("response", {"value": response, "text": "".join(parts), "tier": tier})


async def _tier_blocking(tier: str, messages: list[dict]) -> str:
    """The non-streaming fallback, for a provider that cannot stream at all."""
    from core.models import get_model_gateway

    response = await asyncio.wait_for(
        asyncio.to_thread(get_model_gateway().chat, messages, model=tier),
        timeout=TURN_TIMEOUT_S,
    )
    return _extract_text(response)


# ------------------------------------------------------------------ the gate
async def _escalate(
    answer: str,
    logprobs: Any,
    messages: list[dict],
    *,
    elapsed_s: float,
    wanted_urls: tuple[str, ...],
    query: str,
    force: bool = False,
) -> AsyncIterator[Frame]:
    """Ask a better model when the local one is not confident enough.

    The local model answers first because it is free, private and warm. This
    decides whether its answer is good enough to show. Every step is a
    ``yield``, so the client sees the work as it happens instead of after it.

    ``force=True`` skips the bar entirely and escalates unconditionally. It
    exists for one case: the local tier returned *nothing*. There is no
    confidence to measure in an empty answer, and leaving it to the bar would
    mean the one turn that most needs help is the one that gets an empty
    bubble.

    Yields ``delta`` frames for the replacement answer, and one of
    ``grounded`` / ``escalated`` to explain what happened. Never raises: a gate
    that can fail a conversation is worse than no gate.
    """
    try:
        from core.confidence import assess
        from core.config import config as _cfg
        from core.grounding import admits_ignorance
    except Exception as exc:  # noqa: BLE001
        log.warning("confidence gate unavailable: %s: %s", type(exc).__name__, exc)
        return

    bar = float(getattr(_cfg, "confidence_bar", 0.0) or 0.0)
    if bar <= 0 and not force:
        return

    if force:
        from core.confidence import ConfidenceReading

        # Deliberately not `assess(...)`. assess() iterates its logprobs
        # argument and `None` is not iterable — and there is nothing to
        # measure here anyway, which is precisely why this path exists. The
        # reading is built directly: no confidence claimed, a stated reason,
        # escalate set. Claiming a confidence we did not measure would be the
        # exact lie the confidence bar exists to prevent.
        reading = ConfidenceReading(
            escalate=True,
            confidence=None,
            bar=bar,
            reason="the local model returned no text at all",
            elapsed_s=elapsed_s,
            signals=("silent",),
        )
    else:
        reading = assess(logprobs, text=answer, bar=bar, elapsed_s=elapsed_s)

    # A model saying "I don't have real-time information" is telling the truth,
    # and saying it with total confidence. The bar cannot see that, because
    # certainty about your own ignorance is still certainty — measured on this
    # machine, that sentence cleared the bar in 10s and the user got no lookup.
    # So it is a peer trigger, checked before the big model is considered.
    confessed = admits_ignorance(answer)

    # A reply that was entirely a tool call has already been stripped, so what
    # is left is empty. That is not a low-confidence answer, it is no answer,
    # and the model has just told us it wanted to reach the network. Treating
    # it as "done" would render an empty bubble in the room.
    wanted_a_lookup = bool(wanted_urls)
    if wanted_a_lookup and not answer.strip() and not reading.escalate:
        reading = replace(
            reading,
            escalate=True,
            reason="the model tried to call a tool and had none to call",
            signals=tuple(reading.signals) + ("wanted_tool",),
        )

    if confessed and not reading.escalate:
        reading = replace(
            reading,
            escalate=True,
            reason="the model said it does not know this",
            signals=tuple(reading.signals) + ("admits_ignorance",),
        )

    # No logprobs means unmeasured, not confident, but escalating every call
    # from a provider that omits them would make the local tier unusable.
    #
    # ``force`` is exempt, and it has to be: this reading has confidence=None
    # *by construction* — there was no answer to measure — so the guard below
    # would otherwise send home the one case that most needs a real model. The
    # guard exists to avoid escalating on an *unmeasured but answered* turn,
    # which is a different thing from having nothing at all.
    if not force and (
        not reading.escalate or (reading.confidence is None and not (confessed or wanted_a_lookup))
    ):
        return

    # -- Step 1: look it up, before paying for the big model.
    #
    # Retrieval is a network round trip and no tokens; a 120B completion is
    # both. A pre-retrieval router cannot know whether retrieval will help
    # because that depends on what the index holds, not on the question, so
    # the honest order is cheapest-first and escalate only when a step comes
    # back empty.
    found = None
    try:
        from core.grounding import Grounding, Source, is_searchable, search_for

        # A URL the model itself asked for is better evidence than a search
        # result list: it named the page it wanted. It is also untrusted input
        # written by a model, so the scheme is checked here rather than passed
        # to a fetcher on trust.
        if wanted_urls:
            safe = [u for u in wanted_urls if u.lower().startswith(("http://", "https://"))][:2]
            if safe:
                yield ("activity", {"data": {"label": f"Reading {len(safe)} page{'s' if len(safe) != 1 else ''} the model picked"}})
                found = Grounding(
                    query=query or safe[0],
                    sources=tuple(Source(title=u.rsplit("/", 1)[-1] or u, url=u, snippet="") for u in safe),
                )

        if found is None and is_searchable(query):
            yield ("activity", {"data": {"label": "Searching the web"}})
            found = await asyncio.to_thread(search_for, query)
            if found.useful:
                yield ("activity", {"data": {"label": f"Reading {len(found.sources)} sources"}})
                research_msgs = [
                    {
                        "role": "system",
                        "content": _persona()
                        + "\n\nAnswer using the search results below. If they do not "
                        "contain the answer, say so plainly instead of guessing, and "
                        "cite the source numbers you used.",
                    },
                    *messages,
                    {"role": "user", "content": found.as_context()},
                ]
                # The grounded answer streams too, for the same reason the
                # cascade does: a research answer is the longest thing this
                # endpoint produces, and it is exactly the case where a blob
                # after a long silence is least tolerable.
                #
                # ``replace`` goes out BEFORE the first token, never after: the
                # client clears what the local tier said when the replacement
                # starts arriving. Emitting it afterwards would mean the
                # replacement text was appended to the local answer, then wiped,
                # then re-sent — the user watches an answer appear, vanish and
                # come back.
                yield ("replace", {"stage": "grounded"})
                started = time.monotonic()
                streamed: list[str] = []
                last_beat = time.monotonic()
                try:
                    async for frame in _tier_stream(_cfg.model, research_msgs, cap=ESCALATED_MAX_TOKENS):
                        if frame[0] == "response":
                            break
                        # Keep the heartbeat in step with every tier: this is
                        # the longest single stream in the endpoint.
                        if frame[0] == "keepalive":
                            yield frame
                            continue
                        yield frame
                        # ``_tier_stream`` yields ("delta", text); the route
                        # layer re-wraps it as {"text": ...} on the way out.
                        # Accumulate the bare text — joining the dict payloads
                        # raises "expected str instance, dict found" and takes
                        # the whole grounded answer down with it.
                        streamed.append(frame[1])
                        if time.monotonic() - last_beat > 12:
                            last_beat = time.monotonic()
                            yield ("keepalive", {"elapsed_s": round(time.monotonic() - started, 1)})
                except Exception as exc:  # noqa: BLE001
                    log.warning("grounded answer failed (%s); escalating instead", exc)
                if streamed and "".join(streamed).strip():
                    reading = replace(
                        reading,
                        stage="grounded",
                        model_used=_cfg.model,
                        sources=tuple(s.url for s in found.sources) if found else (),
                    )
                    yield (
                        "grounded",
                        {
                            "reason": reading.reason,
                            "sources": list(reading.sources)[:4],
                            "query": query,
                        },
                    )
                    return
    except Exception as exc:  # noqa: BLE001
        log.warning("grounding failed (%s); escalating instead", exc)

    # -- Step 2: walk the cascade cheapest-useful-first.
    #
    # Groq answers a 120B in about a second; NVIDIA takes 2-6s and sometimes
    # returns "Service temporarily overloaded". Putting the fast free tiers
    # first means the slow one is the exception rather than the default,
    # which is the whole point of having them.
    tiers = _cfg.escalation_tiers()
    yield ("activity", {"data": {"label": "Nothing usable found, asking a bigger model"}})

    for tier in tiers:
        began = time.monotonic()
        log.warning("cascade: trying %s", tier)
        # The client is told to discard whatever the local tier said before the
        # first token of the replacement arrives, so a partial answer from a
        # tier that then fails cannot be left on screen as if it were the
        # answer.
        yield ("replace", {"tier": tier})
        parts: list[str] = []
        try:
            async for frame in _tier_stream(tier, messages, cap=ESCALATED_MAX_TOKENS):
                if frame[0] == "response":
                    break
                if frame[1]:
                    parts.append(frame[1])
                    yield frame
            text = "".join(parts)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "cascade: %s failed after %.1fs (%s: %s)",
                tier, time.monotonic() - began, type(exc).__name__, exc,
            )
            # One retry, blocking, in case it was specifically the streaming
            # surface that provider refused. Cheap next to another tier.
            try:
                text = await _tier_blocking(tier, messages)
            except Exception as inner:  # noqa: BLE001
                log.warning("cascade: %s blocking retry failed (%s)", tier, inner)
                continue

        if not text.strip():
            # An empty answer is a failure here, not a short reply. Reasoning
            # models return "" when max_tokens is under the budget they want, so
            # an empty string means "this tier could not answer", and the next
            # tier is the right response rather than showing a blank bubble.
            log.warning("cascade tier %s returned no text; trying the next", tier)
            continue

        log.warning("cascade: %s answered in %.1fs (%d chars)", tier, time.monotonic() - began, len(text))
        used_tier = tier
        reading = replace(reading, stage="escalated", model_used=tier)
        yield (
            "escalated",
            {
                "reason": reading.reason,
                "confidence": reading.confidence,
                "bar": reading.bar,
                "to": tier,
                "replaced": True,
            },
        )
        yield ("activity", {"data": {"label": "Writing that up"}})
        return

    # Every tier came back empty. Showing the local answer beats an empty
    # bubble, and the frames above already told the client what happened.
    log.warning("the whole cascade produced no text; keeping the local answer")
    yield ("kept_local", {"reason": reading.reason, "confidence": reading.confidence, "bar": reading.bar})


# ------------------------------------------------------------------- the turn
async def run_turn(
    *,
    text: str,
    history: Any = None,
    system: Any = None,
    model: str | None = None,
    max_tokens: int | None = None,
    want_stream: bool = True,
) -> AsyncIterator[Frame]:
    """One conversational turn as ``(event, data)`` frames.

    This is the single implementation both surfaces use: ``/api/v1/chat``
    renders each frame as an SSE line, and the background job handler forwards
    each frame onto the run bus. A frame cannot be lost between them because
    there is nothing to lose — it is a value on the ``yield``.

    Frames, in the order they can appear: ``status``, ``speaking``, ``delta``,
    ``activity``, ``keepalive``, ``replace``, ``grounded``/``escalated``,
    ``error``, ``final``.
    """
    # Local memory first, and cheaply: one SQLite lookup plus one embed, against
    # a 190-second agent turn. It is also the only retrieval that consults what
    # HERMUS has actually been told, so without it every answer comes from the
    # model or the web and never from you.
    try:
        context = await asyncio.to_thread(_retrieve_context, text)
    except Exception:
        context = ""

    messages = build_messages(text, history, system)
    if context:
        # Folded into the system message rather than appended as a user turn:
        # it is background the model should use, not something it said, and a
        # fake user message is a small lie about who is talking.
        messages[0]["content"] = f"{messages[0]['content']}\n\n{context}"

    started = time.monotonic()

    # Progress first, so the panel has something honest to show during the
    # provider's cold start, which on a free tier can be several seconds.
    yield "status", {"phase": "thinking", "messages": len(messages) - 1}

    try:
        from core.models import get_model_gateway
    except Exception as exc:  # noqa: BLE001
        yield "error", {"error": f"no model layer is importable: {type(exc).__name__}: {exc}"}
        return

    local = (model or "").strip() or None
    if not local:
        try:
            from core.config import config as _cfg

            local = _cfg.local_model or _cfg.model
        except Exception:  # noqa: BLE001
            local = None

    # -- tier 1: the local model, streamed.
    streamed: list[str] = []
    response: Any = None
    first_token_s: float | None = None
    used_local = False
    # (asked_for, actually_used) for every non-streamed answer this turn. The
    # final frame reports from this, so a tier swap cannot pass silently.
    model_substitutions: list[tuple[str, str]] = []
    used_tier: str = ""
    # Distinguishes "the stream broke" from "the stream worked and the model
    # had nothing to say". Only the first justifies a blocking retry; the
    # second is the model answering, and re-asking costs a duplicate of the
    # whole generation.
    stream_failed = False
    # Set when the local tier was cut off by the first-token deadline. It has
    # already told the user it is being replaced, so the "nothing to say" line
    # below would be a second, contradictory announcement.
    abandoned_early = False

    if want_stream:
        cap = max_tokens or LOCAL_MAX_TOKENS
        try:
            async for frame in _tier_stream(
                local,
                messages,
                cap=cap,
                first_token_deadline=LOCAL_FIRST_TOKEN_S or None,
            ):
                if frame[0] == "response":
                    response = frame[1]["value"]
                    continue
                if frame[0] == "keepalive":
                    # Forwarded, and this is the whole point: a 4B model on a
                    # laptop GPU can take a minute to reach its first token, and
                    # a client that sees nothing for that minute cannot tell a
                    # slow model from a dead server. It cannot do anything about
                    # the wait, but it can be *in* the wait.
                    yield "keepalive", {"elapsed_s": round(time.monotonic() - started, 1), "stage": "local"}
                    continue
                piece = frame[1]
                if piece:
                    if first_token_s is None:
                        first_token_s = round(time.monotonic() - started, 3)
                        # Tells the panel to move the orb to speaking now,
                        # rather than at the end. That is what makes a slow
                        # model feel alive.
                        yield "speaking", {"first_token_s": first_token_s}
                    streamed.append(piece)
                    yield "delta", {"text": piece}
            used_local = True
        except FirstTokenTimeout as exc:
            # Not a failure to retry. This model is generating and choosing to
            # think instead of answer, which re-asking would reproduce exactly.
            # Escalate and let a tier that can answer do so.
            abandoned_early = True
            log.info("local tier abandoned early: %s", exc)
            yield "activity", {"data": {"label": "The local model is still thinking — asking a bigger one"}}
        except Exception as exc:  # noqa: BLE001
            stream_failed = True
            log.warning("local tier %s could not stream (%s: %s)", local, type(exc).__name__, exc)
            yield "activity", {"data": {"label": f"The local model would not stream ({type(exc).__name__})"}}

    if not want_stream:
        # A caller explicitly asked for one blob. Still no retry-on-empty
        # afterwards; the gate is what handles silence.
        stream_failed = True

    if not streamed and stream_failed:
        # Retry blocking ONLY when the stream itself broke — a provider that
        # refuses to stream at all may still answer a plain request.
        #
        # Not when the stream succeeded and simply produced no text. That case
        # is measured, not hypothetical: the local model spends its entire
        # token budget on reasoning and emits zero content deltas, finishing
        # with `finish_reason: length`. Re-asking the same model with the same
        # prompt blocking does the identical work and gets the identical empty
        # result — it just pays for it twice. Measured on this machine that
        # doubled a 12s local turn into 25s, and on a cold model turned a
        # 12s turn into a 120s one, before escalation even started. That
        # duplicate call was the single largest contributor to the wait.
        yield "activity", {"data": {"label": "Asking the model directly"}}
        try:
            llm_obj = await asyncio.to_thread(get_model_gateway().llm, model=local)
            answer = await asyncio.wait_for(asyncio.to_thread(llm_obj.chat, messages), timeout=TURN_TIMEOUT_S)
            _note_actual_model(answer, local, model_substitutions)
        except Exception as exc:  # noqa: BLE001
            log.exception("chat failed")
            yield "error", {"error": f"{type(exc).__name__}: {exc}"}
            return
        text_out = _extract_text(answer)
        if text_out.strip():
            first_token_s = round(time.monotonic() - started, 3)
            yield "speaking", {"first_token_s": first_token_s}
            yield "delta", {"text": text_out}
            streamed = [text_out]
            response = answer
        else:
            # Still nothing. Fall through to escalation, which is a *different*
            # model and is the only thing that can help now.
            log.info("the local model produced no text; escalating instead of asking again")

    if not streamed and not abandoned_early:
        # The local tier said nothing. Say so and go straight to a model that
        # can answer, rather than rendering an empty bubble.
        #
        # The two reasons are named separately because the user can act on one
        # and not the other. "It thought the whole time" is a budget the
        # operator can raise or a model that can be swapped; "it had nothing to
        # say" is neither.
        _thought = 0
        try:
            _thought = int((getattr(response, "usage", None) or {}).get("reasoning_chars") or 0)
        except Exception:  # noqa: BLE001
            _thought = 0
        if _thought > 200:
            yield "activity", {
                "data": {
                    "label": "The local model spent the whole turn thinking and never answered — asking a bigger one"
                }
            }
        else:
            yield "activity", {"data": {"label": "The local model had nothing to say — asking a bigger one"}}

    # -- gate. A local model with no tools to call will print the call as prose.
    # Strip it before anything reads the answer, and treat the fact that it
    # asked to fetch something as a reason to look: the URL it picked is
    # usually right, and it is the cheapest evidence available.
    joined, wanted = "".join(streamed), ()
    try:
        from core.grounding import strip_tool_json

        joined, wanted = strip_tool_json(joined)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not strip tool json (%s)", exc)

    # A local tier that said *nothing* must still go through the gate. "No
    # answer" is not a low-confidence answer, it is no answer, and a turn that
    # skips the gate here renders an empty bubble. The gate reads the empty
    # text as a reason to ask a model that can actually respond.
    if not joined.strip():
        yield "replace", {"stage": "local_silent"}
        # Accumulated, same as every other path. Yielding deltas without adding
        # them here is how a perfect streamed answer still ends as an empty
        # bubble: `final` is built from this list, not from the wire.
        rescued: list[str] = []
        async for frame in _escalate(
            "",
            None,
            messages,
            elapsed_s=time.monotonic() - started,
            wanted_urls=(),
            query=_last_user_text(messages),
            force=True,
        ):
            if frame[0] == "delta":
                piece = frame[1]
                if piece:
                    rescued.append(piece)
                    if first_token_s is None:
                        first_token_s = round(time.monotonic() - started, 3)
                        yield "speaking", {"first_token_s": first_token_s}
                    yield "delta", {"text": piece}
            elif frame[0] == "replace":
                yield frame
            elif frame[0] == "response":
                continue
            else:
                yield frame
        if rescued:
            streamed = rescued

    if joined.strip():
        # The gate's deltas have to be accumulated into ``streamed`` exactly
        # like the local tier's. Forwarding them to the client without adding
        # them here is the worst possible version of the bug: the user watches
        # a perfect answer stream past, and then gets told the model returned
        # nothing. ``final`` is built from this list, not from the wire.
        #
        # The local answer is kept so it can be put back. A ``replace`` frame
        # has already told the client to clear the bubble, so if every tier
        # then fails, silently returning nothing leaves an empty bubble and no
        # way to tell that was not the model's answer.
        local_answer = joined
        answer_parts: list[str] = []
        async for frame in _escalate(
            joined,
            getattr(response, "logprobs", None),
            messages,
            elapsed_s=time.monotonic() - started,
            wanted_urls=wanted,
            query=_last_user_text(messages),
        ):
            if frame[0] == "delta":
                piece = frame[1]
                if piece:
                    answer_parts.append(piece)
                    if first_token_s is None:
                        first_token_s = round(time.monotonic() - started, 3)
                    # Re-wrapped, and this is not cosmetic. The tier helper
                    # yields a bare string (it is the token), but a client
                    # parses `data` as JSON and reads `.text` — emitting the raw
                    # string here would put `"The"` on the wire where `{"text":
                    # "The"}` is expected, and the panel would render nothing.
                    # Every delta on this stream has one shape.
                    yield "delta", {"text": piece}
            elif frame[0] == "replace":
                # A replacement is starting. Drop what the local tier said so
                # the user is not reading a superseded answer while the better
                # one is still being written.
                streamed = []
                yield frame
            elif frame[0] == "response":
                continue
            else:
                yield frame

        if answer_parts:
            streamed = answer_parts
        elif not "".join(streamed).strip() and local_answer.strip():
            # Every tier came back empty after we told the client to clear the
            # bubble. Put the local answer back rather than leaving a hole.
            log.warning("no replacement answer; restoring the local one")
            streamed = [local_answer]
            yield "replace", {"stage": "restored_local"}
            yield "delta", {"text": local_answer}

    final_text = "".join(streamed)
    if not final_text.strip():
        # The local tier said nothing and the cascade could not rescue it.
        yield "error", {"error": "nothing came back from any model — try again in a moment"}
        return

    # Who actually answered, and whether that is who was asked.
    #
    # A tier can be swapped underneath this function -- an unrecognised model
    # name falls back to the local one -- and the answer still arrives
    # confident and unmarked. Reporting the answering model is not a nicety:
    # without it there is no way to tell a real answer from one produced by a
    # model nobody chose.
    actually_used = model_substitutions[-1][1] if model_substitutions else (used_tier or local)
    substituted = bool(model_substitutions) and actually_used != (used_tier or local)

    yield "final", {
        "ok": True,
        "content": final_text,
        "elapsed_s": round(time.monotonic() - started, 2),
        "first_token_s": first_token_s,
        "truncated": not used_local,
        "model_used": actually_used,
        "model_substituted": substituted,
    }


def _note_actual_model(answer, asked_for: str, sink: list[tuple[str, str]]) -> None:
    """Record which model really produced a non-streamed answer.

    The provider layer may route somewhere other than where it was pointed --
    an unknown model name resolves to the local one -- and that decision was
    previously unrecoverable from the response. The response now carries it.
    """
    got = getattr(answer, "model", "") or ""
    if not got:
        got = asked_for
    sink.append((asked_for, got))


def sse(frame: Frame) -> str:
    """One frame as an SSE line. Named so a client can ignore what it does not
    understand instead of inferring the meaning from the payload shape."""
    event, data = frame
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
