"""Canonical HTTP response envelope for the gateway.

One stable contract for every JSON reply, so the control room, the CLI and
external clients never have to guess which of ``ok`` / ``success`` / ``error``
/ ``detail`` a given route happens to use today:

    success   {"success": true,  ...<handler payload, untouched>}
    failure   {"success": false, "error": "<code>", "message": "<human text>",
               "code": "<code>", "retryable": <bool>, "details": {...}}

Two layers enforce it:

* :func:`ok` / :func:`fail` — what *new* handlers call directly.
* :func:`normalize_error_body` — what the :class:`gateway.middleware.
  ErrorEnvelopeMiddleware` applies to *every* 4xx/5xx JSON body, including the
  ones raised by FastAPI itself (``HTTPException`` → ``{"detail": ...}``) and
  the ~120 legacy ``JSONResponse(status_code=..., content={"error": ...})``
  sites that predate this module.

Normalization is strictly **additive**: existing keys (``error``, ``detail``)
are preserved, so a client reading ``body["error"]`` keeps working, while new
clients can rely on ``success`` / ``code`` / ``message`` / ``retryable`` being
present on every failure.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse

#: Canonical machine-readable code + human text + retryability per status.
#: ``retryable`` tells a client whether resending the *same* request can
#: succeed (429/503 yes, 400/404 no) — the control room uses it to decide
#: whether to offer a one-click retry.
_STATUS_MAP: dict[int, tuple[str, str, bool]] = {
    400: ("bad_request", "Bad request", False),
    401: ("unauthorized", "Unauthorized", False),
    403: ("forbidden", "Forbidden", False),
    404: ("not_found", "Not found", False),
    405: ("method_not_allowed", "Method not allowed", False),
    408: ("timeout", "Request timed out", True),
    409: ("conflict", "Conflict", False),
    413: ("payload_too_large", "Payload too large", False),
    422: ("validation_error", "Validation error", False),
    429: ("rate_limited", "Too many requests", True),
    499: ("client_closed", "Client closed the request", False),
    500: ("internal", "Internal server error", False),
    502: ("upstream_error", "Upstream error", True),
    503: ("unavailable", "Service unavailable", True),
    504: ("upstream_timeout", "Upstream timed out", True),
}

DEFAULT_CODE = "error"


def status_code_for(status: int) -> tuple[str, str, bool]:
    """Return ``(code, message, retryable)`` for an HTTP status.

    Unknown statuses fall back to a generic code so the envelope shape never
    depends on the status being enumerated above.
    """
    if status in _STATUS_MAP:
        return _STATUS_MAP[status]
    if status >= 500:
        return ("internal", "Internal server error", True)
    return (DEFAULT_CODE, "Request failed", False)


def ok(payload: Mapping[str, Any] | None = None, /, **extra: Any) -> dict[str, Any]:
    """Render a success body: the handler payload with ``success: true`` added.

    Existing payload keys are never renamed or dropped, so a route can migrate
    to the envelope without changing what its callers already read.
    """
    body: dict[str, Any] = {"success": True}
    if payload:
        body.update(dict(payload))
    if extra:
        body.update(extra)
    return body


def error_body(
    *,
    code: str,
    message: str = "",
    status: int = 500,
    retryable: bool | None = None,
    details: Mapping[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the canonical failure body.

    ``code`` is the stable machine string (``not_found``, ``rate_limited``,
    ...). ``message`` is human text and defaults to the status' canned text.
    """
    default_code, default_message, default_retryable = status_code_for(status)
    resolved_code = code or default_code
    resolved_retry = default_retryable if retryable is None else retryable
    body: dict[str, Any] = {
        "success": False,
        "error": resolved_code,
        "code": resolved_code,
        "message": message or default_message,
        "retryable": bool(resolved_retry),
        "details": dict(details or {}),
    }
    if extra:
        body.update(extra)
    return body


def fail(
    code_or_message: str,
    message: str = "",
    *,
    status: int = 400,
    retryable: bool | None = None,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    """Return a canonical failure :class:`JSONResponse`.

    Call form mirrors the legacy sites it replaces::

        return fail("missing field: text")            # 400
        return fail("not_found", "No such job", status=404)
    """
    # One-argument form: the string is the human message, the code comes from
    # the status (or is the message when it is already a stable code).
    if message:
        code = code_or_message
    else:
        code, _msg, _retry = status_code_for(status)
        message = code_or_message if code_or_message else _msg
        if code_or_message and code_or_message.replace("_", "").isalnum() and " " not in code_or_message:
            code = code_or_message
            message = _msg
    return JSONResponse(
        status_code=status,
        content=error_body(
            code=code,
            message=message,
            status=status,
            retryable=retryable,
            details=details,
            **extra,
        ),
        headers=dict(headers or {}),
    )


def normalize_error_body(body: Any, status: int) -> dict[str, Any]:
    """Make an existing error body canonical *without* removing any key.

    Accepts the shapes the gateway already produces (``{"error": "..."}``,
    ``{"error": {...}}``, FastAPI's ``{"detail": ...}``, ``{"success": false,
    "error": ...}``) and returns a dict carrying every canonical key. A
    non-dict body (a bare string, a list) is moved under ``details`` so the
    envelope stays an object.
    """
    code, default_message, default_retryable = status_code_for(status)

    if isinstance(body, Mapping):
        merged: dict[str, Any] = dict(body)
        raw_error = merged.get("error")
        raw_detail = merged.get("detail")
        message = merged.get("message")

        # Prefer, in order: an explicit message, a plain-text error, a
        # plain-text detail. Structured error/detail objects stay in place and
        # are also mirrored into `details` for structured consumers.
        if not isinstance(message, str) or not message:
            for candidate in (raw_error, raw_detail):
                if isinstance(candidate, str) and candidate:
                    message = candidate
                    break
            else:
                message = default_message

        if isinstance(raw_error, str) and raw_error and raw_error not in _STATUS_MAP.values():
            # A legacy string error is the most specific code we have.
            candidate = raw_error.strip().lower().replace(" ", "_").replace("-", "_")[:64]
            if candidate and candidate.replace("_", "").isalnum():
                code = candidate

        details: dict[str, Any] = {}
        existing_details = merged.get("details")
        if isinstance(existing_details, Mapping):
            details.update(existing_details)
        for key, value in (("error", raw_error), ("detail", raw_detail)):
            if value is not None and not isinstance(value, str):
                details.setdefault(key if key != "detail" else "detail", value)

        merged.update(
            {
                "success": False,
                "error": merged.get("error") if isinstance(raw_error, str) and raw_error else code,
                "code": code,
                "message": message,
                "retryable": bool(merged.get("retryable", default_retryable)),
                "details": details,
            }
        )
        return merged

    # Non-object body: keep it under `details` rather than changing its type.
    return {
        "success": False,
        "error": code,
        "code": code,
        "message": default_message,
        "retryable": default_retryable,
        "details": {"body": body},
    }


def dumps(body: Any) -> bytes:
    """Serialize an envelope body the way FastAPI's ``JSONResponse`` does."""
    return json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
    ).encode("utf-8")
