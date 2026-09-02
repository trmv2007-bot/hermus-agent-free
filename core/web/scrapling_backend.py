"""The one module in Hermus allowed to import Scrapling (spec §3).

Every fetch — static, dynamic, stealth, session-scoped, or captured-XHR — is
performed here, behind the typed errors of :mod:`core.web.models`. Scrapling
responses never escape: the backend returns ``(response, meta)`` pairs that the
normalizer flattens into a :class:`core.web.models.WebResult`, and the
normalizer deliberately drops cookies / request headers.

Scrapling is an OPTIONAL dependency. Nothing in this module may be imported at
Hermus boot time; all scrapling imports are lazy and every failure mode is
mapped to a typed error so the rest of the subsystem never sees a raw
ImportError or curl traceback.

Version target: Scrapling 0.4.x (tested against 0.4.15, BSD-3-Clause).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import capabilities
from .errors import (
    StrategyUnavailableError,
    WebAcquisitionError,
)
from .models import (
    FetchStrategy,
    FailureClass,
)
from .security import WebSecurityPolicy

# curl_cffi network error families → failure classification.
def _classify_network_error(exc: Exception) -> FailureClass:
    text = f"{type(exc).__name__} {exc}".lower()
    if "timed out" in text or "timeout" in text:
        return FailureClass.TIMEOUT
    if "ssl" in text or "certificate" in text or "tls" in text:
        return FailureClass.TLS
    if "resolve" in text or "getaddrinfo" in text or "name or service not known" in text \
            or "nodename nor servname" in text:
        return FailureClass.DNS
    if any(k in text for k in ("connection", "connect", "refused", "reset", "unreachable")):
        return FailureClass.CONNECTION
    return FailureClass.UNKNOWN


def _raise_for_network_error(exc: Exception) -> WebAcquisitionError:
    failure_class = _classify_network_error(exc)
    retryable = failure_class in (FailureClass.TIMEOUT, FailureClass.CONNECTION)
    return WebAcquisitionError(
        f"{type(exc).__name__}: {exc}",
        failure_class=failure_class,
        error_code=f"WEB_{failure_class.value.upper()}",
        retryable=retryable,
    )


def _ensure(strategy: FetchStrategy) -> None:
    """Raise a typed error when the strategy cannot run on this machine."""
    if not capabilities.strategy_ready(strategy.value):
        status = capabilities.probe().get(strategy.value, {})
        raise StrategyUnavailableError(
            f"{strategy.value} acquisition is not usable here: {status.get('detail', 'unknown')}",
            strategy=strategy.value,
        )


@dataclass
class RawFetch:
    """A raw Scrapling response plus bounded metadata — internal to core.web."""

    url: str
    final_url: str = ""
    status: Optional[int] = None
    reason: str = ""
    content_type: str = ""
    size_bytes: int = 0
    duration_ms: int = 0
    history: tuple[str, ...] = ()
    captured_xhr: list[dict[str, Any]] = field(default_factory=list)
    response: Any = None  # scrapling Response — never serialized, never returned


class ScraplingBackend:
    """Thin, honest adapter over Scrapling's fetchers/sessions.

    Concurrency safety: Scrapling's module-level one-off fetchers spin their own
    per-call sessions; named sessions use dedicated Scrapling session objects
    owned by :class:`core.web.sessions.WebSessionManager` and guarded by a lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ probe
    def status(self, strategy: FetchStrategy) -> dict[str, Any]:
        return dict(capabilities.probe().get(strategy.value, {"status": capabilities.NOT_INSTALLED,
                                                              "detail": "unknown strategy"}))

    # ----------------------------------------------------------------- static
    def fetch_static(
        self,
        url: str,
        *,
        policy: WebSecurityPolicy,
        timeout: float = 20.0,
        headers: Optional[dict[str, str]] = None,
        session: Optional[Any] = None,
        stealthy_headers: bool = True,
    ) -> RawFetch:
        """Fast HTTP fetch via Scrapling Fetcher (browser TLS fingerprint)."""
        _ensure(FetchStrategy.STATIC)
        started = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "timeout": timeout,
                "stealthy_headers": stealthy_headers,
                # Belt: Scrapling itself refuses redirect chains that leave for
                # private/internal addresses. Suspenders: gateway re-validates
                # the final URL afterwards.
                "follow_redirects": "safe",
                "max_redirects": max(1, policy.max_redirects),
            }
            if headers:
                kwargs["headers"] = dict(headers)
            if session is not None:
                response = session.get(url, **kwargs)
            else:
                from scrapling.fetchers import Fetcher  # lazy: optional dependency

                response = Fetcher.get(url, **kwargs)
        except StrategyUnavailableError:
            raise
        except Exception as exc:  # curl_cffi network failures
            raise _raise_for_network_error(exc) from exc
        return self._to_raw(url, response, started)

    # ---------------------------------------------------------------- dynamic
    def fetch_dynamic(
        self,
        url: str,
        *,
        policy: WebSecurityPolicy,
        timeout: float = 45.0,
        wait_selector: Optional[str] = None,
        network_idle: bool = True,
        capture_xhr: Optional[str] = None,
        session: Optional[Any] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> RawFetch:
        """JS-rendered fetch via Scrapling DynamicFetcher (Playwright Chromium)."""
        _ensure(FetchStrategy.DYNAMIC)
        started = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "timeout": timeout,
                "network_idle": network_idle,
                "headless": True,
                "retries": 1,
            }
            if wait_selector:
                kwargs["wait_selector"] = wait_selector
            if capture_xhr:
                kwargs["capture_xhr"] = capture_xhr
            if extra_headers:
                kwargs["extra_headers"] = dict(extra_headers)
            if session is not None:
                response = session.fetch(url, **kwargs)
            else:
                from scrapling.fetchers import DynamicFetcher  # lazy: optional dependency

                response = DynamicFetcher.fetch(url, **kwargs)
        except StrategyUnavailableError:
            raise
        except Exception as exc:
            raise _raise_for_network_error(exc) from exc
        raw = self._to_raw(url, response, started)
        raw.captured_xhr = _bundle_captured_xhr(getattr(response, "captured_xhr", None) or [])
        return raw

    # ---------------------------------------------------------------- stealth
    def fetch_stealth(
        self,
        url: str,
        *,
        policy: WebSecurityPolicy,
        timeout: float = 60.0,
        solve_cloudflare: bool = False,
        session: Optional[Any] = None,
    ) -> RawFetch:
        """Hardened anti-bot fetch via Scrapling StealthyFetcher.

        Reaching this path requires the router to have decided stealth is
        *permitted and necessary* (spec §5) — the backend never escalates on
        its own.
        """
        _ensure(FetchStrategy.STEALTH)
        started = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "timeout": timeout,
                "headless": True,
                "retries": 1,
                "solve_cloudflare": bool(solve_cloudflare),
            }
            if session is not None:
                response = session.fetch(url, **kwargs)
            else:
                from scrapling.fetchers import StealthyFetcher  # lazy: optional dependency

                response = StealthyFetcher.fetch(url, **kwargs)
        except StrategyUnavailableError:
            raise
        except Exception as exc:
            raise _raise_for_network_error(exc) from exc
        return self._to_raw(url, response, started)

    # ---------------------------------------------------------------- parsing
    def parse_html(self, html: str, url: str = "") -> Any:
        """Wrap raw HTML in a Scrapling Selector (parser only — no network).

        Used by extraction so selector/adaptive power is available even when a
        page arrived through another route (cache, job pipeline, tests).
        """
        try:
            from scrapling.parser import Selector  # lazy: optional dependency
        except Exception as exc:  # pragma: no cover - covered by capability checks
            raise StrategyUnavailableError(
                f"scrapling parser unavailable: {exc}", strategy="parser"
            ) from exc
        return Selector(content=html, url=url or "about:blank")

    # ---------------------------------------------------------------- helpers
    def _to_raw(self, url: str, response: Any, started: float) -> RawFetch:
        headers = getattr(response, "headers", None) or {}
        content_type = ""
        try:
            content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
        except Exception:
            pass
        body = getattr(response, "body", b"") or b""
        history = tuple(
            getattr(h, "url", "") or "" for h in (getattr(response, "history", None) or [])
        )
        return RawFetch(
            url=url,
            final_url=getattr(response, "url", "") or url,
            status=int(getattr(response, "status", 0) or 0),
            reason=str(getattr(response, "reason", "") or ""),
            content_type=content_type,
            size_bytes=len(body),
            duration_ms=int((time.monotonic() - started) * 1000),
            history=history,
            response=response,
        )


def _bundle_captured_xhr(xhrs: list[Any]) -> list[dict[str, Any]]:
    """Flatten Scrapling captured XHR responses into bounded plain dicts.

    Only JSON-ish bodies are kept (spec §14: structured API data); each body is
    size-capped and still treated as untrusted data downstream.
    """
    out: list[dict[str, Any]] = []
    for xhr in xhrs:
        try:
            body = getattr(xhr, "body", b"") or b""
            if len(body) > 256 * 1024:
                continue
            import json

            try:
                data = json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                continue  # non-JSON XHR (images, blobs) is not worth keeping
            out.append({
                "url": getattr(xhr, "url", ""),
                "status": int(getattr(xhr, "status", 0) or 0),
                "json": data,
            })
        except Exception:
            continue
    return out


backend = ScraplingBackend()
