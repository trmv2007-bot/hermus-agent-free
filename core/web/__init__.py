"""Hermus web acquisition subsystem — the ONE canonical web boundary.

Agent → ToolGateway → **WebGateway** → StrategyRouter → Scrapling backend →
sanitization → WebResult.

Contract (enforced by tests/test_architecture_gates.py):

* ``scrapling`` may only be imported inside this package (in practice only by
  ``scrapling_backend.py`` / ``sessions.py`` / ``extractor.py`` / capability
  probes) — never by tools, agent code, or gateway routes.
* Every production web action flows through :class:`WebGateway`
  (:func:`get_web_gateway`), which owns security, routing, normalization,
  caching, telemetry and session isolation.
* Results cross the boundary exclusively as :class:`WebResult` /
  plain dicts — raw Scrapling objects never escape this package.
* Webpage content is always UNTRUSTED DATA; see ``core/web/sanitize.py``.

Scrapling is an optional dependency: the package imports cleanly without it
and every operation degrades to a typed, honest failure result.
"""
from __future__ import annotations

from .errors import (
    AllStrategiesFailedError,
    SecurityBlockedError,
    StrategyUnavailableError,
    WebAcquisitionError,
)
from .gateway import WebGateway, get_web_gateway, set_web_gateway
from .models import (
    CrawlProgress,
    CrawlStatus,
    ExtractionResult,
    FailureClass,
    FetchStrategy,
    LinkInfo,
    StrategyAttempt,
    WebResult,
)

__all__ = [
    "AllStrategiesFailedError",
    "CrawlProgress",
    "CrawlStatus",
    "ExtractionResult",
    "FailureClass",
    "FetchStrategy",
    "LinkInfo",
    "SecurityBlockedError",
    "StrategyAttempt",
    "StrategyUnavailableError",
    "WebAcquisitionError",
    "WebGateway",
    "WebResult",
    "get_web_gateway",
    "set_web_gateway",
]
