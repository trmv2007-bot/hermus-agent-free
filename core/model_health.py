"""Whether a free model can actually answer right now — measured, not assumed.

The catalog can say what a model *is* (size, family, context, tool calling) and
what order it belongs in. It cannot say what a particular key can reach this
minute: a free tier rate-limits per model, retires names without notice, and
serves different models to different plans. An authored "best model" that the
key answers with HTTP 429 is a ranking nobody measured, which is exactly the
failure this module exists to prevent.

So availability is probed: one request per candidate, in catalog order, stopping
at the first model that answers. The probe is a single-token completion — the
cheapest request a provider bills — and its outcome is cached, because a
resolution runs on every page load and must not spend a request each time.

The states are deliberately separate, because they mean different things to the
person and to the picker:

``ok``       the model answered this key
``busy``     HTTP 429 / capacity: it exists and this key has it, but not now.
             Never treated as a retirement, and never a reason to migrate away
``gone``     HTTP 404, or a 400 naming the model: this key cannot call that name
``denied``   HTTP 401/403: the key itself is unusable, so no candidate is tried
``unknown``  no answer (offline, timeout, 5xx): not evidence of anything

A provider that denies the key is blocked for longer than a busy model is, so a
wrong key costs one request rather than one per candidate every ten minutes.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import model_catalog as catalog

OK = "ok"
BUSY = "busy"
GONE = "gone"
DENIED = "denied"
UNKNOWN = "unknown"

#: how long each outcome is believed, in seconds
TTL = {OK: 600.0, BUSY: 900.0, GONE: 3_600.0, DENIED: 1_800.0, UNKNOWN: 120.0}

#: one token, no thinking, no tool schema: the cheapest request a provider bills
PROBE_TIMEOUT = 10.0

_CHAT = {
    "mistral": "https://api.mistral.ai/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "cerebras": "https://api.cerebras.ai/v1/chat/completions",
    "together": "https://api.together.xyz/v1/chat/completions",
}
_LIST = {
    "mistral": "https://api.mistral.ai/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
}
_GEMINI_LIST = "https://generativelanguage.googleapis.com/v1beta/models"


@dataclass(frozen=True)
class ModelState:
    """One candidate's measured availability, with the reason it holds."""

    provider: str
    name: str
    state: str
    detail: str = ""
    checked_at: float = 0.0

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.name}"

    @property
    def usable(self) -> bool:
        """Can this candidate be offered? ``unknown`` is offered, labelled."""
        return self.state in (OK, UNKNOWN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "state": self.state,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


#: tests replace this to stay offline; production sends one real request
transport: Callable[[urllib.request.Request, float], tuple[int, str]] = None  # type: ignore[assignment]

_cache: dict[tuple[str, str], ModelState] = {}
_denied_until: dict[str, float] = {}


def probe_enabled() -> bool:
    return os.environ.get("HERMUS_MODEL_PROBE", "1") not in ("0", "false", "False")


def reset_cache() -> None:
    """Forget every measurement. Used by tests and by a forced re-resolution."""
    _cache.clear()
    _denied_until.clear()


def _send(request: urllib.request.Request, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - fixed provider URLs
            return int(getattr(resp, "status", 200) or 200), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _error_text(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:160]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "")[:160]
        if isinstance(error, str):
            return error[:160]
        if data.get("message"):
            return str(data["message"])[:160]
    return body.strip()[:160]


def _classify(status: int, body: str) -> tuple[str, str]:
    text = _error_text(body)
    if status in (200, 201):
        return OK, ""
    if status == 429:
        return BUSY, f"HTTP 429 — capacity, not retirement ({text or 'rate limited'})"[:200]
    if status in (401, 403):
        return DENIED, f"HTTP {status} — the key cannot be used ({text})"[:200]
    if status == 404:
        return GONE, f"HTTP 404 — this key cannot call the name ({text})"[:200]
    if status == 400 and ("model" in text.lower() or "not found" in text.lower()):
        return GONE, f"HTTP 400 — provider does not serve it to this key ({text})"[:200]
    if status >= 500:
        return UNKNOWN, f"HTTP {status} — provider error, not evidence about the model ({text})"[:200]
    return UNKNOWN, f"HTTP {status} ({text})"[:200]


def _chat_request(provider: str, key: str, name: str) -> urllib.request.Request | None:
    url = _CHAT.get(provider)
    if url is None:
        return None
    payload = {
        "model": name,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "temperature": 0,
    }
    return urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )


def _listing_request(provider: str, key: str) -> urllib.request.Request | None:
    url = _LIST.get(provider)
    if url is None and provider == "gemini":
        url = _GEMINI_LIST
    if url is None:
        return None
    headers = {"Content-Type": "application/json"}
    if provider == "gemini":
        url = f"{url}?key={key}"
    else:
        headers["Authorization"] = f"Bearer {key}"
    return urllib.request.Request(url, headers=headers, method="GET")


def _gemini_listed(key: str, name: str) -> ModelState:
    """Gemini has no OpenAI-compatible chat path; its own listing is the probe."""
    state = _probe_listing("gemini", key, name)
    return state


def _probe_listing(provider: str, key: str, name: str) -> ModelState:
    request = _listing_request(provider, key)
    if request is None:
        return ModelState(provider, name, UNKNOWN, "no probe path for this provider", time.time())
    send = transport or _send
    status, body = send(request, PROBE_TIMEOUT)
    if status in (200, 201):
        try:
            entries = json.loads(body)
        except ValueError:
            return ModelState(provider, name, UNKNOWN, "the listing was not JSON", time.time())
        listed = [str(entry.get("id") or "") for entry in (entries.get("models") or entries.get("data") or [])]
        listed = [item.split("/", 1)[-1] for item in listed]
        if name in listed or any(item.split("/", 1)[-1] == name.split("/", 1)[-1] for item in listed):
            return ModelState(provider, name, OK, "listed for this key", time.time())
        return ModelState(provider, name, GONE, "not in this key's model listing", time.time())
    state, detail = _classify(status, body)
    return ModelState(provider, name, state, detail, time.time())


def probe(provider: str, key: str, name: str, *, force: bool = False) -> ModelState:
    """One candidate's availability, cached for its outcome's own TTL."""
    if not probe_enabled() or not key:
        return ModelState(provider, name, UNKNOWN, "not probed", time.time())
    blocked = _denied_until.get(provider)
    if blocked and blocked > time.time():
        return ModelState(provider, name, DENIED, "the key was refused a moment ago", time.time())

    cached = _cache.get((provider, name))
    if cached is not None and not force and time.time() - cached.checked_at < TTL.get(cached.state, 120.0):
        return cached

    if provider == "gemini":
        result = _gemini_listed(key, name)
    else:
        request = _chat_request(provider, key, name)
        if request is None:
            result = ModelState(provider, name, UNKNOWN, "no probe path for this provider", time.time())
        else:
            send = transport or _send
            status, body = send(request, PROBE_TIMEOUT)
            state, detail = _classify(status, body)
            result = ModelState(provider, name, state, detail, time.time())

    if result.state == DENIED:
        _denied_until[provider] = time.time() + TTL[DENIED]
    _cache[(provider, name)] = result
    return result


def rank(provider: str, key: str, *, force: bool = False) -> list[ModelState]:
    """Every catalog candidate for a provider, with its measured state.

    Catalog order is kept, so the caller sees which model would have won and what
    actually happens when it is tried — that difference is the thing worth
    recording.
    """
    return [probe(provider, key, entry.name, force=force) for entry in catalog.ranked(provider)]


def usable_states(provider: str, key: str, *, force: bool = False) -> list[ModelState]:
    return [state for state in rank(provider, key, force=force) if state.usable]
