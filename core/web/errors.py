"""Typed exceptions for the web acquisition subsystem.

Imported across ``core.web``; the classes themselves live here (not in
``models``) so the security/strategy layers can raise them without importing
the heavier result models.
"""
from __future__ import annotations

from typing import Optional

from .models import FailureClass


class WebAcquisitionError(Exception):
    """Typed failure raised inside the web subsystem.

    Carries a machine-readable ``failure_class`` so the router and the retry
    policy can act without string-matching error messages.
    """

    def __init__(self, message: str, *, failure_class: FailureClass = FailureClass.UNKNOWN,
                 error_code: str = "WEB_ERROR", retryable: bool = False,
                 status_code: Optional[int] = None):
        super().__init__(message)
        self.failure_class = failure_class
        self.error_code = error_code
        self.retryable = retryable
        self.status_code = status_code


class SecurityBlockedError(WebAcquisitionError):
    """A request was refused by Hermus security policy — never retry it."""

    def __init__(self, message: str, *, error_code: str = "WEB_SECURITY_BLOCKED"):
        super().__init__(message, failure_class=FailureClass.SECURITY_BLOCKED,
                         error_code=error_code, retryable=False)


class ResponseTooLargeError(WebAcquisitionError):
    """A response exceeded the configured size cap — never retry it.

    Distinct from a security refusal: the target was allowed, but the body is
    larger than ``web_max_response_bytes``. Carries ``SIZE_LIMIT`` so the router
    aborts the plan (a bigger fetch never helps) instead of escalating.
    """

    def __init__(self, message: str, *, error_code: str = "WEB_RESPONSE_TOO_LARGE"):
        super().__init__(message, failure_class=FailureClass.SIZE_LIMIT,
                         error_code=error_code, retryable=False)


class StrategyUnavailableError(WebAcquisitionError):
    """The requested strategy cannot run here (dependency missing / disabled)."""

    def __init__(self, message: str, *, strategy: str = ""):
        super().__init__(message, failure_class=FailureClass.DEPENDENCY_MISSING,
                         error_code="WEB_STRATEGY_UNAVAILABLE", retryable=False)
        self.strategy = strategy


class AllStrategiesFailedError(WebAcquisitionError):
    """Every allowed strategy was attempted and none produced usable content."""

    def __init__(self, message: str):
        super().__init__(message, failure_class=FailureClass.UNKNOWN,
                         error_code="WEB_ALL_STRATEGIES_FAILED", retryable=True)


__all__ = [
    "WebAcquisitionError",
    "SecurityBlockedError",
    "ResponseTooLargeError",
    "StrategyUnavailableError",
    "AllStrategiesFailedError",
]
