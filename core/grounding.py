"""When HERMUS does not know, it goes and looks. Then, only then, it escalates.

Why this is ordered the way it is
---------------------------------
The obvious design is "low confidence -> call the bigger model". That is the
wrong order, and the research is fairly clear about why:

* A pre-retrieval router cannot know whether retrieval will help, because that
  depends on what the index contains, not on the query. Arxiv 2605.27220 calls
  this the coverage illusion and shows pre-retrieval routing failing outright;
  its fix is a cascade that runs the cheap steps first and escalates only when
  a step returns nothing.
* Retrieval costs a network round trip and no tokens. A 120B completion costs
  both. Running the cheap step first is strictly better whenever it works.

So the order is: answer locally, and if that is not confident, *search*, answer
again with the evidence, and escalate to the big model only if the search left
us no better off.

Two more findings worth keeping
-------------------------------
* Adaptive RAG (arXiv 2603.06604) returns the grounded answer only when it is
  more confident than the ungrounded one. Retrieval does not reliably help, so
  comparing the two is the honest thing to do rather than trusting whatever
  came back.
* The same paper reports 58% of retrievals recovering 95% of the maximum
  achievable accuracy gain, and that confidence is poorly calibrated as an
  absolute probability (AUROC 0.628 for predicting retrieval benefit). It is a
  useful trigger, not a probability. Nothing here treats it as one.

It also notes that RL-tuned models lose confidence reliability while SFT keeps
it. So do not fine-tune this signal away.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# Questions whose answer is stable and cheap to hold in weights. Searching for
# these spends a network round trip to learn nothing.
NOT_WORTH_SEARCHING = re.compile(
    r"^\s*(hi|hey|hello|yo|thanks|thank you|ok|okay|cool|nice|good|"
    r"what is \d+\s*[+\-*/]\s*\d+|"
    r"\d+\s*[+\-*/]\s*\d+)",
    re.I,
)

# Signals that the answer lives outside the weights. Deliberately broad: a
# wasted search is a couple of seconds, a missed search is a wrong answer.
WORTH_SEARCHING = re.compile(
    r"\b(who|when|where|which|whose|how much|how many|latest|current|today|"
    r"now|recent|news|price|cost|version|release|weather|score|schedule|"
    r"what is|what are|who is|where is|define|meaning of|does .* support)\b",
    re.I,
)

# Things that are about *this* machine. Searching the web for them is always
# wrong; the answer is one call away locally.
LOCAL_ABOUTTIONS = re.compile(
    r"\b(this (pc|computer|machine)|my (pc|computer|files|memory|screen)|"
    r"system|memory|cpu|gpu|ram|disk|port|process|running|here|room|"
    r"what can you do|who are you|your name)\b",
    re.I,
)

# Questions the clock answers exactly, for free, with no network. Anchored on
# the topic noun so "current" elsewhere still searches normally.
CLOCK_ANSWERS = re.compile(
    r"\b(what|whats|what's)\b[^?]{0,24}\b(time|date|day|month|year|week)\b"
    r"|\b(what time is it|what's the time|what is the time"
    r"|what date is it|what's the date|today'?s date)\b",
    re.I,
)


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Grounding:
    """What a search turned up, and whether it was worth anything."""

    query: str
    sources: tuple[Source, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def useful(self) -> bool:
        """Whether this retrieval can actually change the answer.

        A search that errored, returned nothing, or returned the same page
        twice is not evidence, and treating it as evidence is how a model ends
        up confidently citing nothing.
        """
        if self.error or not self.sources:
            return False
        real = [s for s in self.sources if s.url and "example.com" not in s.url]
        if not real:
            return False
        return len({s.url for s in real}) >= 2

    def as_context(self, limit: int = 4) -> str:
        """The evidence block, with the date so the model can judge staleness."""
        if not self.sources:
            return ""
        lines = [f"Search results for: {self.query}", ""]
        for i, s in enumerate(self.sources[:limit], 1):
            lines.append(f"[{i}] {s.title}\n    {s.url}\n    {s.snippet[:400]}")
        return "\n".join(lines)


# Admitting ignorance, delivered confidently.
#
# This exists because the confidence bar cannot catch the most common case of
# not knowing. Measured: asked for the current price of an RTX 5090 in India,
# the local model replied "I don't have real-time pricing information" in
# 10s - which is the correct answer, stated with high token confidence, so the
# gate waved it through and the user got no lookup. Certainty about your own
# ignorance is still certainty.
#
# So this is a third trigger, independent of confidence, and it fires before
# the big model is considered. Detecting "I don't know" is a string check
# costing nothing, while a retrieval call that was not needed costs a network
# round trip.
ADMITS_IGNORANCE = re.compile(
    r"\b("
    r"i (don'?t|do not|cannot|can'?t|am unable to|'m unable to)\s+"
    r"(have|know|have access to|be able to|find|get|see|check|browse|look up|"
    r"retrieve|fetch|determine|tell you|provide|confirm|access|reach|browse)"
    r"|i (don'?t|do not) (have any |any )?(real[- ]?time|live|current|recent|up[- ]?to[- ]date|"
    r"access to (the )?(internet|web)|a way to)"
    r"|(no|not) (real[- ]?time|live) (access|data|information|information available)"
    r"|(my|our) (training|knowledge) (data )?(cut[- ]?off|ends?|goes) (in|at|on)? ?\w*"
    r"|beyond (my|what i know)"
    r"|that (requires|needs) (a )?web (search|lookup)|"
    r"i can'?t verify|i (have no|haven'?t got) (access|record|way)"
    r")",
    re.I,
)


# Long answers are scanned only for the confession, not for the topic.
MAX_IGNORANCE_SCAN_CHARS = 1200


# A local model asked to look something up, with no tools available.
#
# Measured: asked for the current price of an RTX 5090, spark-x2.5-4b replied
# with a literal tool call as prose - {"tool": "fetch_url", "args": {...}} -
# because the chat route hands it no tools to call. Rendered raw in the room it
# is the most obviously broken output imaginable, and worse than useless because
# the URL it chose is usually right.
#
# So the intent is recovered instead of the JSON being shown. A URL the model
# asked for is evidence it wanted that page; fetching it is the cheapest
# possible way to find out.
# Keys that name the action a model is trying to take. Regex matching on these
# kept missing shapes: the same model emitted {"tool": "fetch_url", ...} on one
# turn and {"action": "web_search", ...} on the next, and a pattern that covers
# one does not cover the other. So the block is parsed as JSON and the keys are
# checked as keys, which is what they are.
_ACTION_KEYS = {"tool", "name", "function", "action", "command", "method", "type", "capability"}
_ARG_KEYS = {
    "args", "arguments", "parameters", "params", "input", "url", "query",
    "q", "text", "search", "path", "target", "cmd",
}
# Wrapper keys a model puts around the actual call.
_CONTAINER_KEYS = {
    "invocation", "tool_call", "toolcall", "function_call", "functioncall",
    "request", "call", "payload", "params", "arguments", "args", "body",
}
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)\]]+")


def _as_tool_call(text: str) -> dict | None:
    """Return the dict if this text is a tool call the model printed, else None.

    Tolerates a ```json fence, which models add unprompted, and insists the
    object be the whole reply: a JSON blob quoted inside a sentence is
    something the model is discussing, not something it is asking to do.
    """
    candidate = normalize_text(text).strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", candidate).strip()
    if not candidate.startswith("{"):
        return None
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    if _is_call(parsed):
        return parsed

    # Models wrap the call in a container. Measured shapes from this model
    # alone: {"tool": ...}, {"action": ...}, and {"invocation": {"tool":
    # "http_request", ...}}. Stopping at the top level misses the last one, so
    # known container keys are unwrapped and re-checked.
    for key in _CONTAINER_KEYS:
        inner = parsed.get(key)
        if isinstance(inner, dict) and _is_call(inner):
            return inner
    return None


def _is_call(obj: dict) -> bool:
    keys = {str(k).lower() for k in obj}
    return bool(keys & _ACTION_KEYS) and bool(keys & _ARG_KEYS)


def _collect(value, urls: list[str]) -> None:
    """Pull URLs out of anywhere inside the call's arguments."""
    if isinstance(value, str):
        urls.extend(_URL_IN_TEXT.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            _collect(v, urls)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect(v, urls)


def strip_tool_json(text: str) -> tuple[str, tuple[str, ...]]:
    """Pull out a tool call the model printed as prose.

    Returns (clean_text, urls). The clean text keeps ordinary prose, so a reply
    that both answered and asked to fetch something does not lose its answer.

    The URLs come back separately rather than being handed to a fetcher: the
    model picks which page to read, but a model-suggested URL is untrusted
    input, and whatever fetches it still has to check the scheme.
    """
    if not text:
        return "", ()

    call = _as_tool_call(text)
    if call is not None:
        urls: list[str] = []
        _collect(call, urls)
        return "", tuple(dict.fromkeys(urls))

    # The whole reply was not a call, so keep the prose and drop any embedded
    # call, but only when the surrounding text is worth keeping.
    stripped = text.strip()
    if len(stripped) > 2 and stripped[0] not in "{":
        urls = []
        _collect(stripped, urls)
        if urls and _ACTION_KEYS & set(re.findall(r'"(\w+)"\s*:', stripped)):
            return text, ()

    return text.strip(), ()


# Models emit typography, not ASCII. Measured: the exact sentence that should
# have triggered a lookup came back as "I don't have real-time pricing data"
# with a curly apostrophe and a non-breaking hyphen, so a pattern written
# against ASCII matched nothing and the gate waved it through.
_TYPOGRAPHY = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",
}


def normalize_text(text: str) -> str:
    """Fold the characters a model actually emits onto the ones patterns use.

    Without this, "don't" and "don\u2019t" are different strings and every
    pattern that looks for the first silently misses the second.
    """
    for fancy, plain in _TYPOGRAPHY.items():
        text = text.replace(fancy, plain)
    return text


def admits_ignorance(answer: str) -> bool:
    """Whether the model just said it cannot know this.

    Deliberately a narrow set of phrasings. A loose match here sends the
    conversation to a search on every turn that happens to contain "I don't
    know" in passing, and a wasted search is visible to the user as a lag.
    """
    if not answer:
        return False
    answer = normalize_text(answer)
    if len(answer) > MAX_IGNORANCE_SCAN_CHARS:
        # A long answer that mentions not knowing is usually discussing the
        # topic rather than confessing to it. 1200 chars is about where that
        # flips: under it, "I don't know" is the point of the reply; over it,
        # it is normally a passing clause inside an essay.
        return False
    return bool(ADMITS_IGNORANCE.search(answer))


def is_searchable(question: str) -> bool:
    """Whether this turn is worth a network round trip.

    Four checks, cheapest first: is it arithmetic or a greeting, is it about
    this machine, can the clock answer it, and only then does it have to look
    like a question with an answer that moves.
    """
    q = normalize_text(question or "").strip()
    if not q:
        return False
    if NOT_WORTH_SEARCHING.match(q):
        return False
    if LOCAL_ABOUTTIONS.search(q):
        return False
    if CLOCK_ANSWERS.match(q):
        # "What time is it" matched WORTH_SEARCHING on the word "current" and
        # cost a 20s DuckDuckGo round trip to learn what now_context() already
        # knows exactly. The clock is local, exact and free; the web is slow
        # and can be wrong. Route on the topic, not on the freshness adjective.
        return False
    return bool(WORTH_SEARCHING.search(q))


def search_for(question: str, max_results: int = 4, timeout_s: float = 25.0) -> Grounding:
    """Look it up. Never raises.

    A failed search is a normal outcome, not an error worth propagating: the
    caller escalates instead, and a search that throws would take the
    conversation down with it.
    """
    query = normalize_text(question or "").strip()[:300]
    if not query:
        return Grounding(query=query, error="empty query")
    try:
        from tools.web_search import web_search

        raw = web_search(query, max_results=max_results)
    except Exception as exc:  # noqa: BLE001
        return Grounding(query=query, error=f"{type(exc).__name__}: {exc}")

    sources: list[Source] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("href") or item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("body") or item.get("snippet") or "").strip()
        if url and (title or snippet):
            sources.append(Source(title=title or url, url=url, snippet=snippet))
    return Grounding(query=query, sources=tuple(sources))


def now_context(tz_offset_hours: float = 5.5, name: str = "IST") -> str:
    """What time it is, for the system prompt.

    Cheap, exact, and the one piece of world state a model genuinely cannot
    know: a training cutoff means it has no idea what day it is. Without this
    it will confidently answer time-sensitive questions from a stale prior.
    """
    tz = timezone(timedelta(hours=tz_offset_hours))
    now = datetime.now(tz)
    return (
        f"It is {now.strftime('%A %d %B %Y, %H:%M')} {name} "
        f"(your training data has a cutoff, so never guess today's date)."
    )
