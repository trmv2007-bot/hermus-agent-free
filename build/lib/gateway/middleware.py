"""Gateway ASGI middleware: request context, error envelope, rate limiting.

Three small, dependency-free ASGI middlewares (no ``BaseHTTPMiddleware`` — that
wrapper re-implements the response cycle and is measurably slower):

* :class:`RequestContextMiddleware` — assigns/propagates ``X-Request-ID``, times
  the request, emits one structured access-log line and echoes the id + duration
  back to the caller. This is what makes a dashboard 500 traceable: every log
  record emitted while handling that request carries the same id.
* :class:`ErrorEnvelopeMiddleware` — rewrites *every* 4xx/5xx JSON body into the
  canonical :mod:`gateway.envelope` shape, including FastAPI's own
  ``{"detail": ...}`` responses, so clients get one contract without touching
  the ~120 legacy error sites.
* :class:`RateLimitMiddleware` — opt-in per-client sliding-window limiter that
  answers 429 with ``Retry-After`` before the request ever reaches a handler.

All three are opt-out/opt-in via config and are no-ops on non-HTTP scopes
(WebSocket, lifespan), so streaming endpoints are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from core.log import bind_request_id, get_logger

log = get_logger("gateway.access")
ratelimit_log = get_logger("gateway.ratelimit")

_REQUEST_ID_HEADER = b"x-request-id"
_RESPONSE_TIME_HEADER = b"x-response-time"

#: Longest request id we accept from a client (a header is untrusted input).
_MAX_REQUEST_ID = 64


def _clean_request_id(raw: str) -> str:
    """Sanitize a client-supplied ``X-Request-ID`` (untrusted input)."""
    candidate = (raw or "").strip()
    if not candidate or len(candidate) > _MAX_REQUEST_ID:
        return ""
    if all(ch.isalnum() or ch in "-_.:" for ch in candidate):
        return candidate
    return ""


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def client_ip(scope: dict[str, Any]) -> str:
    """Best-effort client IP.

    ``X-Forwarded-For`` is only honoured when ``HERMUS_TRUST_PROXY=1``: behind
    a reverse proxy the direct peer is the proxy itself, but trusting a
    client-controlled header without an explicit opt-in lets anyone spoof
    their way past a per-IP limit.
    """
    client = scope.get("client")
    direct = client[0] if isinstance(client, (tuple, list)) and client else "unknown"
    if os.environ.get("HERMUS_TRUST_PROXY", "").strip().lower() not in ("1", "true", "yes", "on"):
        return direct
    headers = scope.get("headers") or []
    for name, value in headers:
        if name == b"x-forwarded-for":
            first = value.decode("latin-1").split(",")[0].strip()
            if first:
                return first
    return direct


class RequestContextMiddleware:
    """Correlate every log record and response with one request id."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        incoming = ""
        for name, value in headers:
            if name == _REQUEST_ID_HEADER:
                incoming = _clean_request_id(value.decode("latin-1"))
                break
        rid = incoming or new_request_id()

        status_holder: list[int] = [0]
        started = time.perf_counter()

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder[0] = int(message.get("status", 0))
                extra = [
                    (_REQUEST_ID_HEADER, rid.encode("latin-1")),
                    (
                        _RESPONSE_TIME_HEADER,
                        f"{(time.perf_counter() - started) * 1000:.1f}ms".encode("latin-1"),
                    ),
                ]
                message["headers"] = list(message.get("headers") or []) + extra
            await send(message)

        with bind_request_id(rid):
            duration_ms = 0.0
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                duration_ms = (time.perf_counter() - started) * 1000
            self._log(scope, status_holder[0], duration_ms, rid)

    @staticmethod
    def _log(scope: dict[str, Any], status: int, duration_ms: float, rid: str) -> None:
        try:
            path = scope.get("path", "")
            method = scope.get("method", "")
            record = log.warning if status >= 500 else log.info
            record(
                "%s %s -> %s in %.1fms (%s)",
                method,
                path,
                status or "-",
                duration_ms,
                rid,
            )
        except Exception:  # pragma: no cover - logging must never raise
            pass


class ErrorEnvelopeMiddleware:
    """Normalize 4xx/5xx JSON bodies into the canonical envelope (additively)."""

    #: Path prefixes we never touch (binary/HTML/SSE responses).
    _SKIP_PREFIXES: tuple[str, ...] = ()

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state: dict[str, Any] = {"status": 0, "json": False, "chunks": [], "done": True}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status = int(message.get("status", 0))
                state["status"] = status
                headers = list(message.get("headers") or [])
                if status >= 400:
                    content_type = ""
                    for name, value in headers:
                        if name == b"content-type":
                            content_type = value.decode("latin-1").lower()
                            break
                    state["json"] = content_type.startswith("application/json")
                if status < 400 or not state["json"]:
                    await send(message)
                    return
                # Hold the header block until the body is (possibly) rewritten.
                state["headers"] = headers
                return

            if message["type"] == "http.response.body" and state["json"] and state["status"] >= 400:
                chunk = message.get("body", b"") or b""
                state["chunks"].append(chunk)
                if message.get("more_body"):
                    return  # keep buffering
                body = b"".join(state["chunks"])
                state["chunks"] = []
                rewritten = self._rewrite(body, state["status"])
                if rewritten is None:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": state["status"],
                            "headers": state["headers"],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
                headers = self._replace_content_length(state["headers"], len(rewritten))
                await send(
                    {
                        "type": "http.response.start",
                        "status": state["status"],
                        "headers": headers,
                    }
                )
                await send({"type": "http.response.body", "body": rewritten})
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    def _replace_content_length(headers: list[tuple[bytes, bytes]], length: int) -> list[tuple[bytes, bytes]]:
        out: list[tuple[bytes, bytes]] = []
        for name, value in headers:
            if name != b"content-length":
                out.append((name, value))
        out.append((b"content-length", str(length).encode("latin-1")))
        return out

    @staticmethod
    def _rewrite(body: bytes, status: int) -> bytes | None:
        """Return the normalized body, or ``None`` to pass the original through."""
        if not body:
            return None
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        try:
            from gateway.envelope import normalize_error_body

            normalized = normalize_error_body(parsed, status)
        except Exception:
            return None
        try:
            from gateway.envelope import dumps

            return dumps(normalized)
        except (ValueError, TypeError):
            return None


class _SlidingWindow:
    """Per-key sliding-window counter with bounded memory."""

    def __init__(self, window: float) -> None:
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, now: float) -> tuple[bool, float]:
        """Return ``(allowed, retry_after_seconds)``."""
        hits = self._hits[key]
        cutoff = now - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(0.0, self.window - (now - hits[0]))
            return False, retry_after
        hits.append(now)
        # Bound memory: drop keys that have gone quiet (only when cheap).
        if len(self._hits) > 5000:
            for stale in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                self._hits.pop(stale, None)
        return True, 0.0

    def reset(self) -> None:
        self._hits.clear()


#: Process-wide buckets: a limiter that reset on every app instance would not
#: limit anything, since each request is served by the same long-lived app.
#: Kept at module scope so tests (and a future multi-worker store) can reset it.
_BUCKETS = _SlidingWindow(60.0)


def reset_rate_limits() -> None:
    """Forget every recorded hit (test isolation / operational reset)."""
    _BUCKETS.reset()


class RateLimitMiddleware:
    """Per-client sliding-window limiter (opt-in, 429 + ``Retry-After``).

    Disabled unless ``HERMUS_RATE_LIMIT_PER_MINUTE`` is set to a positive
    number, so existing single-user installs are unaffected.
    """

    DEFAULT_EXEMPT = (
        "/healthz",
        "/readyz",
        "/livez",
        "/api/status",
        "/static",
        "/control",
        "/favicon.ico",
        "/docs",
        "/openapi.json",
    )

    def __init__(self, app: Any) -> None:
        self.app = app
        # Shared, 60-second window (see _BUCKETS). A per-instance window would
        # be a lie: the buckets outlive any single app instance by design.
        self._buckets = _BUCKETS

    # -- configuration ----------------------------------------------------
    @classmethod
    def limit_from_env(cls) -> int:
        raw = os.environ.get("HERMUS_RATE_LIMIT_PER_MINUTE", "").strip()
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def exempt_paths(cls) -> tuple[str, ...]:
        raw = os.environ.get("HERMUS_RATE_LIMIT_EXEMPT", "").strip()
        if raw:
            return tuple(p.strip() for p in raw.split(",") if p.strip())
        return cls.DEFAULT_EXEMPT

    @classmethod
    def enabled(cls) -> bool:
        return cls.limit_from_env() > 0

    # -- ASGI -------------------------------------------------------------
    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_from_env()
        if limit <= 0:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "") or "/"
        if any(path.startswith(prefix) for prefix in self.exempt_paths()):
            await self.app(scope, receive, send)
            return

        key = client_ip(scope)
        token = ""
        for name, value in scope.get("headers") or []:
            if name == b"x-hermus-token":
                token = value.decode("latin-1")
                break
        if token:
            # An authenticated caller gets its own bucket so a shared NAT IP
            # (or the gateway's own dashboard) cannot starve it. Hashed: the
            # bucket key is written to logs and must never carry the token.
            key = "tok:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

        allowed, retry_after = self._buckets.check(key, limit, time.monotonic())
        if allowed:
            await self.app(scope, receive, send)
            return

        ratelimit_log.warning("rate limit exceeded: key=%s path=%s limit=%s/min", key, path, limit)
        body = json.dumps(
            {
                "success": False,
                "error": "rate_limited",
                "code": "rate_limited",
                "message": f"Too many requests (limit {limit}/minute)",
                "retryable": True,
                "details": {"limit_per_minute": limit, "retry_after": round(retry_after, 2)},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                    (b"retry-after", str(max(1, int(retry_after + 0.999))).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
