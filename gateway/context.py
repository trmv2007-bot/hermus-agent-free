"""Shared gateway runtime state and helpers.

Extracted from the gateway monolith so the per-concern router modules can
import agent-registry state without circular imports. ``gateway.gateway``
re-exports everything here for backward compatibility.
"""
from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from core.agent import HermusAgent
from core.config import config

AGENTS: dict[str, HermusAgent] = {}


def get_agent_for_user(
    platform: str,
    user_id: str,
    model: str = None,
    mode: str = "agent",
    api_key: str = None,
    base_url: str = None,
) -> HermusAgent:
    # The cache key includes model + base_url so switching the model or the
    # custom URL/API in chat actually takes effect (previously a changed model
    # was silently ignored because the cached agent kept the old one).
    model = model or config.model
    key = f"{platform}:{user_id}:{mode}:{model}:{base_url or ''}"
    if key not in AGENTS:
        AGENTS[key] = HermusAgent(
            model=model,
            session_id=f"{platform}_{user_id}_{os.urandom(4).hex()}",
            mode=mode,
            api_key=api_key,
            base_url=base_url,
        )
    return AGENTS[key]


def _agent_factory(platform: str, user_id: str, model: str = None, mode: str = "agent"):
    return get_agent_for_user(platform, user_id, model=model, mode=mode)


def _token_matches(provided: Optional[str], expected: str) -> bool:
    """Constant-time token comparison to avoid timing side channels."""
    return hmac.compare_digest(str(provided or ""), str(expected))


def _check_gateway_auth(request: Request, x_hermus_token: Optional[str] = None) -> Optional[JSONResponse]:
    """Optional gateway token auth via HERMUS_GATEWAY_TOKEN / config.gateway_api_token."""
    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    if not expected:
        return None  # open (local default)
    provided = x_hermus_token or request.headers.get("X-Hermus-Token") or request.query_params.get("token")
    if not _token_matches(provided, expected):
        return JSONResponse({"error": "Unauthorized - set X-Hermus-Token header"}, status_code=401)
    return None


def _agent_chat(agent, text: str, *, on_event=None, stream: bool = False,
                steer_source=None) -> dict:
    """Call ``agent.chat`` with only the keyword arguments it actually accepts.

    Agents are pluggable here — custom API profiles, older builds, test fakes —
    so the gateway must not assume the streaming/event kwargs exist.
    ``steer_source`` (drained mid-run instructions from the run bus) is passed
    through when the agent supports it.
    """
    import inspect

    kwargs: dict[str, object] = {}
    try:
        params = inspect.signature(agent.chat).parameters
    except (TypeError, ValueError):
        params = {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        params = {name: None for name in ("on_event", "stream", "should_cancel", "steer_source")}
    if on_event is not None and "on_event" in params:
        kwargs["on_event"] = on_event
    if stream and "stream" in params:
        kwargs["stream"] = True
    if on_event is not None and "should_cancel" in params:
        kwargs["should_cancel"] = lambda: False
    if steer_source is not None and "steer_source" in params:
        kwargs["steer_source"] = steer_source
    try:
        return agent.chat(text, **kwargs) if kwargs else agent.chat(text)
    except TypeError:
        return agent.chat(text)
