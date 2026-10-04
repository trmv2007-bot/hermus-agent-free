"""Shared gateway runtime state and helpers.

Extracted from the gateway monolith so the per-concern router modules can
import agent-registry state without circular imports. ``gateway.gateway``
re-exports everything here for backward compatibility.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Request

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
    session_id: str = None,
) -> HermusAgent:
    # One browser/conversation session owns one persistent agent instance.
    # Do not resolve a fresh model on every message: that can create a new agent
    # and appear to "reset" the selected model/session.
    requested_model = str(model or "").strip() or None
    requested_session = str(session_id or "").strip() or None

    cache_model = requested_model
    if not cache_model:
        try:
            from core.models import get_model_gateway

            selected_provider, selected_model = get_model_gateway().resolve_model("default")
            cache_model = f"{selected_provider}/{selected_model}" if selected_provider and selected_model else "auto"
        except Exception:
            cache_model = "auto"

    if requested_session:
        key = f"{platform}:{user_id}:{mode}:session:{requested_session}"
    else:
        key = f"{platform}:{user_id}:{mode}:model:{cache_model}:{base_url or ''}"

    agent = AGENTS.get(key)
    if agent is None:
        agent = HermusAgent(
            model=requested_model,
            session_id=requested_session or f"{platform}_{user_id}_{os.urandom(4).hex()}",
            mode=mode,
            api_key=api_key,
            base_url=base_url,
        )
        AGENTS[key] = agent
    elif requested_model and str(getattr(agent, "model_name", "")) != requested_model:
        # A deliberate Chat Settings change should change the model without
        # creating a brand-new conversation identity. Keep the agent/session
        # object and its trajectory, replace only the concrete LLM binding.
        try:
            from core.models import get_model_gateway

            agent.llm = get_model_gateway().llm(model=requested_model, api_key=api_key, base_url=base_url)
            agent.model_name = requested_model
            agent._model_pinned = True
            logger_msg = f"[Gateway] session model changed -> {requested_model}"
            from core.log import get_logger
            get_logger(__name__).info(logger_msg)
        except Exception:
            # If the new model cannot be built, preserve the current working
            # binding rather than silently resetting the session to a fallback.
            pass

    # The presence layer is user-aware for local continuity notes. Keep the
    # identity global to this self-hosted instance, but associate active turns
    # with the session owner so the dashboard can explain who is being served.
    AGENTS[key].user_id = str(user_id or "anonymous")
    AGENTS[key].platform = str(platform or "api")
    return AGENTS[key]


def _agent_factory(platform: str, user_id: str, model: str = None, mode: str = "agent"):
    return get_agent_for_user(platform, user_id, model=model, mode=mode)


def _token_matches(provided: str | None, expected: str) -> bool:
    """Constant-time token comparison to avoid timing side channels."""
    return hmac.compare_digest(str(provided or ""), str(expected))


def _check_gateway_auth(request: Request, x_hermus_token: str | None = None) -> None:
    """Optional gateway token auth via HERMUS_GATEWAY_TOKEN / config.gateway_api_token.

    Used as a FastAPI dependency on the control-plane HTTP routers. When no token is
    configured the gateway stays open (local default); when ``HERMUS_GATEWAY_TOKEN``
    or ``config.gateway_api_token`` is set, every gated HTTP route requires it. A
    missing or wrong token raises ``HTTPException`` so the request is rejected before
    any handler runs. (Note: a dependency that merely *returns* a ``Response`` does
    not short-circuit in FastAPI — it must raise.)

    Must only be applied to routers whose routes are all HTTP. WebSocket routes must
    live on a separate, ungated router (or self-authenticate) because a WS route
    cannot inject ``request: Request``.
    """
    from fastapi import HTTPException

    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    if not expected:
        return None  # open (local default)
    provided = x_hermus_token or request.headers.get("X-Hermus-Token") or request.query_params.get("token")
    if not _token_matches(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized - set X-Hermus-Token header")
    return None


def ws_token_ok(websocket) -> bool:
    """Authenticate a WebSocket upgrade against the optional gateway token.

    WS routes cannot use the ``_check_gateway_auth`` FastAPI dependency (a
    WebSocket handler cannot inject ``Request``), so every WS endpoint calls
    this instead. Single-sourcing it here means the policy — open when no
    token is configured, constant-time match otherwise, token accepted from
    ``?token=`` or ``X-Hermus-Token`` — has exactly one owner.

    Returns ``True`` when no token is configured (local default) or the client
    presented a matching one.
    """
    expected = config.gateway_api_token or os.getenv("HERMUS_GATEWAY_TOKEN")
    if not expected:
        return True
    provided = websocket.query_params.get("token") or websocket.headers.get("X-Hermus-Token")
    return _token_matches(provided, expected)


def _agent_chat(agent, text: str, *, on_event=None, stream: bool = False, steer_source=None) -> dict:
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
