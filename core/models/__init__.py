"""Canonical Model subsystem (Rebuild spec §11).

One :class:`ModelGateway` is the only path for provider discovery, model selection,
health, rate limits, capability negotiation and fallback. It replaces everyone
hand-rolling fallback logic across ``providers``, ``provider_resolver``,
``model_fleet``, ``router2``, ``multi_key``, ``llm``, ``openai_compat`` and
``nollama``.

Model name keywords are only one score feature — never proof of capability.
"""

from .gateway import ModelGateway, get_model_gateway, ModelGatewayError

__all__ = ["ModelGateway", "get_model_gateway", "ModelGatewayError"]
