"""Typed error taxonomy for Hermus.

Every domain error in the codebase derives (directly or indirectly) from
:class:`HermusError`, which carries:

* ``code`` — stable machine-readable string (``"provider_error"``, ...),
* ``status`` — the HTTP status the gateway maps it to,
* ``retryable`` — whether retrying the same operation could succeed,
* ``details`` — JSON-serializable context (never secrets),
* :meth:`HermusError.to_dict` — the canonical error envelope shared by the
  gateway handlers, so API consumers see one shape for all failures::

      {"success": False, "error": code, "message": msg,
       "retryable": bool, "details": {...}}

Guidelines:

* Raise the most specific subclass available; add a new one when a caller
  needs to recover differently (not for every call site).
* ``message`` is human-readable and may be shown to users — keep provider
  keys, tokens and file contents out of it (put redacted hints in
  ``details`` instead).
* Control-flow signals (``CancelledRun``, JSON-RPC ``RpcError`` with numeric
  codes) intentionally stay outside this hierarchy.

This module is stdlib-only (like :mod:`core.log`) so any layer can import it.
"""

from __future__ import annotations

from typing import Any


class HermusError(Exception):
    """Base class for all Hermus domain errors."""

    code: str = "internal"
    status: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        status: int | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        if retryable is not None:
            self.retryable = retryable
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical error envelope (without ``success``)."""
        return {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Generic errors (raised by new code; existing subsystems keep their own
# subclasses below / re-based onto HermusError in their home modules)
# ---------------------------------------------------------------------------
class ConfigError(HermusError):
    """Local configuration is missing or invalid."""

    code = "config_error"
    status = 500


class AuthError(HermusError):
    """Caller is not authenticated (missing/invalid gateway token)."""

    code = "auth_error"
    status = 401


class ForbiddenError(HermusError):
    """Caller is authenticated but not allowed to do this."""

    code = "forbidden"
    status = 403


class NotFoundError(HermusError):
    """The requested run/job/skill/model/... does not exist."""

    code = "not_found"
    status = 404


class InvalidRequestError(HermusError):
    """The request itself is malformed or missing required fields."""

    code = "invalid_request"
    status = 400


class RateLimitError(HermusError):
    """A rate limit was hit (local budget or provider 429)."""

    code = "rate_limited"
    status = 429
    retryable = True


class OperationTimeoutError(HermusError):
    """An operation exceeded its deadline."""

    code = "timeout"
    status = 504
    retryable = True


class ProviderError(HermusError):
    """An upstream model/provider call failed."""

    code = "provider_error"
    status = 502
    retryable = True


class UnavailableError(HermusError):
    """The service cannot take this work right now, but can later.

    Raised while the gateway is draining for shutdown (or before its lifespan
    has finished starting): the process is alive, so liveness stays green, but
    new work is refused so a supervisor can send it elsewhere and in-flight
    jobs can finish. Marked retryable so clients retry after backoff.
    """

    code = "unavailable"
    status = 503
    retryable = True


class ToolError(HermusError):
    """A tool execution failed."""

    code = "tool_error"
    status = 500


class ToolNotFoundError(ToolError):
    """No tool with that name is registered."""

    code = "tool_not_found"
    status = 404


class MissionError(HermusError):
    """A mission failed (see the mission report for stage/reason)."""

    code = "mission_error"
    status = 500


class MissionBlockedError(MissionError):
    """A mission cannot proceed (no backend, missing approval, ...)."""

    code = "mission_blocked"
    status = 409


class VerificationError(HermusError):
    """Evidence/verification rejected a result."""

    code = "verification_error"
    status = 500


__all__ = [
    "HermusError",
    "ConfigError",
    "AuthError",
    "ForbiddenError",
    "NotFoundError",
    "InvalidRequestError",
    "RateLimitError",
    "OperationTimeoutError",
    "ProviderError",
    "UnavailableError",
    "ToolError",
    "ToolNotFoundError",
    "MissionError",
    "MissionBlockedError",
    "VerificationError",
]
